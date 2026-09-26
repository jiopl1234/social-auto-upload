"""Bounded preflight through the public task service and real account storage."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import pytest

from tests.task_fixtures import make_manifest, make_service, readback_metadata


class PreparedResource:
    def __init__(self):
        self.context = None
        self.closed = 0
        self.submitted = 0

    async def prepare(self, context):
        self.context = context
        context.resources_started()
        return readback_metadata(context.manifest)

    async def submit(self, context):
        self.submitted += 1
        return None

    async def reconcile(self, context):
        return []

    async def close(self):
        self.closed += 1
        if self.context is not None:
            self.context.resources_closed()


class ReadyTarget:
    async def prepare(self, context):
        return {"title": context.manifest["title"], "body": context.manifest["body"]}

    async def close(self):
        pass


class MediaChangesDuringPrepare(PreparedResource):
    async def prepare(self, context):
        metadata = await super().prepare(context)
        path = Path(context.manifest["media"][0]["path"])
        path.write_bytes(path.read_bytes() + b"changed")
        return metadata


def test_async_preflight_runs_with_database_and_media_paths_containing_spaces(tmp_path):
    workspace = tmp_path / "space in workspace"
    workspace.mkdir()
    service = make_service(workspace)
    job = service.enqueue(make_manifest(workspace))
    result = asyncio.run(service.run_once(adapters={"douyin": ReadyTarget()}, job_id=job["job_id"]))
    assert result is not None
    assert result["status"] == "READY"
    assert result["error"] is None


def test_postprepare_media_change_is_rejected_before_submit(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    adapter = MediaChangesDuringPrepare()
    result = asyncio.run(service.run_once(adapters={"douyin": adapter}, job_id=job["job_id"]))
    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["field"] == "media[0]"
    assert adapter.closed == 1
    assert adapter.submitted == 0


def test_preflight_rechecks_managed_binding_in_child(tmp_path):
    from orchestrator.accounts import managed_session_path

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    managed_session_path(service.runtime_dir, "douyin", "creator").unlink()
    result = asyncio.run(service.run_once(adapters={"douyin": ReadyTarget()}, job_id=job["job_id"]))
    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["field"] == "account"


@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_postprepare_preflight_keeps_event_loop_live_and_cleans_resources(tmp_path, monkeypatch, cancel):
    from orchestrator import preflight_process

    service = make_service(tmp_path)
    stalled_job = service.enqueue(make_manifest(tmp_path, key="stalled"))
    next_job = service.enqueue(make_manifest(tmp_path, key="next"))
    adapter = PreparedResource()
    actual_popen = subprocess.Popen
    starts = 0
    stalled_child = None
    second_started = asyncio.Event()

    def start_child(command, **kwargs):
        nonlocal starts, stalled_child
        starts += 1
        if starts == 2:
            stalled_child = actual_popen(
                [command[0], "-c", "import time; time.sleep(30)"], **kwargs,
            )
            second_started.set()
            return stalled_child
        return actual_popen(command, **kwargs)

    monkeypatch.setattr(preflight_process.subprocess, "Popen", start_child)

    async def run_stalled_job():
        limits = {"total": 5.0, "preflight": 3.0 if cancel else 1.5, "cleanup": 1.0}
        running = asyncio.create_task(service.run_once(
            adapters={"douyin": adapter}, job_id=stalled_job["job_id"], limits=limits,
        ))
        await asyncio.wait_for(second_started.wait(), timeout=3)
        start = time.monotonic()
        await asyncio.wait_for(asyncio.sleep(0.03), timeout=0.25)
        assert time.monotonic() - start < 0.25
        if cancel:
            running.cancel()
        return await asyncio.wait_for(running, timeout=3)

    result = asyncio.run(run_stalled_job())
    assert result is not None
    assert result["status"] == ("CANCELLED" if cancel else "FAILED_PRE_SUBMIT")
    assert result["error"]["code"] == ("CANCELLED" if cancel else "TIMEOUT")
    assert adapter.closed == 1
    assert adapter.submitted == 0
    assert stalled_child is not None and stalled_child.poll() is not None
    # The next same-account task can acquire its lease after verified shutdown.
    next_result = asyncio.run(service.run_once(
        adapters={"douyin": ReadyTarget()}, job_id=next_job["job_id"],
    ))
    assert next_result is not None and next_result["status"] == "READY"


def test_watched_child_exits_when_its_exact_parent_is_hard_killed(tmp_path):
    from orchestrator.locking import process_state
    from orchestrator.preflight_process import _base_python

    child_code = (
        "import os,sys,threading,time; "
        "from orchestrator.preflight_process import _watch_parent; "
        "threading.Thread(target=_watch_parent, "
        "args=(int(sys.argv[1]),sys.argv[2]), daemon=True).start(); "
        "print('ready',flush=True); time.sleep(8)"
    )
    parent_code = (
        "import json,os,pathlib,subprocess,sys,time; "
        "from orchestrator.locking import process_identity; "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[1],"
        "str(os.getpid()),process_identity(os.getpid())],"
        "stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True); "
        "assert child.stdout.readline()=='ready\\n'; "
        "target=pathlib.Path(sys.argv[2]); temporary=target.with_suffix('.tmp'); "
        "temporary.write_text(json.dumps("
        "{'pid':child.pid,'identity':process_identity(child.pid)})); "
        "os.replace(temporary,target); "
        "time.sleep(30)"
    )
    info_path = tmp_path / "watched-child.json"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    parent = subprocess.Popen(
        [_base_python(), "-c", parent_code, child_code, str(info_path)],
        cwd=Path(__file__).resolve().parent.parent,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    child_info = None
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and child_info is None:
            assert parent.poll() is None
            try:
                child_info = json.loads(info_path.read_text())
            except (FileNotFoundError, PermissionError):
                time.sleep(0.02)
        assert child_info is not None
        assert process_state(child_info["pid"], child_info["identity"]) == "LIVE"
        parent.kill()
        parent.wait(timeout=2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and process_state(child_info["pid"], child_info["identity"]) == "LIVE":
            time.sleep(0.02)
        assert process_state(child_info["pid"], child_info["identity"]) == "DEAD"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=2)
        if child_info is not None and process_state(child_info["pid"], child_info["identity"]) == "LIVE":
            try:
                os.kill(child_info["pid"], signal.SIGTERM)
            except OSError:
                pass

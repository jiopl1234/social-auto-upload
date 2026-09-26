"""Diagnostic filesystem stalls must never strand a publishing browser."""
import asyncio
import subprocess
import time

import pytest

from orchestrator.errors import TaskError

from tests.task_fixtures import make_manifest, make_service


def test_slow_diagnostic_writer_cannot_consume_browser_cleanup_time(tmp_path, monkeypatch):
    from orchestrator.store import Store

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    original_write = Store.record_diagnostic_evidence

    def slow_write(self, *args, **kwargs):
        time.sleep(0.8)
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(Store, "record_diagnostic_evidence", slow_write)

    class BrowserTarget:
        closed = False
        context = None

        async def prepare(self, context):
            self.context = context
            context.resources_started()
            context.record_runtime({"driver": "patchright", "driver_version": "1.58.2"})
            return {"title": "Original title", "body": "Original body"}

        async def close(self):
            self.closed = True
            assert self.context is not None
            self.context.resources_closed()

    target = BrowserTarget()
    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"],
                                          limits={"total": 0.7, "cleanup": 0.2}))
    assert target.closed
    assert result is not None and result["status"] == "READY"


@pytest.mark.parametrize("cancel", [False, True])
def test_stalled_diagnostic_child_is_reaped_after_browser_shutdown(tmp_path, monkeypatch, cancel):
    from orchestrator import preflight_process

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    next_job = service.enqueue(make_manifest(tmp_path, key="next"))
    actual_popen = subprocess.Popen
    child = None
    started = asyncio.Event()

    def start_child(command, **kwargs):
        nonlocal child
        if "orchestrator.diagnostic_process" in command:
            child = actual_popen([command[0], "-c", "import time; time.sleep(30)"], **kwargs)
            started.set()
            return child
        return actual_popen(command, **kwargs)

    monkeypatch.setattr(preflight_process.subprocess, "Popen", start_child)

    class BrowserTarget:
        closed = False
        context = None

        async def prepare(self, context):
            self.context = context
            context.resources_started()
            raise TaskError("METADATA_MISMATCH", "Actual title differs", field="title")

        async def close(self):
            self.closed = True
            assert self.context is not None
            self.context.resources_closed()

    target = BrowserTarget()

    async def exercise():
        running = asyncio.create_task(service.run_once(adapters={"douyin": target}, job_id=job["job_id"],
                                                       limits={"total": 7, "cleanup": 4}))
        await asyncio.wait_for(started.wait(), timeout=3)
        assert target.closed
        await asyncio.wait_for(asyncio.sleep(0.03), timeout=0.25)
        if cancel:
            running.cancel()
        return await asyncio.wait_for(running, timeout=3)

    result = asyncio.run(exercise())
    assert result is not None and result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "METADATA_MISMATCH"
    assert service.attempts(job["job_id"])[0]["submit_count"] == 0
    assert any(w["code"] == ("CANCELLED" if cancel else "STORAGE_ERROR") for w in result["warnings"])
    assert child is not None and child.poll() is not None

    class ReadyTarget:
        async def prepare(self, context):
            return {"title": context.manifest["title"], "body": context.manifest["body"]}

        async def close(self):
            pass

    next_result = asyncio.run(service.run_once(adapters={"douyin": ReadyTarget()}, job_id=next_job["job_id"]))
    assert next_result is not None and next_result["status"] == "READY"

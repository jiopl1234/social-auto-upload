"""Crash acceptance for the public task service and a persistent local target."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import time

import pytest

from orchestrator.locking import process_identity, process_state
from tests.task_fixtures import make_manifest, make_service


ROOT = Path(__file__).resolve().parents[1]
PROCESS_SCRIPT = ROOT / "tests" / "fixtures" / "delivery_worker_process.py"
PYTHON = getattr(sys, "_base_executable", sys.executable)
WAIT_SECONDS = 10.0
PATHS = [("douyin", "video"), ("douyin", "note"), ("xiaohongshu", "video"),
         ("xiaohongshu", "note"), ("tencent", "video")]


def wait_json(path: Path, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError):
            # Windows may briefly deny a read immediately around replacement;
            # the same bounded handshake deadline still applies.
            pass
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(
                f"Process {process.pid} exited before {path.name}: "
                f"code={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
            )
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for {path.name}")
        time.sleep(0.02)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class DeliveryProcesses:
    def __init__(self, tmp_path: Path, job_id: str, platform: str):
        self.tmp_path = tmp_path
        self.job_id = job_id
        self.platform = platform
        self.processes: list[subprocess.Popen] = []
        self.calls = tmp_path / "target-calls.jsonl"
        self.deliveries = tmp_path / "target-deliveries.jsonl"

    def __enter__(self):
        return self

    def start(self, name: str, *, gate_stage: str | None = None,
              max_attempts: int = 2, omit_tags: bool = False) -> tuple[subprocess.Popen, dict]:
        config = {
            "db": str(self.tmp_path / "jobs.sqlite3"),
            "runtime": str(self.tmp_path / "runtime"),
            "media_root": str(self.tmp_path / "media"),
            "platform": self.platform,
            "job_id": self.job_id,
            "hello": str(self.tmp_path / f"{name}.hello.json"),
            "ack": str(self.tmp_path / f"{name}.ack"),
            "stage": str(self.tmp_path / f"{name}.stage.json"),
            "release": str(self.tmp_path / f"{name}.release"),
            "result": str(self.tmp_path / f"{name}.result.json"),
            "calls": str(self.calls),
            "deliveries": str(self.deliveries),
            "gate_stage": gate_stage,
            "max_attempts": max_attempts,
            "omit_tags": omit_tags,
        }
        config_path = self.tmp_path / f"{name}.config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        process = subprocess.Popen(
            [PYTHON, str(PROCESS_SCRIPT), str(config_path)],
            cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.processes.append(process)
        hello = wait_json(Path(config["hello"]), process)
        assert hello["pid"] == process.pid
        assert isinstance(hello["identity"], str)
        assert hello["identity"] == process_identity(process.pid)
        Path(config["ack"]).touch()
        return process, config

    @staticmethod
    def stage(process: subprocess.Popen, config: dict, expected: str) -> dict:
        marker_path = Path(config["stage"]).with_suffix(f".{expected}.json")
        marker = wait_json(marker_path, process)
        assert marker["stage"] == expected
        assert marker["pid"] == process.pid
        assert marker["identity"] == process_identity(process.pid)
        return marker

    @staticmethod
    def result(process: subprocess.Popen, config: dict) -> dict:
        result = wait_json(Path(config["result"]), process)
        stdout, stderr = process.communicate(timeout=WAIT_SECONDS)
        assert process.returncode == 0, (stdout, stderr)
        return result

    @staticmethod
    def kill(process: subprocess.Popen, identity: str) -> None:
        assert process_state(process.pid, identity) == "LIVE"
        process.kill()
        process.communicate(timeout=WAIT_SECONDS)
        deadline = time.monotonic() + WAIT_SECONDS
        while process_state(process.pid, identity) == "LIVE":
            if time.monotonic() >= deadline:
                raise AssertionError("Killed worker remained live")
            time.sleep(0.02)
        assert process_state(process.pid, identity) == "DEAD"

    def __exit__(self, _exc_type, _exc, _tb):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=WAIT_SECONDS)


@pytest.mark.parametrize("platform,kind", PATHS)
def test_prepare_crash_retries_within_configured_limit_without_early_delivery(tmp_path, platform, kind):
    service = make_service(tmp_path)
    queued = service.enqueue(make_manifest(tmp_path, key="prepare-crash", platform=platform, content_type=kind))
    job_id = queued["job_id"]

    with DeliveryProcesses(tmp_path, job_id, platform) as workers:
        first, first_config = workers.start("first", gate_stage="prepare")
        first_stage = workers.stage(first, first_config, "prepare")
        assert service.status(job_id)["status"] == "PREPARING"
        assert service.status(job_id)["submission"] == "NOT_SUBMITTED"
        assert read_jsonl(workers.deliveries) == []
        workers.kill(first, first_stage["identity"])

        successor, successor_config = workers.start("successor")
        resumed = workers.result(successor, successor_config)
        assert resumed["kind"] == "job"
        assert resumed["job"]["status"] == "COMPLETED"
        assert resumed["job"]["submission"] == "CONFIRMED"
        attempts = service.attempts(job_id)
        assert len(attempts) == 2
        assert attempts[0]["attempt_id"] == first_stage["attempt_id"]
        assert len(read_jsonl(workers.deliveries)) == 1
        assert [call["stage"] for call in read_jsonl(workers.calls)] == [
            "prepare", "prepare", "submit", "reconcile",
        ]


@pytest.mark.parametrize("platform,kind", PATHS)
def test_crash_after_submission_boundary_before_final_action_is_read_only_unknown(tmp_path, platform, kind):
    service = make_service(tmp_path)
    queued = service.enqueue(make_manifest(tmp_path, key="before-final-action", platform=platform, content_type=kind))
    job_id = queued["job_id"]

    with DeliveryProcesses(tmp_path, job_id, platform) as workers:
        first, first_config = workers.start("first", gate_stage="before_submit")
        boundary = workers.stage(first, first_config, "before_submit")
        assert service.status(job_id)["status"] == "SUBMITTING"
        assert service.status(job_id)["submission"] == "MAY_HAVE_SUBMITTED"
        assert service.attempts(job_id)[0]["submit_count"] == 1
        assert read_jsonl(workers.deliveries) == []
        workers.kill(first, boundary["identity"])

        recovery, recovery_config = workers.start("recovery")
        recovered = workers.result(recovery, recovery_config)
        assert recovered["kind"] == "job"
        assert recovered["job"]["status"] == "UNKNOWN_COMMIT"
        assert recovered["job"]["submission"] == "MAY_HAVE_SUBMITTED"
        assert len(service.attempts(job_id)) == 1
        assert service.attempts(job_id)[0]["attempt_id"] == boundary["attempt_id"]
        assert read_jsonl(workers.deliveries) == []
        assert [call["stage"] for call in read_jsonl(workers.calls)] == [
            "prepare", "submit", "reconcile",
        ]

        repeated, repeated_config = workers.start("repeated")
        again = workers.result(repeated, repeated_config)
        assert again["kind"] == "job"
        assert again["job"]["status"] == "UNKNOWN_COMMIT"
        assert read_jsonl(workers.deliveries) == []
        assert len(service.attempts(job_id)) == 1
        assert [call["stage"] for call in read_jsonl(workers.calls)] == [
            "prepare", "submit", "reconcile", "reconcile",
        ]


@pytest.mark.parametrize("platform,kind", PATHS)
def test_crash_after_remote_write_before_receipt_confirms_original_attempt(tmp_path, platform, kind):
    service = make_service(tmp_path)
    queued = service.enqueue(make_manifest(tmp_path, key="receipt-lost", platform=platform, content_type=kind))
    job_id = queued["job_id"]

    with DeliveryProcesses(tmp_path, job_id, platform) as workers:
        first, first_config = workers.start("first", gate_stage="after_write")
        written = workers.stage(first, first_config, "after_write")
        crossing = service.status(job_id)
        assert crossing["status"] == "SUBMITTING"
        assert crossing["submission"] == "MAY_HAVE_SUBMITTED"
        assert service.attempts(job_id)[0]["receipt_id"] is None
        deliveries = read_jsonl(workers.deliveries)
        assert len(deliveries) == 1
        work = deliveries[0]
        assert work["platform"] == crossing["platform"]
        assert work["account_id"] == crossing["manifest"]["expected_account_id"]
        assert work["content_type"] == crossing["manifest"]["content_type"]
        assert work["metadata"] == crossing["actual_metadata"]
        assert work["media_sha256"] == [asset["sha256"] for asset in crossing["manifest"]["media"]]
        assert work["source"] == "work_detail"
        assert datetime.fromisoformat(work["created_at"]).tzinfo is not None
        workers.kill(first, written["identity"])

        recovery, recovery_config = workers.start("recovery")
        recovered = workers.result(recovery, recovery_config)
        assert recovered["kind"] == "job"
        assert recovered["job"]["status"] == "COMPLETED"
        assert recovered["job"]["submission"] == "CONFIRMED"
        assert recovered["job"]["content_id"] == work["content_id"]
        assert recovered["job"]["platform_result"] == "PUBLISHED"
        assert len(service.attempts(job_id)) == 1
        assert service.attempts(job_id)[0]["attempt_id"] == written["attempt_id"]
        assert len(read_jsonl(workers.deliveries)) == 1
        assert [call["stage"] for call in read_jsonl(workers.calls)] == [
            "prepare", "submit", "reconcile",
        ]

        repeated, repeated_config = workers.start("repeated")
        again = workers.result(repeated, repeated_config)
        assert again["job"]["status"] == "COMPLETED"
        assert len(read_jsonl(workers.deliveries)) == 1
        assert len(service.attempts(job_id)) == 1
        assert [call["stage"] for call in read_jsonl(workers.calls)] == [
            "prepare", "submit", "reconcile",
        ]


def test_prepare_crash_at_attempt_limit_records_failure_without_new_delivery(tmp_path):
    service = make_service(tmp_path)
    queued = service.enqueue(make_manifest(tmp_path, key="prepare-at-limit"))
    job_id = queued["job_id"]

    with DeliveryProcesses(tmp_path, job_id, "douyin") as workers:
        first, first_config = workers.start("first", gate_stage="prepare", max_attempts=1)
        entered = workers.stage(first, first_config, "prepare")
        workers.kill(first, entered["identity"])

        recovery, recovery_config = workers.start("recovery", max_attempts=1)
        result = workers.result(recovery, recovery_config)
        assert result["kind"] == "job"
        assert result["job"]["status"] == "FAILED_PRE_SUBMIT"
        assert result["job"]["submission"] == "NOT_SUBMITTED"
        assert result["job"]["error"]["code"] == "TRANSIENT_PRE_SUBMIT"
        assert len(service.attempts(job_id)) == 1
        assert service.attempts(job_id)[0]["attempt_id"] == entered["attempt_id"]
        assert read_jsonl(workers.deliveries) == []
        assert [call["stage"] for call in read_jsonl(workers.calls)] == ["prepare"]

        repeated, repeated_config = workers.start("repeated", max_attempts=1)
        again = workers.result(repeated, repeated_config)
        assert again["job"]["status"] == "FAILED_PRE_SUBMIT"
        assert len(service.attempts(job_id)) == 1
        assert read_jsonl(workers.deliveries) == []
        assert [call["stage"] for call in read_jsonl(workers.calls)] == ["prepare"]


def test_omitted_tags_warning_survives_crash_after_write_before_receipt(tmp_path):
    service = make_service(tmp_path)
    policy = {
        "required": ["title", "body", "media"],
        "optional": ["tags", "cover", "collection"],
        "allow_omit": ["tags"],
    }
    queued = service.enqueue(make_manifest(
        tmp_path, key="omitted-tags-crash", tags=["requested"], field_policy=policy,
    ))
    job_id = queued["job_id"]

    with DeliveryProcesses(tmp_path, job_id, "douyin") as workers:
        first, first_config = workers.start("first", gate_stage="after_write", omit_tags=True)
        written = workers.stage(first, first_config, "after_write")
        crossing = service.status(job_id)
        assert crossing["status"] == "SUBMITTING"
        assert crossing["submission"] == "MAY_HAVE_SUBMITTED"
        assert crossing["manifest"]["tags"] == ["requested"]
        assert crossing["actual_metadata"]["tags"] == []
        assert service.attempts(job_id)[0]["receipt_id"] is None
        work = read_jsonl(workers.deliveries)
        assert len(work) == 1
        assert work[0]["metadata"] == crossing["actual_metadata"]
        assert work[0]["metadata"]["tags"] == []
        workers.kill(first, written["identity"])

        recovery, recovery_config = workers.start("recovery", omit_tags=True)
        recovered = workers.result(recovery, recovery_config)
        assert recovered["kind"] == "job"
        assert recovered["job"]["status"] == "COMPLETED"
        assert recovered["job"]["submission"] == "CONFIRMED"
        assert recovered["job"]["content_id"] == work[0]["content_id"]
        assert any(
            warning["code"] == "FIELD_OMITTED" and warning["field"] == "tags"
            for warning in recovered["job"]["warnings"]
        )
        assert len(service.attempts(job_id)) == 1
        assert service.attempts(job_id)[0]["attempt_id"] == written["attempt_id"]
        assert len(read_jsonl(workers.deliveries)) == 1
        assert [call["stage"] for call in read_jsonl(workers.calls)] == [
            "prepare", "submit", "reconcile",
        ]

        repeated, repeated_config = workers.start("repeated", omit_tags=True)
        again = workers.result(repeated, repeated_config)
        assert again["job"]["status"] == "COMPLETED"
        assert len(read_jsonl(workers.deliveries)) == 1
        assert len(service.attempts(job_id)) == 1
        assert [call["stage"] for call in read_jsonl(workers.calls)] == [
            "prepare", "submit", "reconcile",
        ]

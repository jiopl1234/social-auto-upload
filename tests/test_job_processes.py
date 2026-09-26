"""Process-level acceptance tests for account ownership during task execution."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from orchestrator.locking import process_state
from orchestrator.service import TaskService


ROOT = Path(__file__).resolve().parents[1]
PROCESS_SCRIPT = ROOT / "tests" / "fixtures" / "job_worker_process.py"
PYTHON = getattr(sys, "_base_executable", sys.executable)
WAIT_SECONDS = 10.0


def manifest(platform: str, account: str, key: str) -> dict:
    return {
        "schema_version": 1,
        "delivery_key": key,
        "platform": platform,
        "account": account,
        "content_type": "video",
        "title": key,
        "body": "Local preparation only",
        "tags": [],
        "media": [],
    }


def wait_json(path: Path, *, process: subprocess.Popen | None = None) -> dict:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        if process is not None and process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(
                f"Process {process.pid} exited before {path.name}: "
                f"code={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
            )
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for {path}")
        time.sleep(0.02)


def wait_dead(pid: int, identity: str, *, timeout: float = WAIT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while process_state(pid, identity) == "LIVE":
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return process_state(pid, identity) == "DEAD"


class ProcessHarness:
    def __init__(self, directory: Path):
        self.directory = directory
        self.processes: list[subprocess.Popen] = []
        self.gates: list[Path] = []
        self.resources: list[tuple[Path, Path]] = []

    def __enter__(self):
        return self

    def start(self, name: str, *, mode: str, db: Path, runtime: Path,
              platform: str, account: str, job_id: str | None = None,
              hold: bool = False, resource: bool = False) -> tuple[subprocess.Popen, dict]:
        entered = self.directory / f"{name}.entered.json"
        result = self.directory / f"{name}.result.json"
        config: dict = {
            "mode": mode,
            "db": str(db),
            "runtime": str(runtime),
            "platform": platform,
            "account": account,
            "entered": str(entered),
            "result": str(result),
        }
        if job_id is not None:
            config["job_id"] = job_id
        if hold:
            gate = self.directory / f"{name}.go"
            config["gate"] = str(gate)
            self.gates.append(gate)
        if resource:
            child_entered = self.directory / f"{name}.resource.entered.json"
            child_gate = self.directory / f"{name}.resource.go"
            config["resource_entered"] = str(child_entered)
            config["resource_gate"] = str(child_gate)
            self.resources.append((child_entered, child_gate))
            self.gates.append(child_gate)
        config_path = self.directory / f"{name}.config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        process = subprocess.Popen(
            [PYTHON, str(PROCESS_SCRIPT), str(config_path)],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        return process, config

    @staticmethod
    def entered(process: subprocess.Popen, config: dict) -> dict:
        value = wait_json(Path(config["entered"]), process=process)
        assert value["pid"] == process.pid
        assert isinstance(value["identity"], str)
        return value

    @staticmethod
    def result(process: subprocess.Popen, config: dict) -> dict:
        value = wait_json(Path(config["result"]), process=process)
        stdout, stderr = process.communicate(timeout=WAIT_SECONDS)
        assert process.returncode == 0, (stdout, stderr)
        return value

    def __exit__(self, _exc_type, _exc, _tb):
        # Opening every gate lets normal cleanup finish even if an assertion
        # failed before the test reached its planned release point.
        for gate in self.gates:
            gate.touch()
        for process in reversed(self.processes):
            if process.poll() is None:
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=2)
            else:
                process.communicate(timeout=2)
        for entered, _gate in self.resources:
            if not entered.exists():
                continue
            record = json.loads(entered.read_text(encoding="utf-8"))
            pid, identity = record["pid"], record["identity"]
            assert isinstance(pid, int) and isinstance(identity, str)
            if not wait_dead(pid, identity, timeout=2):
                # A PID is only targeted while its process-start identity
                # still matches the child recorded in the handshake.
                if process_state(pid, identity) == "LIVE":
                    os.kill(pid, signal.SIGTERM)
                assert wait_dead(pid, identity, timeout=3)


def test_live_owner_stays_exclusive_even_when_its_record_looks_old(tmp_path):
    db, runtime = tmp_path / "jobs.sqlite3", tmp_path / "runtime"
    service = TaskService(db, runtime_dir=runtime)
    first = service.enqueue(manifest("douyin", "creator", "first"))
    second = service.enqueue(manifest("douyin", "creator", "second"))

    with ProcessHarness(tmp_path) as harness:
        owner, owner_cfg = harness.start(
            "owner", mode="run", db=db, runtime=runtime,
            platform="douyin", account="creator", job_id=first["job_id"], hold=True,
        )
        harness.entered(owner, owner_cfg)
        assert service.status(first["job_id"])["status"] == "PREPARING"

        contender, contender_cfg = harness.start(
            "contender", mode="run", db=db, runtime=runtime,
            platform="douyin", account="creator", job_id=second["job_id"],
        )
        assert harness.result(contender, contender_cfg) == {
            "kind": "task_error", "code": "ACCOUNT_BUSY",
        }
        assert not Path(contender_cfg["entered"]).exists()

        # Fault-inject an old timestamp while the same owner process is alive.
        owner_records = list((runtime / "owners").glob("*.json"))
        assert len(owner_records) == 1
        record = json.loads(owner_records[0].read_text(encoding="utf-8"))
        record["created_at"] = 0
        owner_records[0].write_text(json.dumps(record), encoding="utf-8")
        aged, aged_cfg = harness.start(
            "aged-contender", mode="run", db=db, runtime=runtime,
            platform="douyin", account="creator", job_id=second["job_id"],
        )
        assert harness.result(aged, aged_cfg) == {
            "kind": "task_error", "code": "ACCOUNT_BUSY",
        }
        assert not Path(aged_cfg["entered"]).exists()
        assert service.status(second["job_id"])["status"] == "QUEUED"
        assert service.attempts(second["job_id"]) == []

        Path(owner_cfg["gate"]).touch()
        completed = harness.result(owner, owner_cfg)["job"]
        assert completed["status"] == "READY"
        assert completed["submission"] == "NOT_SUBMITTED"
        assert completed["actual_metadata"] == {"prepared_by": owner.pid}
        assert service.status(first["job_id"]) == completed
        successor, successor_cfg = harness.start(
            "successor", mode="run", db=db, runtime=runtime,
            platform="douyin", account="creator", job_id=second["job_id"],
        )
        assert harness.result(successor, successor_cfg)["job"]["status"] == "READY"
        assert len(service.attempts(first["job_id"])) == 1
        assert len(service.attempts(second["job_id"])) == 1
        assert [event["status"] for event in service.events(first["job_id"])] == [
            "QUEUED", "PREPARING", "READY",
        ]


def test_shared_runtime_excludes_aliases_across_databases(tmp_path):
    runtime = tmp_path / "runtime"
    first_db, second_db = tmp_path / "one.sqlite3", tmp_path / "two.sqlite3"
    first_service = TaskService(first_db, runtime_dir=runtime)
    second_service = TaskService(second_db, runtime_dir=runtime)
    first = first_service.enqueue(manifest("douyin", "primary", "first"))
    alias = second_service.enqueue(manifest("douyin", "alias", "alias"))
    other_platform = second_service.enqueue(manifest("xiaohongshu", "creator", "other"))

    with ProcessHarness(tmp_path) as harness:
        owner, owner_cfg = harness.start(
            "primary", mode="run", db=first_db, runtime=runtime,
            platform="douyin", account="primary", job_id=first["job_id"], hold=True,
        )
        harness.entered(owner, owner_cfg)
        blocked, blocked_cfg = harness.start(
            "alias", mode="run", db=second_db, runtime=runtime,
            platform="douyin", account="alias", job_id=alias["job_id"],
        )
        assert harness.result(blocked, blocked_cfg) == {
            "kind": "task_error", "code": "ACCOUNT_BUSY",
        }
        assert not Path(blocked_cfg["entered"]).exists()
        assert second_service.attempts(alias["job_id"]) == []

        other, other_cfg = harness.start(
            "other-platform", mode="run", db=second_db, runtime=runtime,
            platform="xiaohongshu", account="creator", job_id=other_platform["job_id"],
        )
        assert harness.result(other, other_cfg)["job"]["status"] == "READY"
        assert owner.poll() is None

        Path(owner_cfg["gate"]).touch()
        assert harness.result(owner, owner_cfg)["job"]["status"] == "READY"
        retry, retry_cfg = harness.start(
            "alias-retry", mode="run", db=second_db, runtime=runtime,
            platform="douyin", account="alias", job_id=alias["job_id"],
        )
        assert harness.result(retry, retry_cfg)["job"]["status"] == "READY"


def test_maintenance_uses_worker_lock_without_blocking_other_platform(tmp_path):
    db, runtime = tmp_path / "jobs.sqlite3", tmp_path / "runtime"
    service = TaskService(db, runtime_dir=runtime)
    same = service.enqueue(manifest("douyin", "creator", "same"))
    other = service.enqueue(manifest("tencent", "creator", "other"))

    with ProcessHarness(tmp_path) as harness:
        maintenance, maintenance_cfg = harness.start(
            "maintenance", mode="maintenance", db=db, runtime=runtime,
            platform="douyin", account="creator", hold=True,
        )
        harness.entered(maintenance, maintenance_cfg)
        blocked, blocked_cfg = harness.start(
            "same-platform", mode="run", db=db, runtime=runtime,
            platform="douyin", account="creator", job_id=same["job_id"],
        )
        assert harness.result(blocked, blocked_cfg) == {
            "kind": "task_error", "code": "ACCOUNT_BUSY",
        }
        assert not Path(blocked_cfg["entered"]).exists()
        assert service.attempts(same["job_id"]) == []

        independent, independent_cfg = harness.start(
            "independent", mode="run", db=db, runtime=runtime,
            platform="tencent", account="creator", job_id=other["job_id"],
        )
        assert harness.result(independent, independent_cfg)["job"]["status"] == "READY"
        assert maintenance.poll() is None

        Path(maintenance_cfg["gate"]).touch()
        assert harness.result(maintenance, maintenance_cfg) == {"kind": "ok"}
        retry, retry_cfg = harness.start(
            "same-after-maintenance", mode="run", db=db, runtime=runtime,
            platform="douyin", account="creator", job_id=same["job_id"],
        )
        assert harness.result(retry, retry_cfg)["job"]["status"] == "READY"


def test_killed_worker_with_live_registered_resource_refuses_takeover(tmp_path):
    db, runtime = tmp_path / "jobs.sqlite3", tmp_path / "runtime"
    service = TaskService(db, runtime_dir=runtime)
    first = service.enqueue(manifest("douyin", "creator", "first"))
    second = service.enqueue(manifest("douyin", "creator", "second"))

    with ProcessHarness(tmp_path) as harness:
        owner, owner_cfg = harness.start(
            "owner", mode="run", db=db, runtime=runtime,
            platform="douyin", account="creator", job_id=first["job_id"],
            hold=True, resource=True,
        )
        resource_record = wait_json(Path(owner_cfg["resource_entered"]), process=owner)
        assert isinstance(resource_record["identity"], str)
        harness.entered(owner, owner_cfg)
        assert process_state(resource_record["pid"], resource_record["identity"]) == "LIVE"

        owner.kill()
        owner.communicate(timeout=WAIT_SECONDS)
        assert process_state(resource_record["pid"], resource_record["identity"]) == "LIVE"

        contender, contender_cfg = harness.start(
            "contender", mode="run", db=db, runtime=runtime,
            platform="douyin", account="creator", job_id=second["job_id"],
        )
        assert harness.result(contender, contender_cfg) == {
            "kind": "task_error", "code": "RESOURCES_UNCONFIRMED",
        }
        assert not Path(contender_cfg["entered"]).exists()
        assert service.status(first["job_id"])["status"] == "PREPARING"
        assert service.status(second["job_id"])["status"] == "QUEUED"
        assert service.attempts(second["job_id"]) == []

        Path(owner_cfg["resource_gate"]).touch()
        assert wait_dead(resource_record["pid"], resource_record["identity"])
        retry, retry_cfg = harness.start(
            "unconfirmed-retry", mode="run", db=db, runtime=runtime,
            platform="douyin", account="creator", job_id=second["job_id"],
        )
        assert harness.result(retry, retry_cfg) == {
            "kind": "task_error", "code": "RESOURCES_UNCONFIRMED",
        }

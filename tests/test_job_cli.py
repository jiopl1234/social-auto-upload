import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.task_fixtures import make_manifest, make_service


PROJECT = Path(__file__).resolve().parents[1]


def run_jobs(db, *arguments):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "sau_cli", "jobs", "--db", str(db),
         "--runtime-dir", str(db.parent / "runtime"), "--media-root", str(db.parent / "media"),
         "--json", *map(str, arguments)],
        cwd=PROJECT, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", timeout=15,
    )
    assert result.stdout.strip(), result.stderr
    return result, json.loads(result.stdout)


def write_manifest(directory, **overrides):
    manifest = make_manifest(directory, key="original-episode-001", **overrides)
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def bind_local_account(db, platform="douyin"):
    source = db.parent / "source.json"
    source.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
    completed, account = run_jobs(
        db, "bind-account", "--platform", platform, "--account", "creator",
        "--expected-id", f"{platform}:creator", "--session-source", source,
    )
    assert completed.returncode == 0, account


def test_enqueue_survives_a_new_process_and_is_not_published(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    bind_local_account(db)
    manifest = write_manifest(tmp_path)
    completed, queued = run_jobs(db, "enqueue", "--manifest", manifest)
    assert completed.returncode == 0, completed.stderr
    assert queued["status"] == "QUEUED"
    assert queued["submission"] == "NOT_SUBMITTED"
    assert queued["content_id"] is None
    assert queued["attempt_id"] is None
    assert len(queued["request_digest"]) == 64

    completed, queried = run_jobs(db, "status", queued["job_id"])
    assert completed.returncode == 0, completed.stderr
    assert queried == queued

    completed, history = run_jobs(db, "events", queued["job_id"])
    assert completed.returncode == 0, completed.stderr
    assert [event["status"] for event in history["events"]] == ["QUEUED"]
    assert history["events"][0]["job_id"] == queued["job_id"]


def test_duplicate_delivery_is_stable_but_conflicting_content_is_rejected(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    bind_local_account(db)
    manifest = write_manifest(tmp_path)
    _, first = run_jobs(db, "enqueue", "--manifest", manifest)
    content = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps(dict(reversed(list(content.items())))), encoding="utf-8")
    completed, repeated = run_jobs(db, "enqueue", "--manifest", manifest)
    assert completed.returncode == 0
    assert repeated == first

    write_manifest(tmp_path, body="另一个版本")
    completed, conflict = run_jobs(db, "enqueue", "--manifest", manifest)
    assert completed.returncode == 4
    assert conflict["error"]["code"] == "CONFLICT"
    _, listing = run_jobs(db, "list")
    assert [job["job_id"] for job in listing["jobs"]] == [first["job_id"]]
    _, history = run_jobs(db, "events", first["job_id"])
    assert len(history["events"]) == 1


def test_competing_enqueue_processes_return_one_delivery(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    bind_local_account(db)
    manifest = write_manifest(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run_jobs(db, "enqueue", "--manifest", manifest), range(4)))
    assert all(process.returncode == 0 for process, _ in results), [(p.returncode, value, p.stderr) for p, value in results]
    assert len({job["job_id"] for _, job in results}) == 1
    _, listing = run_jobs(db, "list")
    assert len(listing["jobs"]) == 1


def test_duplicate_enqueue_after_media_removal_returns_original_without_new_events(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    bind_local_account(db)
    manifest_path = write_manifest(tmp_path)
    request = json.loads(manifest_path.read_text(encoding="utf-8"))
    _, first = run_jobs(db, "enqueue", "--manifest", manifest_path)
    Path(request["media"][0]).unlink()

    completed, repeated = run_jobs(db, "enqueue", "--manifest", manifest_path)
    assert completed.returncode == 0, repeated
    assert repeated == first

    manifest_path.write_text(json.dumps({**request, "body": "Changed input"}), encoding="utf-8")
    completed, conflict = run_jobs(db, "enqueue", "--manifest", manifest_path)
    assert completed.returncode == 4
    assert conflict["error"]["code"] == "CONFLICT"
    _, listing = run_jobs(db, "list")
    assert listing["jobs"] == [first]
    _, history = run_jobs(db, "events", first["job_id"])
    assert len(history["events"]) == 1
    _, attempts = run_jobs(db, "attempts", first["job_id"])
    assert attempts["attempts"] == []


def test_event_write_failure_rolls_back_delivery(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    bind_local_account(db)
    manifest = write_manifest(tmp_path)
    run_jobs(db, "list")
    # Fault injection only; all resulting state is observed through the CLI.
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'injected'); END")
    completed, error = run_jobs(db, "enqueue", "--manifest", manifest)
    assert completed.returncode == 1
    assert error["error"]["code"] == "STORAGE_ERROR"
    _, listing = run_jobs(db, "list")
    assert listing["jobs"] == []
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TRIGGER fail_event")
    _, job = run_jobs(db, "enqueue", "--manifest", manifest)
    _, history = run_jobs(db, "events", job["job_id"])
    assert len(history["events"]) == 1


def test_errors_are_json_and_unconnected_execution_does_not_publish(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    completed, missing = run_jobs(db, "status", "missing")
    assert completed.returncode == 3
    assert missing["error"]["code"] == "NOT_FOUND"
    bind_local_account(db, "tencent")
    completed, invalid = run_jobs(db, "enqueue")
    assert completed.returncode == 2
    assert invalid["error"]["code"] == "INVALID_INPUT"
    manifest = write_manifest(tmp_path, platform="tencent")
    _, queued = run_jobs(db, "enqueue", "--manifest", manifest)
    completed, unsupported = run_jobs(db, "run", "--job", queued["job_id"])
    assert completed.returncode == 5
    assert unsupported["error"]["code"] == "UNSUPPORTED"
    _, current = run_jobs(db, "status", queued["job_id"])
    assert current == queued
    _, attempts = run_jobs(db, "attempts", queued["job_id"])
    assert attempts["attempts"] == []


def test_invalid_manifest_and_future_database_schema_report_json_errors(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    manifest = tmp_path / "manifest.json"
    for content in ('{broken', '[]', '{"schema_version": 1}'):
        manifest.write_text(content, encoding="utf-8")
        completed, invalid = run_jobs(db, "enqueue", "--manifest", manifest)
        assert completed.returncode == 2
        assert invalid["error"]["code"] == "INVALID_INPUT"
    _, listing = run_jobs(db, "list")
    assert listing["jobs"] == []
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA user_version = 999")
    completed, invalid = run_jobs(db, "list")
    assert completed.returncode == 1
    assert invalid["error"]["code"] == "STORAGE_ERROR"


def test_machine_help_is_also_one_json_value(tmp_path):
    db = tmp_path / "unused.sqlite3"
    for arguments in (("--help",), ("enqueue", "--help")):
        completed, result = run_jobs(db, *arguments)
        assert completed.returncode == 0
        assert result["status"] == "HELP"
        assert "enqueue" in result["help"]
    assert not db.exists()


def test_capability_and_evidence_maintenance_are_machine_readable(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    completed, result = run_jobs(db, "capabilities")
    assert completed.returncode == 0
    assert len(result["capabilities"]) == 5
    assert all(item["production_verified"] is False for item in result["capabilities"])
    completed, pruned = run_jobs(db, "prune-evidence")
    assert completed.returncode == 0
    assert pruned == {"removed": 0, "retention_days": 30}


def test_run_of_finished_task_is_read_only_even_when_live_capability_is_restricted(tmp_path):
    import asyncio
    from tests.test_job_delivery import PublishingTarget

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    completed_job = asyncio.run(service.run_once(adapters={"douyin": PublishingTarget(service)}, job_id=job["job_id"]))
    assert completed_job is not None and completed_job["status"] == "COMPLETED"
    previous_events = service.events(job["job_id"])
    completed, result = run_jobs(service.store.path, "run", "--job", job["job_id"])
    assert completed.returncode == 0
    assert result == completed_job
    assert service.events(job["job_id"]) == previous_events


def test_run_of_failed_and_not_due_tasks_preserves_persisted_results(tmp_path):
    import asyncio
    from orchestrator.errors import TaskError

    service = make_service(tmp_path)
    future = service.enqueue(make_manifest(tmp_path, key="future", run_at={
        "local": "2099-01-01T00:00:00", "timezone": "UTC"}))
    failed = service.enqueue(make_manifest(tmp_path, key="failed"))

    class InvalidTarget:
        async def prepare(self, context):
            raise TaskError("INVALID_INPUT", "Required content is missing", field="title")

        async def close(self):
            pass

    failed = asyncio.run(service.run_once(adapters={"douyin": InvalidTarget()}, job_id=failed["job_id"]))
    assert failed is not None and failed["status"] == "FAILED_PRE_SUBMIT"
    for job in (future, failed):
        previous_events = service.events(job["job_id"])
        completed, result = run_jobs(service.store.path, "run", "--job", job["job_id"])
        assert completed.returncode == 0
        assert result == job
        assert service.events(job["job_id"]) == previous_events


def test_account_pause_reason_is_queryable_from_machine_cli(tmp_path):
    from orchestrator.service import TaskService

    database = tmp_path / "jobs.sqlite3"
    service = make_service(tmp_path)
    # Simulates a platform challenge through the public worker boundary.
    import asyncio
    from orchestrator.errors import TaskError

    class Challenge:
        async def prepare(self, context):
            raise TaskError("AUTH_REQUIRED", "Verification is required")

        async def close(self):
            pass

    path = write_manifest(tmp_path)
    service.enqueue(json.loads(path.read_text(encoding="utf-8")))
    asyncio.run(service.run_once(adapters={"douyin": Challenge()}))
    completed, result = run_jobs(database, "accounts")
    assert completed.returncode == 0
    assert result["accounts"][0]["status"] == "AUTH_REQUIRED"
    assert result["accounts"][0]["pause_reason"]["code"] == "AUTH_REQUIRED"

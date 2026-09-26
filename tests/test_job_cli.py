import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


def run_jobs(db, *arguments):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "sau_cli", "jobs", "--db", str(db),
         "--json", *map(str, arguments)],
        cwd=PROJECT, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", timeout=15,
    )
    assert result.stdout.strip(), result.stderr
    return result, json.loads(result.stdout)


def write_manifest(directory, **overrides):
    manifest = {
        "schema_version": 1,
        "delivery_key": "original-episode-001",
        "platform": "douyin",
        "account": "creator",
        "content_type": "video",
        "title": "原创作品",
        "body": "第一集正文",
        "tags": [],
        "media": [],
    }
    manifest.update(overrides)
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def test_enqueue_survives_a_new_process_and_is_not_published(tmp_path):
    db = tmp_path / "tasks.sqlite3"
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
    manifest = write_manifest(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run_jobs(db, "enqueue", "--manifest", manifest), range(4)))
    assert all(process.returncode == 0 for process, _ in results), [(p.returncode, value, p.stderr) for p, value in results]
    assert len({job["job_id"] for _, job in results}) == 1
    _, listing = run_jobs(db, "list")
    assert len(listing["jobs"]) == 1


def test_event_write_failure_rolls_back_delivery(tmp_path):
    db = tmp_path / "tasks.sqlite3"
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
    completed, invalid = run_jobs(db, "enqueue")
    assert completed.returncode == 2
    assert invalid["error"]["code"] == "INVALID_INPUT"
    manifest = write_manifest(tmp_path)
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

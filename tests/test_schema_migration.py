"""Public service behavior while upgrading older task databases."""

import asyncio
from datetime import datetime, timezone
import sqlite3

from orchestrator.service import TaskService
from tests.task_fixtures import make_manifest, make_service


def _version_two_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY, platform TEXT NOT NULL, account TEXT NOT NULL,
                delivery_key TEXT NOT NULL, request_digest TEXT NOT NULL, manifest_json TEXT NOT NULL,
                attempt_id TEXT, status TEXT NOT NULL, submission TEXT NOT NULL, platform_result TEXT,
                content_id TEXT, verified_at TEXT, actual_metadata_json TEXT, warnings_json TEXT NOT NULL,
                error_json TEXT, evidence_json TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, UNIQUE (platform, account, delivery_key),
                FOREIGN KEY (job_id, attempt_id) REFERENCES attempts (job_id, attempt_id));
            CREATE TABLE attempts (
                attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs (job_id),
                status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, owner_token TEXT,
                submission_started_at TEXT, receipt_id TEXT,
                submit_count INTEGER NOT NULL DEFAULT 0 CHECK (submit_count IN (0, 1)),
                UNIQUE (job_id, attempt_id));
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs (job_id), attempt_id TEXT,
                status TEXT NOT NULL, stage TEXT NOT NULL, created_at TEXT NOT NULL,
                details_json TEXT NOT NULL,
                FOREIGN KEY (job_id, attempt_id) REFERENCES attempts (job_id, attempt_id));
            CREATE TABLE accounts (
                platform TEXT NOT NULL, account TEXT NOT NULL, expected_id TEXT,
                session_path TEXT, status TEXT NOT NULL, pause_reason TEXT,
                updated_at TEXT NOT NULL, PRIMARY KEY (platform, account));
            INSERT INTO jobs VALUES (
                'legacy-v2-job', 'douyin', 'creator', 'legacy-v2', 'old-digest', '{}',
                NULL, 'COMPLETED', 'CONFIRMED', 'PUBLISHED', 'old-work',
                '2025-01-02T03:04:05.000000Z', '{}', '[]', NULL, '[]',
                '2025-01-01T01:02:03.000000Z', '2025-01-02T03:04:05.000000Z');
            PRAGMA user_version = 2;
        """)


def test_v2_database_upgrades_and_future_schedule_stays_queued(tmp_path):
    database = tmp_path / "jobs.sqlite3"
    _version_two_database(database)

    service = make_service(tmp_path)
    legacy = service.status("legacy-v2-job")
    assert legacy["status"] == "COMPLETED"
    assert legacy["content_id"] == "old-work"
    assert legacy["created_at"] == "2025-01-01T01:02:03.000000Z"
    assert legacy["updated_at"] == "2025-01-02T03:04:05.000000Z"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
    assert {"fanout_batches", "fanout_targets"} <= tables

    scheduled = service.enqueue(make_manifest(
        tmp_path, key="future-v2", run_at={
            "local": "2030-01-01T00:15:00", "timezone": "Asia/Shanghai",
        },
    ))
    early = TaskService(database, runtime_dir=tmp_path / "runtime",
                        media_roots=[tmp_path / "media"],
                        clock=lambda: datetime(2029, 12, 31, 16, 14, tzinfo=timezone.utc))
    result = asyncio.run(early.run_once(adapters={}, job_id=scheduled["job_id"]))
    assert result is not None and result["status"] == "QUEUED"
    assert early.attempts(scheduled["job_id"]) == []

    batch_manifest = make_manifest(tmp_path, key="migrated-batch")
    batch_manifest.pop("platform")
    batch_manifest.pop("account")
    batch_manifest["targets"] = [
        {"platform": "douyin", "account": "creator"},
        {"platform": "xiaohongshu", "account": "creator"},
    ]
    batch = service.enqueue_batch(batch_manifest)
    assert [target["status"] for target in batch["targets"]] == ["QUEUED", "QUEUED"]
    assert service.status("legacy-v2-job") == legacy

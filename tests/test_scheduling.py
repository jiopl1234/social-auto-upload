"""Public scheduling behavior with a controlled clock and durable jobs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo

import pytest

from orchestrator.errors import TaskError
from orchestrator.service import TaskService
from tests.test_job_cli import bind_local_account, run_jobs
from tests.task_fixtures import bind_test_account, make_manifest, make_service, readback_metadata


def test_explicit_local_schedule_survives_restart_with_its_utc_instant(tmp_path):
    service = make_service(tmp_path)
    scheduled = service.enqueue(make_manifest(
        tmp_path, run_at={"local": "2030-01-01T00:15:00", "timezone": "Asia/Shanghai"},
    ))

    restarted = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime")
    queried = restarted.status(scheduled["job_id"])

    assert queried["manifest"]["run_at"] == {
        "local": "2030-01-01T00:15:00",
        "timezone": "Asia/Shanghai",
        "utc": "2029-12-31T16:15:00+00:00",
        "fold": 0,
    }
    assert queried["status"] == "QUEUED"
    assert restarted.attempts(scheduled["job_id"]) == []


def test_scheduled_delivery_waits_until_due_and_records_actual_times(tmp_path):
    now = [datetime(2029, 12, 31, 16, 14, 59, tzinfo=timezone.utc)]
    database = tmp_path / "jobs.sqlite3"
    media_root = tmp_path / "media"
    media_root.mkdir()
    service = TaskService(database, runtime_dir=tmp_path / "runtime", media_roots=[media_root],
                          clock=lambda: now[0])
    bind_test_account(service, tmp_path, "douyin", "creator")
    job = service.enqueue(make_manifest(
        tmp_path, run_at={"local": "2030-01-01T00:15:00", "timezone": "Asia/Shanghai"},
    ))

    class LocalTarget:
        def __init__(self):
            self.preparations = 0
            self.submissions = 0
            self.work = None

        async def prepare(self, context):
            self.preparations += 1
            return readback_metadata(context.manifest)

        async def submit(self, context):
            self.submissions += 1
            now[0] = datetime(2029, 12, 31, 16, 15, 45, tzinfo=timezone.utc)
            self.work = {
                "content_id": "scheduled-work", "platform": "douyin", "account_id": "douyin:creator",
                "content_type": "video", "platform_result": "PUBLISHED",
                "created_at": now[0].isoformat(), "metadata": readback_metadata(context.manifest),
                "media_sha256": [item["sha256"] for item in context.manifest["media"]],
                "source": "work_detail",
            }
            return {"content_id": "scheduled-work"}

        async def reconcile(self, _context):
            return [self.work] if self.work is not None else []

        async def close(self):
            pass

    target = LocalTarget()
    early = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert early is not None and early["status"] == "QUEUED"
    assert service.attempts(job["job_id"]) == []
    assert target.preparations == target.submissions == 0

    restarted = TaskService(database, runtime_dir=tmp_path / "runtime", media_roots=[media_root],
                            clock=lambda: now[0].astimezone(ZoneInfo("America/Los_Angeles")))
    now[0] = datetime(2029, 12, 31, 16, 15, tzinfo=timezone.utc)
    delivered = asyncio.run(restarted.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert delivered is not None and delivered["status"] == "COMPLETED"
    assert delivered["verified_at"] == "2029-12-31T16:15:45.000000Z"
    assert restarted.attempts(job["job_id"])[0]["started_at"] == "2029-12-31T16:15:00.000000Z"
    assert len(restarted.attempts(job["job_id"])) == target.preparations == target.submissions == 1
    assert asyncio.run(restarted.run_once(adapters={"douyin": target}, job_id=job["job_id"])) == delivered


@pytest.mark.parametrize("schedule, field", [
    ({"local": "2026-03-08T02:30:00", "timezone": "America/Los_Angeles"}, "run_at.local"),
    ({"local": "2026-11-01T01:30:00", "timezone": "America/Los_Angeles"}, "run_at.fold"),
    ({"local": "2026-11-01T01:30:00", "timezone": "America/Los_Angeles", "fold": 2}, "run_at.fold"),
    ({"local": "2030-01-01T12:00:00Z", "timezone": "Asia/Shanghai"}, "run_at.local"),
    ({"local": "2030-01-01T12:00:00", "timezone": "Invalid/Zone"}, "run_at.timezone"),
    ({"local": "2030-01-01T12:00:00"}, "run_at.timezone"),
])
def test_invalid_or_unresolved_local_time_is_a_field_error(tmp_path, schedule, field):
    service = make_service(tmp_path)

    with pytest.raises(TaskError) as caught:
        service.enqueue(make_manifest(tmp_path, run_at=schedule))

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == field
    assert service.list_jobs() == []


def test_explicit_dst_fold_selects_two_distinct_utc_instants(tmp_path):
    service = make_service(tmp_path)
    first = service.enqueue(make_manifest(
        tmp_path, key="first", run_at={"local": "2026-11-01T01:30:00", "timezone": "America/Los_Angeles", "fold": 0},
    ))
    second = service.enqueue(make_manifest(
        tmp_path, key="second", run_at={"local": "2026-11-01T01:30:00", "timezone": "America/Los_Angeles", "fold": 1},
    ))

    assert first["manifest"]["run_at"]["utc"] == "2026-11-01T08:30:00+00:00"
    assert second["manifest"]["run_at"]["utc"] == "2026-11-01T09:30:00+00:00"


def test_local_seconds_and_fraction_are_preserved_in_utc_instant(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(
        tmp_path, run_at={"local": "2030-01-01T00:15:30.500000", "timezone": "Asia/Shanghai"},
    ))

    assert job["manifest"]["run_at"]["local"] == "2030-01-01T00:15:30.500000"
    assert job["manifest"]["run_at"]["utc"] == "2029-12-31T16:15:30.500000+00:00"


def test_cli_enqueue_and_status_report_local_zone_and_utc_start(tmp_path):
    database = tmp_path / "jobs.sqlite3"
    bind_local_account(database)
    manifest = make_manifest(
        tmp_path, run_at={"local": "2030-01-01T00:15:00", "timezone": "Asia/Shanghai"},
    )
    request = tmp_path / "manifest.json"
    request.write_text(json.dumps(manifest), encoding="utf-8")

    enqueued_process, enqueued = run_jobs(database, "enqueue", "--manifest", request)
    status_process, queried = run_jobs(database, "status", enqueued["job_id"])
    _, attempts = run_jobs(database, "attempts", enqueued["job_id"])

    assert enqueued_process.returncode == status_process.returncode == 0
    assert queried["manifest"]["run_at"] == {
        "local": "2030-01-01T00:15:00", "timezone": "Asia/Shanghai",
        "utc": "2029-12-31T16:15:00+00:00", "fold": 0,
    }
    assert queried["status"] == "QUEUED"
    assert queried["verified_at"] is None
    assert attempts["attempts"] == []


def test_run_pending_skips_future_job_and_starts_due_job(tmp_path):
    now = [datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)]
    media_root = tmp_path / "media"
    media_root.mkdir()
    service = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime",
                          media_roots=[media_root], clock=lambda: now[0])
    bind_test_account(service, tmp_path, "douyin", "creator")
    future = service.enqueue(make_manifest(
        tmp_path, key="future", run_at={"local": "2030-01-03T20:00:00", "timezone": "Asia/Shanghai"},
    ))
    due = service.enqueue(make_manifest(tmp_path, key="due"))

    class PreparationTarget:
        async def prepare(self, context):
            return readback_metadata(context.manifest)

        async def close(self):
            pass

    target = PreparationTarget()
    first = asyncio.run(service.run_pending(adapters={"douyin": target}))

    assert [job["job_id"] for job in first] == [due["job_id"]]
    assert service.attempts(future["job_id"]) == []
    assert service.status(future["job_id"])["status"] == "QUEUED"

    now[0] = datetime(2030, 1, 3, 12, 0, tzinfo=timezone.utc)
    second = asyncio.run(service.run_pending(adapters={"douyin": target}))
    assert [job["job_id"] for job in second] == [future["job_id"]]
    assert len(service.attempts(future["job_id"])) == 1


def test_clock_reversal_defers_then_reaches_attempt_limit_without_submission(tmp_path):
    due = datetime(2029, 12, 31, 16, 15, tzinfo=timezone.utc)
    early = datetime(2029, 12, 31, 16, 14, tzinfo=timezone.utc)
    now = [due]
    media_root = tmp_path / "media"
    media_root.mkdir()
    service = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime",
                          media_roots=[media_root], clock=lambda: now[0])
    bind_test_account(service, tmp_path, "douyin", "creator")
    job = service.enqueue(make_manifest(
        tmp_path, run_at={"local": "2030-01-01T00:15:00", "timezone": "Asia/Shanghai"},
    ))

    class ReversedClockTarget:
        def __init__(self):
            self.submissions = 0

        async def prepare(self, context):
            now[0] = early
            return readback_metadata(context.manifest)

        async def submit(self, _context):
            self.submissions += 1
            return {"content_id": "unexpected"}

        async def reconcile(self, _context):
            return []

        async def close(self):
            pass

    target = ReversedClockTarget()
    deferred = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"], max_attempts=2))
    assert deferred is not None and deferred["status"] == "QUEUED"
    assert service.attempts(job["job_id"])[0]["status"] == "DEFERRED"
    assert target.submissions == 0

    now[0] = due
    exhausted = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"], max_attempts=2))
    assert exhausted is not None and exhausted["status"] == "FAILED_PRE_SUBMIT"
    assert exhausted["error"]["code"] == "ATTEMPT_LIMIT"
    assert len(service.attempts(job["job_id"])) == 2
    assert target.submissions == 0
    assert asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"], max_attempts=2)) == exhausted


@pytest.mark.parametrize("max_attempts, expected_status, expected_attempt", [
    (2, "QUEUED", "DEFERRED"),
    (1, "FAILED_PRE_SUBMIT", "FAILED_PRE_SUBMIT"),
])
def test_cancelling_clock_rollback_cleanup_stops_pending_executor(
    tmp_path, max_attempts, expected_status, expected_attempt,
):
    due = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    early = datetime(2030, 1, 1, 11, 59, tzinfo=timezone.utc)
    now = [due]
    media_root = tmp_path / "media"
    media_root.mkdir()
    database = tmp_path / "jobs.sqlite3"
    service = TaskService(database, runtime_dir=tmp_path / "runtime",
                          media_roots=[media_root], clock=lambda: now[0])
    bind_test_account(service, tmp_path, "douyin", "creator")
    first = service.enqueue(make_manifest(
        tmp_path, key="first", run_at={"local": "2030-01-01T12:00:00", "timezone": "UTC"},
    ))
    second = service.enqueue(make_manifest(tmp_path, key="second"))

    class LocalTarget:
        def __init__(self):
            self.closing = asyncio.Event()
            self.release_close = asyncio.Event()
            self.defer_first = True
            self.active = None
            self.writes = []

        async def prepare(self, context):
            self.active = context.job_id
            if context.job_id == first["job_id"] and self.defer_first:
                now[0] = early
            return readback_metadata(context.manifest)

        async def submit(self, context):
            self.writes.append(context.job_id)
            return {"content_id": context.job_id}

        async def reconcile(self, context):
            return [{
                "platform": "douyin", "account_id": "douyin:creator", "content_type": "video",
                "content_id": context.job_id, "platform_result": "PUBLISHED", "source": "work_detail",
                "created_at": now[0].isoformat(), "metadata": readback_metadata(context.manifest),
            }]

        async def close(self):
            if self.active == first["job_id"] and self.defer_first:
                self.closing.set()
                await self.release_close.wait()

    target = LocalTarget()

    async def cancel_during_deferred_cleanup():
        running = asyncio.create_task(service.run_pending(adapters={"douyin": target}, max_attempts=max_attempts))
        await asyncio.wait_for(target.closing.wait(), timeout=5)
        running.cancel()
        target.release_close.set()
        return await asyncio.wait_for(running, timeout=5)

    results = asyncio.run(cancel_during_deferred_cleanup())
    assert [job["job_id"] for job in results] == [first["job_id"]]
    deferred = results[0]
    assert deferred["status"] == expected_status and deferred["submission"] == "NOT_SUBMITTED"
    if expected_status == "QUEUED":
        assert deferred["error"] is None
    else:
        assert deferred["error"]["code"] == "ATTEMPT_LIMIT"
    assert any(item["code"] == "CANCELLED" for item in deferred["warnings"])
    assert service.attempts(first["job_id"])[0]["status"] == expected_attempt
    assert service.status(second["job_id"])["status"] == "QUEUED"
    assert service.attempts(second["job_id"]) == []
    assert target.writes == []

    restarted = TaskService(database, runtime_dir=tmp_path / "runtime",
                            media_roots=[media_root], clock=lambda: now[0])
    assert restarted.status(first["job_id"]) == deferred
    schedule_events = [event for event in restarted.events(first["job_id"]) if event["stage"] == "schedule"]
    assert len(schedule_events) == 1
    assert any(item["code"] == "CANCELLED" for item in schedule_events[0]["details"]["warnings"])
    resumed = asyncio.run(restarted.run_pending(adapters={"douyin": target}, max_attempts=max_attempts))
    assert [job["job_id"] for job in resumed] == [second["job_id"]]
    assert resumed[0]["status"] == "COMPLETED"
    assert target.writes == [second["job_id"]]

    if expected_status == "FAILED_PRE_SUBMIT":
        assert restarted.status(first["job_id"]) == deferred
        return
    now[0] = due
    target.defer_first = False
    later = asyncio.run(restarted.run_pending(adapters={"douyin": target}))
    assert [job["job_id"] for job in later] == [first["job_id"]]
    assert later[0]["status"] == "COMPLETED"
    assert not any(item["code"] == "CANCELLED" for item in later[0]["warnings"])
    assert target.writes == [second["job_id"], first["job_id"]]


def test_unknown_commit_recovery_ignores_schedule_after_clock_moves_back(tmp_path):
    now = [datetime(2029, 12, 31, 16, 15, tzinfo=timezone.utc)]
    media_root = tmp_path / "media"
    media_root.mkdir()
    database = tmp_path / "jobs.sqlite3"
    service = TaskService(database, runtime_dir=tmp_path / "runtime", media_roots=[media_root],
                          clock=lambda: now[0])
    bind_test_account(service, tmp_path, "douyin", "creator")
    job = service.enqueue(make_manifest(
        tmp_path, run_at={"local": "2030-01-01T00:15:00", "timezone": "Asia/Shanghai"},
    ))

    class ReadbackTarget:
        def __init__(self):
            self.preparations = 0
            self.submissions = 0
            self.visible = False
            self.work = None

        async def prepare(self, context):
            self.preparations += 1
            return readback_metadata(context.manifest)

        async def submit(self, context):
            self.submissions += 1
            self.work = {
                "content_id": "remote-work", "platform": "douyin", "account_id": "douyin:creator",
                "content_type": "video", "platform_result": "PUBLISHED", "created_at": now[0].isoformat(),
                "metadata": readback_metadata(context.manifest),
                "media_sha256": [item["sha256"] for item in context.manifest["media"]],
                "source": "work_detail",
            }
            return {"content_id": "remote-work"}

        async def reconcile(self, _context):
            return [self.work] if self.visible else []

        async def close(self):
            pass

    target = ReadbackTarget()
    unknown = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert unknown is not None and unknown["status"] == "UNKNOWN_COMMIT"

    now[0] = datetime(2029, 12, 31, 16, 14, tzinfo=timezone.utc)
    target.visible = True
    restarted = TaskService(database, runtime_dir=tmp_path / "runtime", media_roots=[media_root],
                            clock=lambda: now[0])
    recovered = asyncio.run(restarted.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert recovered is not None and recovered["status"] == "COMPLETED"
    assert target.preparations == target.submissions == 1
    assert len(restarted.attempts(job["job_id"])) == 1

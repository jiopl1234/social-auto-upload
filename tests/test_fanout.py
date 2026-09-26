"""Multi-platform fanout through the public task interface."""

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.errors import TaskError
from orchestrator.service import TaskService
from tests.task_fixtures import make_manifest, make_service, readback_metadata


def batch_request(tmp_path, *, key="episode-one", targets=None, **changes):
    content_type = changes.pop("content_type", "video")
    request = make_manifest(tmp_path, key=key, content_type=content_type)
    request.pop("platform")
    request.pop("account")
    request["targets"] = targets if targets is not None else [
        {"platform": "douyin", "account": "creator"},
        {"platform": "xiaohongshu", "account": "creator"},
    ]
    request.update(changes)
    return request


def test_batch_creates_independent_jobs_and_reports_them(tmp_path):
    service = make_service(tmp_path)

    batch = service.enqueue_batch(batch_request(tmp_path))

    assert batch["batch_id"]
    assert batch["delivery_key"] == "episode-one"
    assert [(item["platform"], item["account"]) for item in batch["targets"]] == [
        ("douyin", "creator"), ("xiaohongshu", "creator"),
    ]
    assert all(item["status"] == "QUEUED" and item["job_id"] for item in batch["targets"])
    assert len({item["job_id"] for item in batch["targets"]}) == 2
    assert service.batch_status(batch["batch_id"]) == batch
    assert {job["delivery_key"] for job in service.list_jobs()} == {"episode-one"}


def test_scheduled_batch_and_per_target_override_keep_distinct_utc_instants(tmp_path):
    service = make_service(tmp_path)
    common_schedule = {"local": "2030-01-01T00:15:00", "timezone": "Asia/Shanghai"}
    later_schedule = {"local": "2030-01-01T01:15:00", "timezone": "Asia/Shanghai"}
    request = batch_request(
        tmp_path, key="scheduled-fanout", run_at=common_schedule,
        targets=[
            {"platform": "douyin", "account": "creator"},
            {"platform": "xiaohongshu", "account": "creator",
             "overrides": {"run_at": later_schedule}},
        ],
    )

    batch = service.enqueue_batch(request)

    assert [item["status"] for item in batch["targets"]] == ["QUEUED", "QUEUED"]
    jobs = [service.status(item["job_id"]) for item in batch["targets"]]
    assert [job["manifest"]["run_at"]["utc"] for job in jobs] == [
        "2029-12-31T16:15:00+00:00", "2029-12-31T17:15:00+00:00",
    ]
    assert service.enqueue_batch(request) == batch
    assert all(service.attempts(job["job_id"]) == [] for job in jobs)


def test_repeat_is_idempotent_and_same_key_with_new_content_conflicts(tmp_path):
    service = make_service(tmp_path)
    request = batch_request(tmp_path, targets=[
        {"platform": "douyin", "account": "creator", "overrides": {"body": "Douyin text"}},
        {"platform": "xiaohongshu", "account": "creator", "overrides": {"body": "XHS text"}},
    ])
    first = service.enqueue_batch(request)
    reordered = dict(reversed(list(request.items())))

    assert service.enqueue_batch(reordered) == first
    assert [service.status(item["job_id"])["manifest"]["body"] for item in first["targets"]] == [
        "Douyin text", "XHS text",
    ]
    assert len(service.list_jobs()) == 2

    with pytest.raises(TaskError, match="different content") as conflict:
        service.enqueue_batch({**request, "title": "Changed title"})
    assert conflict.value.code == "CONFLICT"
    assert service.batch_status(first["batch_id"]) == first
    assert len(service.list_jobs()) == 2


@pytest.mark.parametrize("change,field", [
    ({"session_path": "secret"}, "session_path"),
    ({"targets": [{"platform": "douyin", "account": "creator",
                   "overrides": {"cookies": "secret"}}]}, "targets[0].overrides.cookies"),
    ({"targets": [{"platform": "douyin", "account": "creator",
                   "overrides": {"delivery_key": "different"}}]}, "targets[0].overrides.delivery_key"),
    ({"targets": [{"platform": "douyin", "account": "creator",
                   "overrides": {"facts": {"session_path": "secret"}}}]},
     "targets[0].overrides.facts.session_path"),
    ({"targets": [{"platform": "douyin", "account": "creator",
                   "overrides": {"run_at": {"local": "2030-01-01T00:15:00",
                                             "timezone": "Asia/Shanghai", "session_path": "secret"}}}]},
     "targets[0].overrides.run_at.session_path"),
    ({"targets": [{"platform": "douyin", "account": "creator"},
                   {"platform": "douyin", "account": "creator"}]}, "targets[1]"),
    ({"targets": []}, "targets"),
])
def test_invalid_batch_shape_is_rejected_before_intake(tmp_path, change, field):
    service = make_service(tmp_path)
    with pytest.raises(TaskError) as invalid:
        service.enqueue_batch(batch_request(tmp_path, **change))
    assert invalid.value.code == "INVALID_INPUT"
    assert invalid.value.field == field
    assert service.list_jobs() == []
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'fanout_batches'"
        ).fetchone() is None


def test_invalid_target_schedule_is_visible_without_blocking_other_target(tmp_path):
    service = make_service(tmp_path)
    request = batch_request(
        tmp_path, key="invalid-target-schedule",
        targets=[
            {"platform": "douyin", "account": "creator"},
            {"platform": "xiaohongshu", "account": "creator", "overrides": {
                "run_at": {"local": "2026-03-08T02:30:00", "timezone": "America/Los_Angeles"},
            }},
        ],
    )

    batch = service.enqueue_batch(request)

    assert [item["status"] for item in batch["targets"]] == ["QUEUED", "ERROR"]
    assert batch["targets"][1]["error"]["field"] == "run_at.local"
    assert batch["status"] == "PARTIAL_FAILURE"
    assert len(service.list_jobs()) == 1


class RecordingTarget:
    def __init__(self, *, prepare_error=None, disconnect=False, visible=True,
                 platform_result="PUBLISHED"):
        self.prepare_error = prepare_error
        self.disconnect = disconnect
        self.visible = visible
        self.platform_result = platform_result
        self.preparations = []
        self.writes = []
        self.lookups = 0
        self.applied = None

    async def prepare(self, context):
        self.preparations.append(context.job_id)
        if self.prepare_error is not None:
            raise self.prepare_error
        self.applied = readback_metadata(context.manifest)
        return self.applied

    async def submit(self, context):
        assert self.applied is not None
        content_id = f"{context.manifest['platform']}-work-{len(self.writes) + 1}"
        self.writes.append({
            "content_id": content_id,
            "platform": context.manifest["platform"],
            "account_id": context.manifest["expected_account_id"],
            "content_type": context.manifest["content_type"],
            "platform_result": self.platform_result,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": dict(self.applied),
            "media_sha256": [item["sha256"] for item in context.manifest["media"]],
            "source": "work_detail",
        })
        if self.disconnect:
            raise ConnectionError("Receipt lost after remote publication")
        return {"content_id": content_id}

    async def reconcile(self, context):
        self.lookups += 1
        return list(self.writes) if self.visible else []

    async def close(self):
        pass


def test_unsupported_target_is_visible_and_other_target_can_publish(tmp_path):
    service = make_service(tmp_path)
    request = batch_request(
        tmp_path, key="note-one", content_type="note",
        targets=[{"platform": "douyin", "account": "creator"},
                 {"platform": "tencent", "account": "creator"}],
    )

    batch = service.enqueue_batch(request)

    assert batch["status"] == "PARTIAL_FAILURE"
    assert batch["targets"][0]["status"] == "QUEUED"
    assert batch["targets"][1]["job_id"] is None
    assert batch["targets"][1]["status"] == "ERROR"
    assert batch["targets"][1]["error"]["code"] == "UNSUPPORTED"
    target = RecordingTarget()
    finished = asyncio.run(service.run_pending(adapters={"douyin": target}))
    assert [job["status"] for job in finished] == ["COMPLETED"]
    queried = service.batch_status(batch["batch_id"])
    assert [item["status"] for item in queried["targets"]] == ["COMPLETED", "ERROR"]
    assert queried["targets"][0]["content_id"] == "douyin-work-1"
    assert queried["targets"][0]["evidence"]
    assert queried["status"] == "PARTIAL_FAILURE"
    assert len(target.writes) == len(service.list_jobs()) == 1
    assert service.enqueue_batch(request) == queried


def test_paused_account_is_explicit_and_other_platform_runs(tmp_path):
    service = make_service(tmp_path)

    class AuthChallenge:
        async def prepare(self, context):
            raise TaskError("AUTH_REQUIRED", "Verification is required")

        async def close(self):
            pass

    probe = service.enqueue(make_manifest(tmp_path, key="pause-probe"))
    asyncio.run(service.run_once(adapters={"douyin": AuthChallenge()}, job_id=probe["job_id"]))
    batch = service.enqueue_batch(batch_request(tmp_path, key="after-pause"))

    assert batch["targets"][0]["status"] == "PAUSED"
    assert batch["targets"][0]["job_status"] == "QUEUED"
    assert batch["targets"][0]["pause_reason"]["code"] == "AUTH_REQUIRED"
    assert batch["targets"][1]["status"] == "QUEUED"
    xhs = RecordingTarget()
    results = asyncio.run(service.run_pending(adapters={"xiaohongshu": xhs}))
    assert [job["platform"] for job in results] == ["xiaohongshu"]
    current = service.batch_status(batch["batch_id"])
    assert [item["status"] for item in current["targets"]] == ["PAUSED", "COMPLETED"]
    assert len(xhs.writes) == 1


def test_failed_target_retries_without_resending_completed_target(tmp_path):
    service = make_service(tmp_path)
    request = batch_request(tmp_path, key="one-fails-first")
    batch = service.enqueue_batch(request)
    douyin = RecordingTarget()
    xhs = RecordingTarget(prepare_error=TaskError("TRANSIENT_PRE_SUBMIT", "Temporary outage"))

    asyncio.run(service.run_once(adapters={"douyin": douyin}, job_id=batch["targets"][0]["job_id"]))
    asyncio.run(service.run_once(adapters={"xiaohongshu": xhs}, job_id=batch["targets"][1]["job_id"]))
    partial = service.batch_status(batch["batch_id"])
    assert [item["status"] for item in partial["targets"]] == ["COMPLETED", "FAILED_PRE_SUBMIT"]
    assert partial["targets"][1]["error"]["code"] == "TRANSIENT_PRE_SUBMIT"
    assert partial["status"] == "PARTIAL_FAILURE"

    restarted = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime",
                            media_roots=[tmp_path / "media"])
    assert restarted.enqueue_batch(request) == partial
    xhs.prepare_error = None
    retried = asyncio.run(restarted.run_pending(adapters={"douyin": douyin, "xiaohongshu": xhs}))
    assert [job["platform"] for job in retried] == ["xiaohongshu"]
    assert restarted.batch_status(batch["batch_id"])["status"] == "COMPLETED"
    assert len(douyin.writes) == len(xhs.writes) == 1
    assert len(restarted.list_jobs()) == 2


def test_rejected_remote_result_does_not_make_batch_a_success(tmp_path):
    service = make_service(tmp_path)
    batch = service.enqueue_batch(batch_request(tmp_path, key="remote-rejection"))
    douyin = RecordingTarget()
    xhs = RecordingTarget(platform_result="REJECTED")

    completed = asyncio.run(service.run_pending(adapters={"douyin": douyin, "xiaohongshu": xhs}))

    assert [job["status"] for job in completed] == ["COMPLETED", "COMPLETED"]
    current = service.batch_status(batch["batch_id"])
    assert [item["platform_result"] for item in current["targets"]] == ["PUBLISHED", "REJECTED"]
    assert current["status"] == "PARTIAL_FAILURE"
    assert len(douyin.writes) == len(xhs.writes) == 1


def test_cancelled_target_is_terminal_partial_failure(tmp_path):
    service = make_service(tmp_path)
    batch = service.enqueue_batch(batch_request(tmp_path, key="one-cancelled"))
    douyin = RecordingTarget()

    class WaitingTarget:
        def __init__(self):
            self.started = asyncio.Event()

        async def prepare(self, context):
            self.started.set()
            await asyncio.Event().wait()

        async def close(self):
            pass

    xhs = WaitingTarget()
    completed = asyncio.run(service.run_once(adapters={"douyin": douyin},
                                             job_id=batch["targets"][0]["job_id"]))
    assert completed is not None and completed["status"] == "COMPLETED"

    async def cancel_during_prepare():
        running = asyncio.create_task(service.run_once(
            adapters={"xiaohongshu": xhs}, job_id=batch["targets"][1]["job_id"],
        ))
        await asyncio.wait_for(xhs.started.wait(), timeout=2)
        running.cancel()
        return await running

    cancelled = asyncio.run(cancel_during_prepare())
    assert cancelled is not None and cancelled["status"] == "CANCELLED"
    current = service.batch_status(batch["batch_id"])
    assert [item["status"] for item in current["targets"]] == ["COMPLETED", "CANCELLED"]
    assert current["targets"][1]["error"]["code"] == "CANCELLED"
    assert current["status"] == "PARTIAL_FAILURE"
    assert len(douyin.writes) == 1
    assert asyncio.run(service.run_pending(adapters={"douyin": douyin, "xiaohongshu": xhs})) == []


def test_auth_challenge_blocked_target_is_partial_or_all_failure(tmp_path):
    service = make_service(tmp_path)
    batch = service.enqueue_batch(batch_request(tmp_path, key="one-blocked"))

    class ChallengeTarget:
        async def prepare(self, context):
            raise TaskError("AUTH_REQUIRED", "Verification is required")

        async def close(self):
            pass

    blocked = asyncio.run(service.run_once(adapters={"douyin": ChallengeTarget()},
                                           job_id=batch["targets"][0]["job_id"]))
    xhs = RecordingTarget()
    completed = asyncio.run(service.run_once(adapters={"xiaohongshu": xhs},
                                             job_id=batch["targets"][1]["job_id"]))
    assert blocked is not None and blocked["status"] == "BLOCKED"
    assert completed is not None and completed["status"] == "COMPLETED"
    current = service.batch_status(batch["batch_id"])
    assert [item["status"] for item in current["targets"]] == ["BLOCKED", "COMPLETED"]
    assert current["targets"][0]["error"]["code"] == "AUTH_REQUIRED"
    assert current["status"] == "PARTIAL_FAILURE"

    only_blocked = service.enqueue_batch(batch_request(
        tmp_path, key="all-blocked", targets=[{"platform": "tencent", "account": "creator"}],
    ))
    result = asyncio.run(service.run_once(adapters={"tencent": ChallengeTarget()},
                                          job_id=only_blocked["targets"][0]["job_id"]))
    assert result is not None and result["status"] == "BLOCKED"
    assert service.batch_status(only_blocked["batch_id"])["status"] == "FAILED"


def test_unknown_target_survives_restart_and_repeat_without_resubmission(tmp_path):
    service = make_service(tmp_path)
    request = batch_request(tmp_path, key="one-unknown")
    batch = service.enqueue_batch(request)
    douyin = RecordingTarget()
    xhs = RecordingTarget(disconnect=True, visible=False)
    asyncio.run(service.run_once(adapters={"douyin": douyin}, job_id=batch["targets"][0]["job_id"]))
    asyncio.run(service.run_once(adapters={"xiaohongshu": xhs}, job_id=batch["targets"][1]["job_id"]))

    current = service.batch_status(batch["batch_id"])
    assert [item["status"] for item in current["targets"]] == ["COMPLETED", "UNKNOWN_COMMIT"]
    assert current["targets"][1]["evidence"] == []
    assert current["status"] == "PARTIAL_FAILURE"
    for media in (tmp_path / "media").iterdir():
        media.unlink()
    restarted = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime",
                            media_roots=[tmp_path / "media"])
    assert restarted.enqueue_batch(request) == current
    assert asyncio.run(restarted.run_pending(adapters={"douyin": douyin, "xiaohongshu": xhs})) == []
    assert len(douyin.writes) == len(xhs.writes) == 1
    xhs.visible = True
    reconciled = asyncio.run(restarted.run_once(adapters={"xiaohongshu": xhs},
                                                job_id=batch["targets"][1]["job_id"]))
    assert reconciled is not None
    assert reconciled["status"] == "COMPLETED"
    assert restarted.batch_status(batch["batch_id"])["status"] == "COMPLETED"
    assert len(xhs.writes) == 1


def test_interrupted_link_recovers_existing_child_without_media(tmp_path):
    service = make_service(tmp_path)
    request = batch_request(tmp_path, key="link-interrupted", targets=[
        {"platform": "douyin", "account": "creator"},
    ])
    seed = service.enqueue_batch(batch_request(
        tmp_path, key="seed-unsupported", content_type="note",
        targets=[{"platform": "tencent", "account": "creator"}],
    ))
    assert seed["targets"][0]["error"]["code"] == "UNSUPPORTED"
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as connection:
        connection.execute("""CREATE TRIGGER block_batch_link BEFORE UPDATE OF job_id ON fanout_targets
            WHEN NEW.job_id IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'simulated receipt-link crash'); END""")

    with pytest.raises(TaskError) as interrupted:
        service.enqueue_batch(request)
    assert interrupted.value.code == "STORAGE_ERROR"
    existing_job = service.list_jobs()[0]
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as connection:
        connection.execute("DROP TRIGGER block_batch_link")
    for media in (tmp_path / "media").iterdir():
        media.unlink()

    restarted = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime",
                            media_roots=[tmp_path / "media"])
    recovered = restarted.enqueue_batch(request)
    assert recovered["targets"][0]["job_id"] == existing_job["job_id"]
    assert len(restarted.list_jobs()) == 1
    assert restarted.enqueue_batch(request) == recovered


def test_pending_child_uses_original_snapshot_after_media_changes(tmp_path):
    service = make_service(tmp_path)
    request = batch_request(tmp_path, key="media-changed", targets=[
        {"platform": "douyin", "account": "creator"},
    ])
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as connection:
        connection.execute("""CREATE TRIGGER block_child_enqueue BEFORE INSERT ON jobs
            BEGIN SELECT RAISE(ABORT, 'simulated child enqueue crash'); END""")
    with pytest.raises(TaskError) as interrupted:
        service.enqueue_batch(request)
    assert interrupted.value.code == "STORAGE_ERROR"
    assert service.list_jobs() == []
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as connection:
        connection.execute("DROP TRIGGER block_child_enqueue")
    media_path = Path(request["media"][0])
    media_path.write_bytes(media_path.read_bytes() + b"changed")

    restarted = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime",
                            media_roots=[tmp_path / "media"])
    resumed = restarted.enqueue_batch(request)
    job = restarted.status(resumed["targets"][0]["job_id"])
    assert job["manifest"]["media"][0]["sha256"] != sha256(media_path.read_bytes()).hexdigest()
    target = RecordingTarget()
    failed = asyncio.run(restarted.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert failed is not None
    assert failed["status"] == "FAILED_PRE_SUBMIT"
    assert target.writes == []


def run_jobs(db: Path, *arguments: object) -> tuple[subprocess.CompletedProcess[str], dict]:
    project = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "sau_cli", "jobs", "--db", str(db),
         "--runtime-dir", str(db.parent / "runtime"), "--media-root", str(db.parent / "media"),
         "--json", *map(str, arguments)],
        cwd=project, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", timeout=15,
    )
    assert completed.stdout.strip(), completed.stderr
    return completed, json.loads(completed.stdout)


def test_batch_cli_enqueues_and_queries_across_processes(tmp_path):
    make_service(tmp_path)
    request = batch_request(tmp_path, key="cli-batch")
    manifest_path = tmp_path / "batch.json"
    manifest_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    db = tmp_path / "jobs.sqlite3"

    enqueued, batch = run_jobs(db, "enqueue-batch", "--manifest", manifest_path)
    queried, current = run_jobs(db, "batch-status", batch["batch_id"])

    assert enqueued.returncode == queried.returncode == 0
    assert current == batch
    assert [item["status"] for item in current["targets"]] == ["QUEUED", "QUEUED"]
    repeated, same = run_jobs(db, "enqueue-batch", "--manifest", manifest_path)
    assert repeated.returncode == 0
    assert same == batch
    manifest_path.write_text(json.dumps({**request, "body": "changed"}), encoding="utf-8")
    conflicting, error = run_jobs(db, "enqueue-batch", "--manifest", manifest_path)
    assert conflicting.returncode == 4
    assert error["error"]["code"] == "CONFLICT"

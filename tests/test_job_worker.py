import asyncio
import json
import sqlite3

import pytest

from orchestrator.errors import TaskError
from orchestrator.service import TaskService


def manifest(platform="douyin", account="creator", key="one"):
    return {
        "schema_version": 1, "delivery_key": key, "platform": platform,
        "account": account, "content_type": "video", "title": "Original",
        "body": "Unchanged body", "tags": [], "media": [],
    }


class PreparedTarget:
    def __init__(self):
        self.prepared = []
        self.closed = 0

    async def prepare(self, context):
        context.assert_owner()
        self.prepared.append(context.job_id)
        return {"title": context.manifest["title"], "body": context.manifest["body"]}

    async def close(self):
        self.closed += 1


def test_run_records_one_attempt_and_does_not_infer_a_platform_result(tmp_path):
    service = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime")
    job = service.enqueue(manifest())
    target = PreparedTarget()
    result = asyncio.run(service.run_once(adapters={"douyin": target}))
    assert result is not None
    assert result["job_id"] == job["job_id"]
    assert result["status"] == "READY"
    assert result["platform_result"] is None
    assert result["submission"] == "NOT_SUBMITTED"
    assert result["actual_metadata"] == {"title": "Original", "body": "Unchanged body"}
    assert service.status(job["job_id"]) == result
    assert len(service.attempts(job["job_id"])) == 1
    assert [e["status"] for e in service.events(job["job_id"])] == ["QUEUED", "PREPARING", "READY"]
    assert asyncio.run(service.run_once(adapters={"douyin": target})) is None
    assert target.prepared == [job["job_id"]]
    assert target.closed == 1


def test_unconfirmed_resource_shutdown_blocks_account_and_other_platform_proceeds(tmp_path):
    class UnclosedTarget(PreparedTarget):
        async def prepare(self, context):
            context.resources_started()
            return await super().prepare(context)

    service = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime")
    unsafe = service.enqueue(manifest())
    skipped = service.enqueue(manifest(key="two"))
    other = service.enqueue(manifest(platform="xiaohongshu"))
    unsafe_target, other_target = UnclosedTarget(), PreparedTarget()
    results = asyncio.run(service.run_pending(adapters={"douyin": unsafe_target, "xiaohongshu": other_target}))
    assert {r["job_id"] for r in results} == {unsafe["job_id"], other["job_id"]}
    result = service.status(unsafe["job_id"])
    assert result["status"] == "BLOCKED"
    assert result["error"]["code"] == "RESOURCES_UNCONFIRMED"
    assert service.status(skipped["job_id"])["status"] == "QUEUED"
    assert service.status(other["job_id"])["status"] == "READY"
    account = service.accounts()[0]
    assert account["platform"] == "douyin"
    assert account["pause_reason"]["code"] == "RESOURCES_UNCONFIRMED"


def test_authentication_pause_skips_same_account_but_runs_other_platform(tmp_path):
    class ChallengeTarget(PreparedTarget):
        async def prepare(self, context):
            self.prepared.append(context.job_id)
            raise TaskError("AUTH_REQUIRED", "Session verification is required")

    service = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime")
    blocked = service.enqueue(manifest())
    pending = service.enqueue(manifest(key="next"))
    other = service.enqueue(manifest(platform="tencent"))
    challenge, healthy = ChallengeTarget(), PreparedTarget()
    results = asyncio.run(service.run_pending(adapters={"douyin": challenge, "tencent": healthy}))
    assert len(results) == 2
    assert service.status(blocked["job_id"])["status"] == "BLOCKED"
    assert service.status(pending["job_id"])["status"] == "QUEUED"
    assert service.attempts(pending["job_id"]) == []
    assert service.status(other["job_id"])["status"] == "READY"
    assert challenge.prepared == [blocked["job_id"]]
    assert challenge.closed == healthy.closed == 1
    assert service.accounts()[0]["pause_reason"]["code"] == "AUTH_REQUIRED"


def test_executor_that_loses_ownership_cannot_write_or_finish_the_attempt(tmp_path):
    database = tmp_path / "jobs.sqlite3"
    service = TaskService(database, runtime_dir=tmp_path / "runtime")
    job = service.enqueue(manifest())

    class FencedTarget(PreparedTarget):
        async def prepare(self, context):
            # External ownership fault. Observe all results via the task API.
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE attempts SET owner_token = 'new-owner' WHERE attempt_id = ?", (context.attempt_id,))
            context.assert_owner()
            return await super().prepare(context)

    target = FencedTarget()
    with pytest.raises(TaskError) as caught:
        asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert caught.value.code == "OWNERSHIP_LOST"
    assert target.prepared == []
    assert target.closed == 1
    assert service.status(job["job_id"])["status"] == "PREPARING"
    assert [e["status"] for e in service.events(job["job_id"])] == ["QUEUED", "PREPARING"]


def test_malformed_owner_record_is_a_controlled_block_without_a_new_attempt(tmp_path):
    service = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime")
    job = service.enqueue(manifest())
    lease = service.maintenance("douyin", "creator")
    lease.path.parent.mkdir(parents=True)
    lease.path.write_text(json.dumps({"active": True, "pid": "damaged"}), encoding="utf-8")
    with pytest.raises(TaskError) as caught:
        asyncio.run(service.run_once(adapters={"douyin": PreparedTarget()}, job_id=job["job_id"]))
    assert caught.value.code == "RESOURCES_UNCONFIRMED"
    assert service.status(job["job_id"])["status"] == "QUEUED"
    assert service.attempts(job["job_id"]) == []

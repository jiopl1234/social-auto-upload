"""Deadline and cancellation behavior at the public execution seam."""

import asyncio
import time

import pytest

from orchestrator.errors import TaskError
from tests.test_job_delivery import PublishingTarget
from tests.task_fixtures import make_manifest, make_service


class StalledPreparation:
    def __init__(self):
        self.closed = False

    async def prepare(self, context):
        await asyncio.Future()

    async def close(self):
        self.closed = True


def test_preparation_timeout_finishes_without_submission_and_closes_adapter(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    adapter = StalledPreparation()
    started = time.monotonic()
    result = asyncio.run(service.run_once(
        adapters={"douyin": adapter}, job_id=job["job_id"],
        limits={"total": 1, "prepare": 0.03, "cleanup": 0.2},
    ))
    assert result is not None
    assert time.monotonic() - started < 2
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["submission"] == "NOT_SUBMITTED"
    assert result["error"]["code"] == "TIMEOUT"
    assert result["error"]["field"] == "prepare"
    assert adapter.closed
    assert service.attempts(job["job_id"])[0]["submit_count"] == 0


@pytest.mark.parametrize("stage", ["navigation", "upload", "processing", "metadata"])
def test_stalled_page_stage_is_bounded_before_any_publish(tmp_path, stage):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))

    class StalledStage(PublishingTarget):
        async def prepare(self, context):
            return await context.run_stage(stage, lambda: asyncio.Future())

    target = StalledStage(service)
    result = asyncio.run(service.run_once(
        adapters={"douyin": target}, job_id=job["job_id"],
        limits={"total": 1, stage: 0.03, "cleanup": 0.2},
    ))
    assert result is not None and result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "TIMEOUT"
    assert result["error"]["field"] == stage
    assert target.writes == [] and target.closed == 1


def test_cancel_after_remote_write_stays_unknown_then_only_reconciles(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    written = asyncio.Event()

    class InterruptedSubmission(PublishingTarget):
        async def submit(self, context):
            await super().submit(context)
            written.set()
            return await asyncio.Future[dict[str, str]]()

    target = InterruptedSubmission(service)

    async def cancel_after_write():
        running = asyncio.create_task(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
        await asyncio.wait_for(written.wait(), 2)
        running.cancel()
        return await running

    result = asyncio.run(cancel_after_write())
    assert result is not None and result["status"] == "UNKNOWN_COMMIT"
    assert result["submission"] == "MAY_HAVE_SUBMITTED"
    assert result["error"]["code"] == "CANCELLED"
    assert target.lookups == 0 and len(target.writes) == 1 and target.closed == 1
    recovered = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert recovered is not None and recovered["status"] == "COMPLETED"
    assert target.lookups == 1 and len(target.writes) == 1
    assert len(service.attempts(job["job_id"])) == 1


def test_submit_and_read_only_timeouts_never_repeat_final_action(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))

    class StalledDelivery(PublishingTarget):
        async def submit(self, context):
            await super().submit(context)
            return await asyncio.Future[dict[str, str]]()

        async def reconcile(self, context):
            self.lookups += 1
            return await asyncio.Future[list[dict]]()

    target = StalledDelivery(service)
    result = asyncio.run(service.run_once(
        adapters={"douyin": target}, job_id=job["job_id"],
        limits={"total": 1, "submit": 0.03, "reconcile": 0.03, "cleanup": 0.2},
    ))
    assert result is not None and result["status"] == "UNKNOWN_COMMIT"
    assert result["error"]["code"] == "TIMEOUT" and result["error"]["field"] == "submit"
    assert any(item["code"] == "TIMEOUT" and item["field"] == "reconcile" for item in result["warnings"])
    assert len(target.writes) == target.lookups == target.closed == 1
    assert asyncio.run(service.run_pending(adapters={"douyin": target})) == []


def test_total_deadline_reserves_cleanup_and_does_not_reset_between_stages(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))

    class SlowStages(PublishingTarget):
        async def prepare(self, context):
            await context.run_stage("upload", lambda: asyncio.sleep(0.12))
            await context.run_stage("processing", lambda: asyncio.sleep(0.12))
            return await super().prepare(context)

        async def close(self):
            await asyncio.sleep(0.05)
            await super().close()

    target = SlowStages(service)
    result = asyncio.run(service.run_once(
        adapters={"douyin": target}, job_id=job["job_id"],
        limits={"total": 0.3, "cleanup": 0.1},
    ))
    assert result is not None and result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "TIMEOUT"
    assert target.closed == 1 and target.writes == []


def test_repeated_operations_share_the_stage_budget(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))

    class RepeatedUploads(PublishingTarget):
        async def prepare(self, context):
            for _ in range(3):
                await context.run_stage("upload", lambda: asyncio.sleep(0.04))
            return await super().prepare(context)

    target = RepeatedUploads(service)
    result = asyncio.run(service.run_once(
        adapters={"douyin": target}, job_id=job["job_id"],
        limits={"total": 1, "upload": 0.07, "cleanup": 0.2},
    ))
    assert result is not None and result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "TIMEOUT" and result["error"]["field"] == "upload"
    assert target.writes == []


def test_cancellation_during_cleanup_allows_bounded_shutdown_to_finish(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    closing = asyncio.Event()

    class ClosingPreparation:
        closed = False

        async def prepare(self, context):
            return {"title": context.manifest["title"]}

        async def close(self):
            closing.set()
            await asyncio.sleep(0.03)
            self.closed = True

    target = ClosingPreparation()

    async def cancel_during_close():
        running = asyncio.create_task(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
        await asyncio.wait_for(closing.wait(), 2)
        running.cancel()
        return await running

    result = asyncio.run(cancel_during_close())
    assert result is not None and result["status"] == "CANCELLED"
    assert result["submission"] == "NOT_SUBMITTED"
    assert result["error"]["code"] == "CANCELLED"
    assert target.closed
    assert next(account for account in service.accounts() if account["platform"] == "douyin")["status"] == "READY"


@pytest.mark.parametrize("limits", [{"total": True}, {"prepare": float("nan")}, {"extra": 1}, {"total": 1}])
def test_invalid_budgets_do_not_start_an_attempt(tmp_path, limits):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with pytest.raises(TaskError) as error:
        asyncio.run(service.run_once(adapters={"douyin": PublishingTarget(service)},
                                     job_id=job["job_id"], limits=limits))
    assert error.value.code == "INVALID_INPUT"
    assert service.status(job["job_id"])["status"] == "QUEUED"
    assert service.attempts(job["job_id"]) == []

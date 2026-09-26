"""Delivery semantics through the public task interface and a stateful target."""

import asyncio
from datetime import datetime, timezone
import sqlite3
import pytest

from orchestrator.errors import TaskError
from orchestrator.service import TaskService
from tests.task_fixtures import make_manifest, make_service, readback_metadata


def make_delivery(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, key="episode-one"))
    return service, job


class PublishingTarget:
    def __init__(self, service, *, disconnect=False, visible=True, platform_result="PUBLISHED"):
        self.service = service
        self.disconnect = disconnect
        self.visible = visible
        self.platform_result = platform_result
        self.writes = []
        self.preparations = []
        self.closed = 0
        self.lookups = 0
        self.applied = {}

    async def prepare(self, context):
        context.assert_owner()
        self.preparations.append(context.job_id)
        self.applied = readback_metadata(context.manifest)
        return self.applied

    async def submit(self, context):
        # Observe the durable boundary before the external side effect.
        job = self.service.status(context.job_id)
        assert job["status"] == "SUBMITTING"
        assert job["submission"] == "MAY_HAVE_SUBMITTED"
        assert self.service.events(context.job_id)[-1]["status"] == "SUBMITTING"
        self.writes.append({
            "content_id": "work-001", "platform": context.manifest["platform"],
            "account_id": context.manifest["expected_account_id"],
            "content_type": context.manifest["content_type"], "platform_result": self.platform_result,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": dict(self.applied),
            "media_sha256": [asset["sha256"] for asset in context.manifest["media"]],
            "source": "work_detail",
        })
        if self.disconnect:
            raise ConnectionError("Receipt lost after the remote side effect")
        return {"content_id": "work-001"}

    async def reconcile(self, context):
        self.lookups += 1
        return list(self.writes) if self.visible else []

    async def close(self):
        self.closed += 1


def test_delivery_records_boundary_and_confirms_one_remote_work(tmp_path):
    service, job = make_delivery(tmp_path)
    target = PublishingTarget(service)
    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert result is not None
    assert result["status"] == "COMPLETED"
    assert result["submission"] == "CONFIRMED"
    assert result["platform_result"] == "PUBLISHED"
    assert result["content_id"] == "work-001"
    assert result["verified_at"] is not None
    assert service.status(job["job_id"]) == result
    assert [event["status"] for event in service.events(job["job_id"])] == [
        "QUEUED", "PREPARING", "READY", "SUBMITTING", "COMPLETED",
    ]
    repeated = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert repeated == result
    assert len(target.writes) == len(target.preparations) == 1
    assert len(service.attempts(job["job_id"])) == 1


def test_lost_receipt_recovers_by_read_only_lookup_without_original_media(tmp_path):
    service, job = make_delivery(tmp_path)
    target = PublishingTarget(service, disconnect=True, visible=False)
    unknown = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert unknown is not None
    assert unknown["status"] == "UNKNOWN_COMMIT"
    assert unknown["submission"] == "MAY_HAVE_SUBMITTED"
    assert unknown["content_id"] is None
    for asset in (tmp_path / "media").iterdir():
        asset.unlink()
    restarted = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime",
                            media_roots=[tmp_path / "media"])
    target.visible = True
    recovered = asyncio.run(restarted.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert recovered is not None
    assert recovered["status"] == "COMPLETED"
    assert recovered["submission"] == "CONFIRMED"
    assert recovered["content_id"] == "work-001"
    assert len(target.writes) == len(target.preparations) == 1
    assert target.lookups == 2
    assert len(restarted.attempts(job["job_id"])) == 1
    assert restarted.events(job["job_id"])[-1]["status"] == recovered["status"]


def test_only_certain_pre_submit_transient_errors_retry_within_attempt_limit(tmp_path):
    service, job = make_delivery(tmp_path)

    class UnavailableTarget(PublishingTarget):
        async def prepare(self, context):
            self.preparations.append(context.job_id)
            raise TaskError("TRANSIENT_PRE_SUBMIT", "Upload service is temporarily unavailable")

    target = UnavailableTarget(service)
    results = asyncio.run(service.run_pending(adapters={"douyin": target}, max_attempts=2))
    assert len(results) == 2
    assert all(result["status"] == "FAILED_PRE_SUBMIT" for result in results)
    assert all(result["submission"] == "NOT_SUBMITTED" for result in results)
    assert target.writes == []
    assert len(target.preparations) == len(service.attempts(job["job_id"])) == 2
    assert asyncio.run(service.run_pending(adapters={"douyin": target}, max_attempts=2)) == []
    assert asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"], max_attempts=2)) == results[-1]


@pytest.mark.parametrize("platform_result", ["ACCEPTED", "IN_REVIEW", "PUBLISHED", "REJECTED", "DRAFT"])
def test_remote_status_is_reported_without_treating_rejection_or_draft_as_retryable(tmp_path, platform_result):
    service, job = make_delivery(tmp_path)
    target = PublishingTarget(service, platform_result=platform_result)
    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert result is not None
    assert result["platform_result"] == platform_result
    assert result["submission"] == "CONFIRMED"
    assert asyncio.run(service.run_pending(adapters={"douyin": target})) == []
    assert len(target.writes) == 1


@pytest.mark.parametrize("problem", ["multiple", "identity", "metadata", "media", "old_work", "url_only", "unavailable"])
def test_insufficient_or_ambiguous_work_evidence_stays_unknown(tmp_path, problem):
    service, job = make_delivery(tmp_path)

    class UncertainTarget(PublishingTarget):
        async def reconcile(self, context):
            evidence = await super().reconcile(context)
            work = dict(evidence[0])
            if problem == "multiple":
                return [work, {**work, "content_id": "another-work"}]
            if problem == "unavailable":
                raise ConnectionError("Read-only work lookup failed")
            changes = {
                "identity": {"account_id": "someone-else"},
                "metadata": {"metadata": {"title": "Original title"}},
                "media": {"media_sha256": ["0" * 64]},
                "old_work": {"created_at": "2020-01-01T00:00:00Z"},
                "url_only": {"source": "url_redirect"},
            }
            return [{**work, **changes[problem]}]

    target = UncertainTarget(service, disconnect=True)
    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert result is not None
    assert result["status"] == "UNKNOWN_COMMIT"
    assert result["submission"] == "MAY_HAVE_SUBMITTED"
    assert result["platform_result"] is result["content_id"] is result["verified_at"] is None
    assert asyncio.run(service.run_pending(adapters={"douyin": target})) == []
    assert len(target.writes) == 1
    assert service.status(job["job_id"]) == result


@pytest.mark.parametrize("code", ["AUTH_REQUIRED", "IDENTITY_MISMATCH", "UI_CHANGED", "DECLARATION_MISMATCH"])
def test_nontransient_pre_submit_errors_never_enter_retry_loop(tmp_path, code):
    service, job = make_delivery(tmp_path)

    class BlockedTarget(PublishingTarget):
        async def prepare(self, context):
            self.preparations.append(context.job_id)
            raise TaskError(code, "Preparation cannot safely continue")

    target = BlockedTarget(service)
    results = asyncio.run(service.run_pending(adapters={"douyin": target}, max_attempts=5))
    assert len(results) == 1
    assert results[0]["submission"] == "NOT_SUBMITTED"
    assert results[0]["error"]["code"] == code
    assert len(target.preparations) == len(service.attempts(job["job_id"])) == 1
    assert target.writes == []


def test_auth_failure_during_read_only_reconciliation_pauses_without_resubmission(tmp_path):
    service, job = make_delivery(tmp_path)

    class ExpiredTarget(PublishingTarget):
        async def reconcile(self, context):
            raise TaskError("AUTH_REQUIRED", "Read-only session requires authentication")

    target = ExpiredTarget(service)
    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert result is not None
    assert result["status"] == "UNKNOWN_COMMIT"
    assert any(item["code"] == "AUTH_REQUIRED" for item in result["warnings"])
    assert next(account for account in service.accounts() if account["platform"] == "douyin")["status"] == "AUTH_REQUIRED"
    repeated = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert repeated == result
    assert len(target.writes) == 1


def test_transient_label_after_submission_never_enables_retry(tmp_path):
    service, job = make_delivery(tmp_path)

    class LostTarget(PublishingTarget):
        async def submit(self, context):
            await super().submit(context)
            raise TaskError("TRANSIENT_PRE_SUBMIT", "Misclassified error after final action")

    target = LostTarget(service, visible=False)
    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert result is not None
    assert result["status"] == "UNKNOWN_COMMIT"
    assert asyncio.run(service.run_pending(adapters={"douyin": target}, max_attempts=5)) == []
    assert len(target.writes) == 1


def test_boundary_persistence_failure_prevents_the_remote_action(tmp_path):
    service, job = make_delivery(tmp_path)
    # External disk/transaction fault at the durable boundary, using real SQLite.
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as connection:
        connection.execute("""CREATE TRIGGER unavailable_boundary BEFORE UPDATE ON jobs
            WHEN NEW.status = 'SUBMITTING'
            BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
    target = PublishingTarget(service)
    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["submission"] == "NOT_SUBMITTED"
    assert result["error"]["code"] == "STORAGE_ERROR"
    assert target.writes == []
    assert "SUBMITTING" not in [event["status"] for event in service.events(job["job_id"])]


def test_transient_failure_then_recovery_delivers_once(tmp_path):
    service, job = make_delivery(tmp_path)

    class BrieflyUnavailableTarget(PublishingTarget):
        async def prepare(self, context):
            first = not self.preparations
            metadata = await super().prepare(context)
            if first:
                raise TaskError("TRANSIENT_PRE_SUBMIT", "Upload endpoint temporarily unavailable")
            return metadata

    target = BrieflyUnavailableTarget(service)
    results = asyncio.run(service.run_pending(adapters={"douyin": target}, max_attempts=2))
    assert [result["status"] for result in results] == ["FAILED_PRE_SUBMIT", "COMPLETED"]
    assert len(target.writes) == 1
    assert len(service.attempts(job["job_id"])) == 2
    assert results[-1]["error"] is None


def test_version_one_database_remains_queryable_and_accepts_new_delivery(tmp_path):
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as connection:
        connection.executescript("""
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY, platform TEXT NOT NULL, account TEXT NOT NULL,
                delivery_key TEXT NOT NULL, request_digest TEXT NOT NULL, manifest_json TEXT NOT NULL,
                attempt_id TEXT, status TEXT NOT NULL, submission TEXT NOT NULL, platform_result TEXT,
                content_id TEXT, verified_at TEXT, actual_metadata_json TEXT, warnings_json TEXT NOT NULL,
                error_json TEXT, evidence_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(platform, account, delivery_key));
            CREATE TABLE attempts (
                attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
                status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, owner_token TEXT,
                UNIQUE(job_id, attempt_id));
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(job_id),
                attempt_id TEXT, status TEXT NOT NULL, stage TEXT NOT NULL, created_at TEXT NOT NULL, details_json TEXT NOT NULL);
            CREATE TABLE accounts (
                platform TEXT NOT NULL, account TEXT NOT NULL, expected_id TEXT, session_path TEXT,
                status TEXT NOT NULL, pause_reason TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(platform, account));
            INSERT INTO jobs VALUES ('old-job', 'douyin', 'creator', 'old', 'old-digest', '{}', 'old-attempt',
                'READY', 'NOT_SUBMITTED', NULL, NULL, NULL, '{}', '[]', NULL, '[]',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            INSERT INTO attempts VALUES ('old-attempt', 'old-job', 'READY', '2026-01-01T00:00:00Z',
                '2026-01-01T00:00:01Z', 'old-owner');
            PRAGMA user_version = 1;
        """)
    service, job = make_delivery(tmp_path)
    old = service.status("old-job")
    assert old["schema_version"] == 1
    assert old["status"] == "READY"
    assert service.attempts("old-job")[0]["submission_started_at"] is None
    target = PublishingTarget(service)
    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))
    assert result is not None and result["status"] == "COMPLETED"
    assert service.status("old-job") == old


def test_finished_preparation_remains_queryable_without_an_adapter(tmp_path):
    service, job = make_delivery(tmp_path)

    class PreparationOnlyTarget:
        async def prepare(self, context):
            return {"title": context.manifest["title"]}

        async def close(self):
            pass

    prepared = asyncio.run(service.run_once(adapters={"douyin": PreparationOnlyTarget()}, job_id=job["job_id"]))
    assert prepared is not None and prepared["status"] == "READY"
    assert asyncio.run(service.run_once(adapters={}, job_id=job["job_id"])) == prepared

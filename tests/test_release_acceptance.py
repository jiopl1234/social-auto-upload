"""Cross-feature release acceptance: local DOM, real SQLite, no platform accounts."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator.service import TaskService
from tests.platform_fixture import CreatorSite, fixture_adapter
from tests.task_fixtures import EXPECTED_DELIVERY_PATHS, make_manifest, make_service
from tests.test_job_cli import run_jobs


def test_capabilities_separate_offline_evidence_from_unverified_production(tmp_path):
    completed, report = run_jobs(tmp_path / "jobs.sqlite3", "capabilities")
    assert completed.returncode == 0
    assert {(p["platform"], p["content_type"]) for p in report["capabilities"]} == set(EXPECTED_DELIVERY_PATHS)
    for path in report["capabilities"]:
        assert path["offline_verified"] is True
        assert path["offline_evidence"] == "synthetic_dom_and_task_protocol"
        assert path["production_verified"] is False
        if (path["platform"], path["content_type"]) == ("douyin", "video"):
            assert path["can_execute"] is True
            assert path["status"] == "available_limited"
            assert path["live_evidence_verified"] is True
            assert path["missing_evidence"] == ["cli_end_to_end_live_submission"]
            continue
        assert path["status"] == "restricted"
        assert path["missing_evidence"] == ["stable_account_id", "per_asset_processing", "work_detail_readback"]


@pytest.mark.parametrize("platform,kind,cancel", [(p, k, False) for p, k in EXPECTED_DELIVERY_PATHS] +
                         [(p, "video", True) for p in ("douyin", "xiaohongshu", "tencent")])
def test_delayed_response_and_cancel_preserve_one_delivery(tmp_path, platform, kind, cancel):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, platform=platform, content_type=kind))
    with CreatorSite(platform, mode="delayed-response") as site:
        async def exercise():
            running = asyncio.create_task(service.run_once(adapters={platform: fixture_adapter(service, site)},
                job_id=job["job_id"], limits={"total": 20, "cleanup": 3}))
            async def wait_for_write():
                while not site.submissions:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_for_write(), timeout=10)
            assert service.status(job["job_id"])["status"] == "SUBMITTING"
            assert service.attempts(job["job_id"])[0]["submit_count"] == 1
            if cancel:
                running.cancel()
            else:
                site.response_gate.set()
            result = await asyncio.wait_for(running, timeout=6)
            site.response_gate.set()
            return result
        result = asyncio.run(exercise())
        assert result is not None and result["status"] == ("UNKNOWN_COMMIT" if cancel else "COMPLETED")
        restarted = TaskService(service.store.path, runtime_dir=service.runtime_dir, media_roots=[tmp_path / "media"])
        recovered = asyncio.run(restarted.run_once(adapters={platform: fixture_adapter(restarted, site)}, job_id=job["job_id"]))
        assert recovered is not None and recovered["status"] == "COMPLETED"
        assert recovered["attempt_id"] == result["attempt_id"]
        assert len(site.submissions) == 1
        assert site.submissions[0]["account_id"] == f"{platform}:creator"
        assert site.submissions[0]["content_type"] == kind
        assert site.submissions[0]["metadata"]["body"] == "Original body"


def test_scheduled_fanout_survives_partial_failure_and_restart_without_resending(tmp_path):
    baseline = make_service(tmp_path)
    due = datetime.now(timezone.utc).replace(microsecond=0)
    now = [due - timedelta(seconds=1)]
    service = TaskService(baseline.store.path, runtime_dir=baseline.runtime_dir, media_roots=[tmp_path / "media"],
                          clock=lambda: now[0])
    request = make_manifest(tmp_path, key="scheduled-release", run_at={
        "local": due.replace(tzinfo=None).isoformat(), "timezone": "UTC"})
    request.pop("platform")
    request.pop("account")
    request["targets"] = [{"platform": p, "account": "creator"} for p in ("douyin", "xiaohongshu", "tencent")]
    batch = service.enqueue_batch(request)
    with CreatorSite("douyin") as douyin, CreatorSite("xiaohongshu", mode="auth") as xhs, \
            CreatorSite("tencent", mode="lost-response") as tencent:
        tencent.hide_works = True
        sites = [douyin, xhs, tencent]
        adapters = {site.platform: fixture_adapter(service, site) for site in sites}
        assert asyncio.run(service.run_pending(adapters=adapters)) == []
        assert all(service.attempts(t["job_id"]) == [] for t in batch["targets"])
        now[0] = due
        results = asyncio.run(service.run_pending(adapters=adapters))
        assert {r["platform"]: r["status"] for r in results} == {
            "douyin": "COMPLETED", "xiaohongshu": "BLOCKED", "tencent": "UNKNOWN_COMMIT"}
        restarted = TaskService(service.store.path, runtime_dir=service.runtime_dir,
                                media_roots=[tmp_path / "media"], clock=lambda: now[0])
        tencent.hide_works = False
        unknown = next(r for r in results if r["platform"] == "tencent")
        recovered = asyncio.run(restarted.run_once(adapters={"tencent": fixture_adapter(restarted, tencent)},
                                                  job_id=unknown["job_id"]))
        assert recovered is not None and recovered["status"] == "COMPLETED"
        assert recovered["attempt_id"] == unknown["attempt_id"]
        repeated = restarted.enqueue_batch(request)
        assert repeated["batch_id"] == batch["batch_id"]
        assert asyncio.run(restarted.run_pending(adapters=adapters)) == []
        assert [len(site.submissions) for site in sites] == [1, 0, 1]
        assert {t["platform"]: t["status"] for t in restarted.batch_status(batch["batch_id"])["targets"]} == {
            "douyin": "COMPLETED", "xiaohongshu": "BLOCKED", "tencent": "COMPLETED"}

"""Platform integration at the approved public TaskService boundary."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock

from tests.task_fixtures import EXPECTED_DELIVERY_PATHS, make_manifest, make_service


@pytest.mark.parametrize("platform,content_type", EXPECTED_DELIVERY_PATHS)
def test_post_submit_challenge_without_receipt_pauses_account_and_never_retries(tmp_path, platform, content_type):
    from orchestrator.service import TaskService
    from tests.platform_fixture import CreatorSite, fixture_adapter

    service = make_service(tmp_path)
    first = service.enqueue(make_manifest(tmp_path, platform=platform, content_type=content_type, key="first"))
    second = service.enqueue(make_manifest(tmp_path, platform=platform, content_type=content_type, key="second"))
    with CreatorSite(platform, mode="auth-after-submit-no-receipt") as site:
        result = asyncio.run(service.run_once(
            adapters={platform: fixture_adapter(service, site)}, job_id=first["job_id"],
            limits={"total": 15, "submit": 2, "reconcile": 2, "cleanup": 3}))
        assert result is not None
        assert result["status"] == "UNKNOWN_COMMIT"
        assert result["submission"] == "MAY_HAVE_SUBMITTED"
        assert result["error"]["code"] == "AUTH_REQUIRED"
        assert len(site.submissions) == 1
        assert site.queries == 0
        assert next(a for a in service.accounts() if a["platform"] == platform)["status"] == "AUTH_REQUIRED"

        restarted = TaskService(service.store.path, runtime_dir=service.runtime_dir,
                                media_roots=[tmp_path / "media"])
        recovered = asyncio.run(restarted.run_once(
            adapters={platform: fixture_adapter(restarted, site)}, job_id=first["job_id"]))
        next_job = asyncio.run(restarted.run_once(
            adapters={platform: fixture_adapter(restarted, site)}, job_id=second["job_id"]))
        assert recovered is not None and recovered["status"] == "UNKNOWN_COMMIT"
        assert next_job is not None and next_job["status"] == "QUEUED"
        assert len(site.submissions) == 1
        assert site.queries == 0


@pytest.mark.parametrize("platform,content_type", EXPECTED_DELIVERY_PATHS)
def test_post_submit_challenge_wins_when_receipt_is_also_available(tmp_path, platform, content_type):
    from tests.platform_fixture import CreatorSite, fixture_adapter

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, platform=platform, content_type=content_type))
    with CreatorSite(platform, mode="auth-with-receipt") as site:
        result = asyncio.run(service.run_once(
            adapters={platform: fixture_adapter(service, site)}, job_id=job["job_id"],
            limits={"total": 15, "submit": 2, "reconcile": 2, "cleanup": 3}))
        assert result is not None and result["status"] == "UNKNOWN_COMMIT"
        assert result["submission"] == "MAY_HAVE_SUBMITTED"
        assert result["error"]["code"] == "AUTH_REQUIRED"
        assert next(a for a in service.accounts() if a["platform"] == platform)["status"] == "AUTH_REQUIRED"
        assert len(site.submissions) == 1
        assert site.queries == 0


def test_challenge_on_editor_is_seen_before_readonly_navigation(tmp_path):
    from tests.platform_fixture import CreatorSite, fixture_adapter

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, platform="tencent"))
    with CreatorSite("tencent", mode="auth-before-reconcile") as site:
        result = asyncio.run(service.run_once(
            adapters={"tencent": fixture_adapter(service, site)}, job_id=job["job_id"],
            limits={"total": 15, "submit": 2, "reconcile": 2, "cleanup": 3}))
        assert result is not None and result["status"] == "UNKNOWN_COMMIT"
        assert any(item["code"] == "AUTH_REQUIRED" for item in result["warnings"])
        assert next(a for a in service.accounts() if a["platform"] == "tencent")["status"] == "AUTH_REQUIRED"
        assert len(site.submissions) == 1
        assert site.queries == 0


def test_cancel_resistant_receipt_observer_keeps_account_fenced(tmp_path, caplog):
    from orchestrator.errors import TaskError
    from tests.platform_fixture import CreatorSite, fixture_adapter

    service = make_service(tmp_path)
    first = service.enqueue(make_manifest(tmp_path, platform="tencent", key="first"))
    second = service.enqueue(make_manifest(tmp_path, platform="tencent", key="second"))
    with CreatorSite("tencent", mode="auth-after-submit-no-receipt-slow-cancel") as site:
        result = asyncio.run(service.run_once(
            adapters={"tencent": fixture_adapter(service, site)}, job_id=first["job_id"],
            limits={"total": 15, "submit": 2, "reconcile": 2, "cleanup": 3}))
        assert result is not None and result["status"] == "UNKNOWN_COMMIT"
        assert result["error"]["code"] == "AUTH_REQUIRED"
        assert any(item["code"] == "RESOURCES_UNCONFIRMED" for item in result["warnings"])
        assert next(a for a in service.accounts() if a["platform"] == "tencent")["status"] == "AUTH_REQUIRED"
        with pytest.raises(TaskError) as blocked:
            asyncio.run(service.run_once(adapters={"tencent": fixture_adapter(service, site)}, job_id=second["job_id"]))
        assert blocked.value.code in {"ACCOUNT_BUSY", "RESOURCES_UNCONFIRMED"}
        assert service.status(second["job_id"])["status"] == "QUEUED"
        assert len(site.submissions) == 1
        assert site.queries == 0
        assert "Task exception was never retrieved" not in caplog.text
        assert "private browser diagnostic" not in caplog.text


def test_receipt_exception_after_challenge_is_consumed(tmp_path, caplog):
    from tests.platform_fixture import CreatorSite, fixture_adapter

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, platform="tencent"))
    with CreatorSite("tencent", mode="auth-after-submit-no-receipt-raises-on-cancel") as site:
        result = asyncio.run(service.run_once(
            adapters={"tencent": fixture_adapter(service, site)}, job_id=job["job_id"],
            limits={"total": 15, "submit": 2, "cleanup": 3}))
        assert result is not None and result["status"] == "UNKNOWN_COMMIT"
        assert result["error"]["code"] == "AUTH_REQUIRED"
        assert len(site.submissions) == 1
        assert "Task exception was never retrieved" not in caplog.text
        assert "private browser diagnostic" not in caplog.text


def test_unverified_live_video_paths_stop_before_browser_or_submission(tmp_path):
    from orchestrator.platforms import capability_report, default_adapters

    service = make_service(tmp_path)
    report = capability_report()
    for platform in ("douyin", "xiaohongshu", "tencent"):
        capability = next(item for item in report if item["platform"] == platform and item["content_type"] == "video")
        assert capability["production_verified"] is False
        assert capability["status"] == "restricted"
        job = service.enqueue(make_manifest(tmp_path, platform=platform))
        result = asyncio.run(service.run_once(adapters=default_adapters(service.runtime_dir), job_id=job["job_id"]))
        assert result is not None
        assert result["status"] == "FAILED_PRE_SUBMIT"
        assert result["error"]["code"] == "UNSUPPORTED"
        assert service.attempts(job["job_id"])[0]["submit_count"] == 0
        assert not any(event["stage"] == "runtime" for event in service.events(job["job_id"]))


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu", "tencent"])
def test_video_delivery_reads_actual_dom_and_queries_independent_work_store(tmp_path, platform):
    from tests.platform_fixture import CreatorSite, fixture_adapter

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, platform=platform, tags=["旅行"],
                                       declarations={"ownership": "original", "ai_usage": "none"}))
    with CreatorSite(platform) as site:
        result = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site)}, job_id=job["job_id"],
                                             limits={"total": 20, "cleanup": 3, "processing": 2, "reconcile": 4, "submit": 3}))
        assert result is not None
        assert result["status"] == "COMPLETED"
        assert result["platform_result"] == "PUBLISHED"
        assert len(site.submissions) == 1
        assert site.queries == 1
        assert result["actual_metadata"]["body"] == "Original body"
        assert result["actual_metadata"]["tags"] == ["旅行"]
        assert result["actual_metadata"]["declarations"] == {"ownership": "original", "ai_usage": "none"}


@pytest.mark.parametrize("platform,mode,code", [
    ("douyin", "wrong-account", "IDENTITY_MISMATCH"),
    ("douyin", "blank", "UI_CHANGED"),
    ("douyin", "auth-after-fields", "AUTH_REQUIRED"),
    ("douyin", "processing", "TIMEOUT"),
    ("xiaohongshu", "auth-after-upload", "AUTH_REQUIRED"),
    ("xiaohongshu", "wrong-topic", "METADATA_MISMATCH"),
    ("xiaohongshu", "required-declaration", "INVALID_INPUT"),
    ("tencent", "auth", "AUTH_REQUIRED"),
    ("tencent", "unknown-dialog", "UI_CHANGED"),
    ("tencent", "truncated-title", "METADATA_MISMATCH"),
])
def test_video_page_failure_never_submits(tmp_path, platform, mode, code):
    from tests.platform_fixture import CreatorSite, fixture_adapter

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, platform=platform, tags=["旅行"]))
    with CreatorSite(platform, mode=mode) as site:
        result = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site)}, job_id=job["job_id"],
                                             limits={"total": 15, "cleanup": 3, "processing": 0.5}))
        assert result is not None
        assert result["error"]["code"] == code
        assert result["submission"] == "NOT_SUBMITTED"
        assert site.submissions == []
        if code in {"IDENTITY_MISMATCH", "AUTH_REQUIRED", "UI_CHANGED"}:
            assert result["status"] == "BLOCKED"
            assert next(a for a in service.accounts() if a["platform"] == platform)["status"] != "READY"


@pytest.mark.parametrize("platform,mode,status", [
    ("douyin", "lost-response", "COMPLETED"),
    ("xiaohongshu", "ambiguous", "UNKNOWN_COMMIT"),
    ("tencent", "false-success", "UNKNOWN_COMMIT"),
])
def test_response_loss_and_false_success_use_work_evidence_once(tmp_path, platform, mode, status):
    from tests.platform_fixture import CreatorSite, fixture_adapter

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, platform=platform))
    with CreatorSite(platform, mode=mode) as site:
        result = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site)}, job_id=job["job_id"],
                                             limits={"total": 20, "cleanup": 3, "reconcile": 4, "submit": 3}))
        assert result is not None
        assert result["status"] == status
        assert len(site.submissions) == 1
        if status == "UNKNOWN_COMMIT":
            recovered = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site)}, job_id=job["job_id"],
                                                      limits={"total": 20, "cleanup": 3, "reconcile": 4}))
            assert recovered is not None
            assert recovered["status"] == "UNKNOWN_COMMIT"
            assert len(site.submissions) == 1


def test_legacy_video_calls_cannot_bypass_task_protocol():
    from orchestrator.errors import TaskError
    from uploader.douyin_uploader.main import DouYinVideo
    from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo
    from uploader.tencent_uploader.main import TencentVideo

    for cls in (DouYinVideo, XiaoHongShuVideo, TencentVideo):
        uploader = cls("title", "video.mp4", [], 0, "session.json")
        with pytest.raises(TaskError) as error:
            asyncio.run(uploader.upload(MagicMock()))
        assert error.value.code == "UNSUPPORTED"


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu", "tencent"])
def test_explicit_ai_rights_cover_and_collection_read_back_without_defaults(tmp_path, platform):
    from tests.platform_fixture import CreatorSite, fixture_adapter
    from tests.task_fixtures import IMAGE_BYTES

    service = make_service(tmp_path)
    cover = tmp_path / "media" / "cover.png"
    cover.write_bytes(IMAGE_BYTES)
    job = service.enqueue(make_manifest(tmp_path, platform=platform, cover=str(cover),
        collection="Series A" if platform != "xiaohongshu" else None,
        facts={"ownership": "original", "ai_usage": "generated", "repost_source": None},
        declarations={"ownership": "original", "ai_usage": "generated", "originality_rights": True}))
    with CreatorSite(platform, result="IN_REVIEW") as site:
        result = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site)}, job_id=job["job_id"],
                                             limits={"total": 20, "cleanup": 3, "metadata": 6, "reconcile": 4}))
        assert result is not None
        assert result["platform_result"] == "IN_REVIEW"
        assert result["actual_metadata"]["declarations"]["ai_usage"] == "generated"
        assert result["actual_metadata"]["declarations"]["originality_rights"] is True
        assert result["actual_metadata"]["cover"]["sha256"] == job["manifest"]["cover"]["sha256"]
        assert result["actual_metadata"]["collection"] == ("Series A" if platform != "xiaohongshu" else None)
        assert len(site.submissions) == 1


@pytest.mark.parametrize("platform,mutation,code", [
    ("douyin", "document.querySelector('#identity').dataset.accountId='switched'", "IDENTITY_MISMATCH"),
    ("xiaohongshu", "document.querySelector('#title').value='changed after preparation'", "METADATA_MISMATCH"),
    ("tencent", "document.body.insertAdjacentHTML('beforeend', '<div data-challenge=auth>实名验证</div>')", "AUTH_REQUIRED"),
])
def test_final_readback_failure_stays_definitely_unsubmitted(tmp_path, platform, mutation, code):
    from tests.platform_fixture import CreatorSite, fixture_adapter

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, platform=platform))
    with CreatorSite(platform) as site:
        result = asyncio.run(service.run_once(
            adapters={platform: fixture_adapter(service, site, mutate_after_save=mutation)}, job_id=job["job_id"],
            limits={"total": 20, "cleanup": 3}))
        assert result is not None and result["error"]["code"] == code
        assert result["submission"] == "NOT_SUBMITTED"
        assert service.attempts(job["job_id"])[0]["submit_count"] == 0
        assert site.submissions == []

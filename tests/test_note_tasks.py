"""Image-note delivery uses the same public task protocol as video."""
import asyncio
from pathlib import Path

import pytest

from tests.platform_fixture import CreatorSite, fixture_adapter
from tests.task_fixtures import IMAGE_BYTES, make_manifest, make_service


def note_manifest(tmp_path, platform, **overrides):
    images = []
    for index in range(3):
        path = tmp_path / "media" / f"image-{index}.png"
        path.write_bytes(IMAGE_BYTES + bytes([index]))
        images.append(str(path))
    return make_manifest(tmp_path, platform=platform, content_type="note", **{
        "media": images, "title": "Explicit title", "body": "完整图文正文，不截断。", "tags": ["旅行"],
        "declarations": {"ownership": "original", "ai_usage": "none"}, **overrides})


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu"])
def test_note_delivers_all_ordered_images_and_explicit_content(tmp_path, platform):
    service = make_service(tmp_path)
    job = service.enqueue(note_manifest(tmp_path, platform))
    with CreatorSite(platform) as site:
        result = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site)},
                                             job_id=job["job_id"], limits={"total": 20, "cleanup": 3}))
        assert result is not None and result["status"] == "COMPLETED"
        assert len(site.submissions) == 1
        actual = site.submissions[0]
        assert actual["content_type"] == "note"
        assert actual["metadata"]["title"] == "Explicit title"
        assert actual["metadata"]["body"] == "完整图文正文，不截断。"
        assert actual["metadata"]["tags"] == ["旅行"]
        assert actual["metadata"]["declarations"] == {"ownership": "original", "ai_usage": "none"}
        assert actual["metadata"]["media"] == [{"kind": "image", "sha256": i["sha256"]} for i in job["manifest"]["media"]]
        assert result["actual_metadata"] == actual["metadata"]
        assert service.attempts(job["job_id"])[0]["submit_count"] == 1


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu"])
@pytest.mark.parametrize("mode,code", [("partial", "UPLOAD_FAILED"), ("missing-image", "TIMEOUT"),
                                       ("reordered-images", "METADATA_MISMATCH")])
def test_title_editor_does_not_replace_positive_evidence_for_every_image(tmp_path, platform, mode, code):
    service = make_service(tmp_path)
    job = service.enqueue(note_manifest(tmp_path, platform))
    with CreatorSite(platform, mode=mode) as site:
        result = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site)},
            job_id=job["job_id"], limits={"total": 15, "cleanup": 3, "processing": 0.5}))
        assert result is not None and result["error"]["code"] == code
        assert result["submission"] == "NOT_SUBMITTED"
        assert service.attempts(job["job_id"])[0]["submit_count"] == 0
        assert site.submissions == []


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu"])
def test_lost_note_receipt_restarts_with_read_only_note_query_without_media(tmp_path, platform):
    from orchestrator.service import TaskService

    service = make_service(tmp_path)
    job = service.enqueue(note_manifest(tmp_path, platform))
    with CreatorSite(platform, mode="lost-response") as site:
        site.hide_works = True
        first = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site)},
                                             job_id=job["job_id"]))
        assert first is not None and first["status"] == "UNKNOWN_COMMIT"
        for item in job["manifest"]["media"]:
            Path(item["path"]).unlink()
        restarted = TaskService(service.store.path, runtime_dir=service.runtime_dir, media_roots=[tmp_path / "media"])
        site.hide_works = False
        result = asyncio.run(restarted.run_once(adapters={platform: fixture_adapter(restarted, site)},
                                               job_id=job["job_id"]))
        assert result is not None and result["status"] == "COMPLETED"
        assert result["attempt_id"] == first["attempt_id"]
        assert len(site.submissions) == 1
        assert site.query_kinds == ["note", "note"]
        assert restarted.attempts(job["job_id"])[0]["submit_count"] == 1


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu"])
@pytest.mark.parametrize("mode,code", [("auth", "AUTH_REQUIRED"), ("auth-after-upload", "AUTH_REQUIRED"),
                                       ("auth-after-fields", "AUTH_REQUIRED"), ("wrong-topic", "METADATA_MISMATCH"),
                                       ("required-declaration", "INVALID_INPUT")])
def test_note_challenges_and_required_content_fail_before_submission(tmp_path, platform, mode, code):
    service = make_service(tmp_path)
    job = service.enqueue(note_manifest(tmp_path, platform, **({"declarations": {}} if mode == "required-declaration" else {})))
    with CreatorSite(platform, mode=mode) as site:
        result = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site)},
                                             job_id=job["job_id"], limits={"total": 15, "cleanup": 3}))
        assert result is not None and result["error"]["code"] == code
        assert result["submission"] == "NOT_SUBMITTED"
        assert site.submissions == []


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu"])
def test_note_cancellation_during_delayed_receipt_cleans_and_never_republishes(tmp_path, platform):
    service = make_service(tmp_path)
    job = service.enqueue(note_manifest(tmp_path, platform))
    with CreatorSite(platform, mode="delayed-response") as site:
        async def exercise():
            running = asyncio.create_task(service.run_once(adapters={platform: fixture_adapter(service, site)},
                job_id=job["job_id"], limits={"total": 20, "cleanup": 3}))
            async def wait_for_write():
                while not site.submissions:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_for_write(), timeout=10)
            running.cancel()
            result = await asyncio.wait_for(running, timeout=5)
            site.response_gate.set()
            return result
        first = asyncio.run(exercise())
        assert first is not None and first["status"] == "UNKNOWN_COMMIT"
        assert first["error"]["code"] == "CANCELLED"
        result = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site)}, job_id=job["job_id"]))
        assert result is not None and result["status"] == "COMPLETED"
        assert len(site.submissions) == 1


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu"])
def test_note_final_challenge_is_unsubmitted_and_pauses_account(tmp_path, platform):
    service = make_service(tmp_path)
    job = service.enqueue(note_manifest(tmp_path, platform))
    with CreatorSite(platform) as site:
        adapter = fixture_adapter(service, site, mutate_after_save=(
            "document.body.insertAdjacentHTML('beforeend', '<div data-challenge=auth>安全验证</div>')"))
        result = asyncio.run(service.run_once(adapters={platform: adapter}, job_id=job["job_id"]))
        assert result is not None and result["status"] == "BLOCKED"
        assert result["error"]["code"] == "AUTH_REQUIRED"
        assert result["submission"] == "NOT_SUBMITTED"
        assert site.submissions == []


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu"])
def test_partial_note_upload_keeps_primary_failure_when_cleanup_also_fails(tmp_path, platform):
    service = make_service(tmp_path)
    job = service.enqueue(note_manifest(tmp_path, platform))
    with CreatorSite(platform, mode="partial") as site:
        result = asyncio.run(service.run_once(adapters={platform: fixture_adapter(service, site, cleanup_error=True)},
                                             job_id=job["job_id"]))
        assert result is not None and result["status"] == "BLOCKED"
        assert result["error"]["code"] == "UPLOAD_FAILED"
        assert any(w["code"] == "CLEANUP_FAILED" for w in result["warnings"])
        assert next(a for a in service.accounts() if a["platform"] == platform)["status"] != "READY"
        assert site.submissions == []

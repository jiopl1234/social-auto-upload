"""Real Douyin protocol exercised at the public service/local browser seam."""
import asyncio
from datetime import datetime, timezone
import pytest

from tests.task_fixtures import make_manifest, make_service, readback_metadata


def test_receipted_douyin_work_uses_independent_fields_without_inventing_source_hash(tmp_path):
    from orchestrator.platforms.douyin import DouyinVideoPort

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))

    class Target:
        async def prepare(self, context):
            context.store.record_video_upload(context.job_id, context.lease.token,
                video_id="fixture-video", sha256=context.manifest["media"][0]["sha256"])
            return readback_metadata(context.manifest)

        async def submit(self, context):
            return {"content_id": "7690181573743742254"}

        async def reconcile(self, context):
            return DouyinVideoPort.work_observations({"status_code": 0, "aweme_list": [{
                "aweme_id": "7690181573743742254", "aweme_type": 4,
                "author": {"uid": "douyin:creator"},
                "create_time": int(datetime.now(timezone.utc).timestamp()),
                "item_title": "Original title", "desc": "Original title Original body",
                "text_extra": [], "video": {"vid": "fixture-video", "width": 720, "height": 1280},
                "status": {"is_delete": False, "is_prohibited": False, "in_reviewing": False,
                           "is_private": False, "private_status": 0, "self_see": False},
            }]})

        async def close(self):
            pass

    result = asyncio.run(service.run_once(adapters={"douyin": Target()}, job_id=job["job_id"]))
    assert result is not None
    assert result["status"] == "COMPLETED"
    assert result["platform_result"] == "PUBLISHED"
    assert service.attempts(job["job_id"])[0]["submit_count"] == 1


def test_real_port_delivers_once_through_browser_and_durable_task_protocol(tmp_path):
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with DouyinSite() as site:
        result = asyncio.run(service.run_once(adapters={"douyin": site.adapter(service)}, job_id=job["job_id"],
            limits={"total": 25, "cleanup": 4, "processing": 3, "submit": 2}))
        assert result is not None
        assert result["status"] == "COMPLETED", (result["error"], result["warnings"])
        assert result["platform_result"] == "PUBLISHED"
        assert site.submissions == [{"title": "Original title", "body": "Original body"}]
        assert service.attempts(job["job_id"])[0]["receipt_id"] == "7690181573743742254"


def test_prepare_only_checks_live_controls_without_crossing_submission_boundary(tmp_path):
    from orchestrator.platforms.base import PreparationOnlyAdapter
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with DouyinSite() as site:
        adapter = PreparationOnlyAdapter(site.adapter(service))
        result = asyncio.run(service.run_once(adapters={"douyin": adapter}, job_id=job["job_id"]))
        assert result is not None
        assert result["status"] == "READY"
        assert result["submission"] == "NOT_SUBMITTED"
        assert result["actual_metadata"]["title"] == "Original title"
        assert site.submissions == []
        assert service.attempts(job["job_id"])[0]["submit_count"] == 0

        # Preparation is deliberately closed, so a later executor must create
        # a fresh preparation attempt before delivering the same durable job.
        from orchestrator.service import TaskService
        restarted = TaskService(service.store.path, runtime_dir=service.runtime_dir, media_roots=[tmp_path / "media"])
        limited = asyncio.run(restarted.run_once(adapters={"douyin": site.adapter(restarted)},
            job_id=job["job_id"], max_attempts=1))
        assert limited is not None and limited["status"] == "READY"
        assert len(restarted.attempts(job["job_id"])) == 1
        result = asyncio.run(restarted.run_once(adapters={"douyin": site.adapter(restarted)}, job_id=job["job_id"]))
        assert result is not None and result["status"] == "COMPLETED", result
        assert len(site.submissions) == 1
        assert [attempt["submit_count"] for attempt in restarted.attempts(job["job_id"])] == [0, 1]


@pytest.mark.parametrize("mode,status,code", [
    ("wrong-account", "BLOCKED", "IDENTITY_MISMATCH"),
    ("processing", "FAILED_PRE_SUBMIT", "TIMEOUT"),
    ("challenge", "UNKNOWN_COMMIT", "AUTH_REQUIRED"),
    ("lost-receipt", "UNKNOWN_COMMIT", "SUBMISSION_UNCERTAIN"),
    ("replaced-preview", "FAILED_PRE_SUBMIT", "UPLOAD_FAILED"),
    ("missing-covers", "FAILED_PRE_SUBMIT", "METADATA_MISMATCH"),
    ("submit-auth", "UNKNOWN_COMMIT", "AUTH_REQUIRED"),
    ("worklist-auth", "UNKNOWN_COMMIT", "AUTH_REQUIRED"),
    ("removed-cover-after-ready", "FAILED_PRE_SUBMIT", "METADATA_MISMATCH"),
    ("changed-cover-after-ready", "FAILED_PRE_SUBMIT", "METADATA_MISMATCH"),
])
def test_real_port_failure_preserves_submission_and_account_state(tmp_path, mode, status, code):
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    first = service.enqueue(make_manifest(tmp_path))
    second = service.enqueue(make_manifest(tmp_path, key="second"))
    with DouyinSite(mode) as site:
        result = asyncio.run(service.run_once(adapters={"douyin": site.adapter(service)}, job_id=first["job_id"],
            limits={"total": 25, "cleanup": 4, "processing": 1, "submit": 2, "reconcile": 2}))
        assert result is not None
        assert result["status"] == status, result["error"]
        diagnostics = ([result["error"]] if result["error"] else []) + result["warnings"]
        assert code in {item["code"] for item in diagnostics}
        assert len(site.submissions) == (1 if status == "UNKNOWN_COMMIT" else 0)
        if code in {"AUTH_REQUIRED", "IDENTITY_MISMATCH"}:
            next_result = asyncio.run(service.run_once(adapters={"douyin": site.adapter(service)}, job_id=second["job_id"]))
            assert next_result is not None
            assert next_result["status"] == "QUEUED"
        if status == "UNKNOWN_COMMIT":
            recovered = asyncio.run(service.run_once(adapters={"douyin": site.adapter(service)}, job_id=first["job_id"]))
            assert recovered is not None
            assert recovered["status"] == "UNKNOWN_COMMIT"
            assert len(site.submissions) == 1


@pytest.mark.parametrize("mode", ["wrong-video", "changed-body", "unknown-state", "duplicate-work"])
def test_work_readback_mismatch_never_confirms_or_republishes(tmp_path, mode):
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with DouyinSite(mode) as site:
        result = asyncio.run(service.run_once(adapters={"douyin": site.adapter(service)}, job_id=job["job_id"]))
        assert result is not None and result["status"] == "UNKNOWN_COMMIT"
        assert result["content_id"] is None
        assert len(site.submissions) == 1


def test_saved_receipt_recovers_after_restart_without_source_media(tmp_path):
    from orchestrator.service import TaskService
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with DouyinSite() as site:
        site.hide_works = True
        result = asyncio.run(service.run_once(adapters={"douyin": site.adapter(service)}, job_id=job["job_id"]))
        assert result is not None and result["status"] == "UNKNOWN_COMMIT"
        for asset in (tmp_path / "media").iterdir():
            asset.unlink()
        restarted = TaskService(service.store.path, runtime_dir=service.runtime_dir)
        site.hide_works = False
        result = asyncio.run(restarted.run_once(adapters={"douyin": site.adapter(restarted)}, job_id=job["job_id"]))
        assert result is not None and result["status"] == "COMPLETED"
        assert len(site.submissions) == 1


def test_readonly_inspection_never_creates_a_delivery(tmp_path):
    from orchestrator.browser import BrowserSession
    from orchestrator.inspection import inspect_work
    from orchestrator.platforms.douyin import DouyinVideoPort
    from tests.douyin_fixture import DouyinSite
    from tests.test_browser_lifecycle import _installed_chromium_executable

    service = make_service(tmp_path)
    with DouyinSite() as site:
        class Port(DouyinVideoPort):
            origin = site.origin
            upload_origin = site.origin
        result = asyncio.run(inspect_work(service, "douyin", "creator", "7690181573743742254", port_factory=Port,
            session_factory=lambda c: BrowserSession(c, launch_options={"headless": True,
                "executable_path": str(_installed_chromium_executable())})))
        assert result["status"] == "NOT_FOUND"
        assert service.list_jobs() == []
        assert site.submissions == []


def test_inspection_closes_browser_after_ownership_is_lost(tmp_path):
    from orchestrator.browser import BrowserSession
    from orchestrator.errors import TaskError
    from orchestrator.inspection import inspect_work
    from tests.test_browser_lifecycle import DriverHarness

    service = make_service(tmp_path)
    harness = DriverHarness()

    def session_factory(context):
        harness.context = context
        return BrowserSession(context, driver_factory=harness.start)

    class LostOwnershipPort:
        async def navigate(self, page, content_type, *, readonly):
            context = harness.context
            assert context is not None
            context.lease.token = "no-longer-the-owner"

    with pytest.raises(TaskError) as caught:
        asyncio.run(inspect_work(service, "douyin", "creator", "123",
            port_factory=LostOwnershipPort, session_factory=session_factory))
    assert caught.value.code == "OWNERSHIP_LOST"
    assert harness.driver is not None and harness.driver.stopped
    assert harness.browser is not None and harness.browser.closed
    assert harness.browser.context is not None and harness.browser.context.closed
    assert service.list_jobs() == []


def test_pre_submit_pause_resumes_same_job_only_after_explicit_session_rebind(tmp_path):
    from orchestrator.errors import TaskError
    from tests.douyin_fixture import DouyinSite
    from tests.task_fixtures import bind_test_account

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with DouyinSite("wrong-account") as site:
        result = asyncio.run(service.run_once(adapters={"douyin": site.adapter(service)}, job_id=job["job_id"]))
        assert result is not None and result["status"] == "BLOCKED"
    with pytest.raises(TaskError):
        service.resume(job["job_id"])
    bind_test_account(service, tmp_path, "douyin", "creator")
    resumed = service.resume(job["job_id"])
    assert resumed["status"] == "QUEUED"
    assert resumed["job_id"] == job["job_id"]
    with DouyinSite() as site:
        result = asyncio.run(service.run_once(adapters={"douyin": site.adapter(service)}, job_id=job["job_id"]))
        assert result is not None and result["status"] == "COMPLETED"
        assert len(site.submissions) == 1

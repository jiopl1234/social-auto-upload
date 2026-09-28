"""Real Douyin protocol exercised at the public service/local browser seam."""
import asyncio
from datetime import datetime, timezone
import pytest

from tests.task_fixtures import make_manifest, make_service, readback_metadata


def test_cli_verification_notice_keeps_json_output_and_confirms_one_submission(tmp_path, monkeypatch, capsys):
    import json
    import time
    from concurrent.futures import ThreadPoolExecutor
    from orchestrator.platforms import profiles
    from sau_cli import main
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with DouyinSite('manual-verification') as site:
        # Only the external platform is replaced; CLI construction and runtime stay real.
        monkeypatch.setattr(profiles, 'create_port', lambda platform: site.port())

        def person():
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if any(event['stage'] == 'verification' for event in service.events(job['job_id'])):
                    paused = next(a for a in service.accounts() if a['platform'] == 'douyin')['status']
                    site.verification_released.set()
                    return paused
                time.sleep(.05)
            pytest.fail('CLI did not expose the active verification prompt')

        with ThreadPoolExecutor(max_workers=1) as executor:
            approval = executor.submit(person)
            code = main(['jobs', '--db', str(service.store.path), '--runtime-dir', str(service.runtime_dir),
                         '--media-root', str(tmp_path / 'media'), '--json', 'run', '--job', job['job_id'],
                         '--headed', '--verification-wait', '8'])
            assert approval.result(timeout=25) == 'AUTH_REQUIRED'
        output = capsys.readouterr()
        result = json.loads(output.out)
        assert code == 0 and result['status'] == 'COMPLETED', result
        assert '抖音要求本人验证' in output.err
        assert '不要再次点击发表' in output.err
        assert '正在确认作品结果' in output.err
        assert len(site.submissions) == 1
        assert service.attempts(job['job_id'])[0]['submit_count'] == 1


@pytest.mark.parametrize('mode', ['manual-verification', 'receipt-verification', 'manual-worklist'])
def test_visible_verification_continues_same_submission_and_restores_account(tmp_path, mode):
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    notices = []
    with DouyinSite(mode) as site:
        async def run():
            task = asyncio.create_task(service.run_once(adapters={'douyin': site.adapter(service,
                verification_wait=8, on_verification=notices.append)}, job_id=job['job_id']))
            try:
                async def wait_for_prompt():
                    while not any(event['stage'] == 'verification' for event in service.events(job['job_id'])):
                        if task.done():
                            pytest.fail(f'Browser closed before verification: {await task}')
                        await asyncio.sleep(.05)
                await asyncio.wait_for(wait_for_prompt(), timeout=15)
                assert not task.done()
                assert next(a for a in service.accounts() if a['platform'] == 'douyin')['status'] == 'AUTH_REQUIRED'
                assert service.attempts(job['job_id'])[0]['submit_count'] == 1
                assert len(site.submissions) == (0 if mode == 'manual-verification' else 1)
                site.verification_released.set()  # The local target simulates the person completing verification.
                return await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        result = asyncio.run(run())
        assert result is not None and result['status'] == 'COMPLETED', result
        assert result['platform_result'] == 'PUBLISHED'
        assert len(site.submissions) == 1
        assert service.attempts(job['job_id'])[0]['submit_count'] == 1
        assert next(a for a in service.accounts() if a['platform'] == 'douyin')['status'] == 'READY'
        assert notices == ['waiting', 'verified']
        assert [event['details']['verification_state'] for event in service.events(job['job_id'])
                if event['stage'] == 'verification'] == ['waiting', 'verified']


@pytest.mark.parametrize('limits,code', [({}, 'AUTH_REQUIRED'), ({'submit': .25}, 'TIMEOUT')])
def test_verification_expiry_keeps_account_paused_and_old_submission_readonly(tmp_path, limits, code):
    from tests.douyin_fixture import DouyinSite
    from tests.task_fixtures import bind_test_account
    from tests.test_job_cli import run_jobs

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    next_job = service.enqueue(make_manifest(tmp_path, key='next'))
    with DouyinSite('manual-verification') as site:
        result = asyncio.run(service.run_once(adapters={'douyin': site.adapter(service, verification_wait=.5)},
            job_id=job['job_id'], limits=limits))
        assert result is not None and result['status'] == 'UNKNOWN_COMMIT', result
        assert result['error']['code'] == code
        assert result['submission'] == 'MAY_HAVE_SUBMITTED'
        assert next(a for a in service.accounts() if a['platform'] == 'douyin')['status'] == 'AUTH_REQUIRED'
        assert site.submissions == []
        assert service.attempts(job['job_id'])[0]['submit_count'] == 1
        blocked = asyncio.run(service.run_once(adapters={'douyin': site.adapter(service)}, job_id=next_job['job_id']))
        assert blocked is not None and blocked['status'] == 'QUEUED'
        assert service.attempts(next_job['job_id']) == []

        completed, error = run_jobs(service.store.path, 'run', '--job', job['job_id'], '--headed', '--verification-wait', '10')
        assert completed.returncode != 0 and error['error']['code'] == 'INVALID_STATE'
        assert service.status(job['job_id']) == result

        # Explicit session refresh only permits a read-only recovery of this job.
        bind_test_account(service, tmp_path, 'douyin', 'creator')
        recovered = asyncio.run(service.run_once(adapters={'douyin': site.adapter(service, verification_wait=8)},
            job_id=job['job_id']))
        assert recovered is not None and recovered['status'] == 'UNKNOWN_COMMIT'
        assert recovered['attempt_id'] == result['attempt_id']
        assert service.attempts(job['job_id'])[0]['submit_count'] == 1
        assert site.submissions == []


@pytest.mark.parametrize('action,code', [('cancel', 'CANCELLED'), ('close', 'PAGE_CLOSED')])
def test_interrupted_verification_never_starts_the_next_job(tmp_path, action, code):
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    second = service.enqueue(make_manifest(tmp_path, key='second'))
    with DouyinSite('manual-verification') as site:
        async def run():
            prompt = asyncio.Event()
            task = asyncio.create_task(service.run_pending(adapters={'douyin': site.adapter(service,
                verification_wait=10, on_verification=lambda state: prompt.set() if state == 'waiting' else None)}))
            try:
                await asyncio.wait_for(prompt.wait(), 15)
                if action == 'cancel':
                    task.cancel()
                else:
                    await site.close_window()
                return await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        results = asyncio.run(run())
        assert len(results) == 1
        assert results[0]['status'] == 'UNKNOWN_COMMIT'
        assert results[0]['error']['code'] == code
        assert service.attempts(job['job_id'])[0]['submit_count'] == 1
        assert service.status(second['job_id'])['status'] == 'QUEUED'
        assert service.attempts(second['job_id']) == []
        assert next(a for a in service.accounts() if a['platform'] == 'douyin')['status'] == 'AUTH_REQUIRED'
        assert site.submissions == []


@pytest.mark.parametrize('mode,code', [
    ('manual-wrong-account', 'IDENTITY_MISMATCH'),
    ('manual-lost-receipt', 'SUBMISSION_UNCERTAIN'),
    ('manual-stale-identity', 'AUTH_REQUIRED'),
    ('manual-hidden-work', None),
])
def test_clearing_verification_without_delivery_proof_does_not_resume_account(tmp_path, mode, code):
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with DouyinSite(mode) as site:
        site.hide_works = mode == 'manual-hidden-work'
        result = asyncio.run(service.run_once(adapters={'douyin': site.adapter(service, verification_wait=5,
            on_verification=lambda state: site.verification_released.set() if state == 'waiting' else None)},
            job_id=job['job_id']))
        assert result is not None and result['status'] == 'UNKNOWN_COMMIT', result
        if code is not None:
            assert result['error']['code'] == code
        assert next(a for a in service.accounts() if a['platform'] == 'douyin')['status'] != 'READY'
        assert service.attempts(job['job_id'])[0]['submit_count'] == 1
        assert len(site.submissions) <= 1


def test_repeated_verification_across_submit_and_work_lookup_shares_one_deadline(tmp_path):
    import time
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    notices = []
    with DouyinSite('manual-repeat') as site:
        async def run():
            async def person():
                await asyncio.sleep(1.2)
                site.verification_released.set()

            approvals: list[asyncio.Task[None]] = []

            def notice(state):
                notices.append((state, time.monotonic()))
                if not approvals:
                    approvals.append(asyncio.create_task(person()))

            result = await service.run_once(adapters={'douyin': site.adapter(service,
                verification_wait=2, on_verification=notice)}, job_id=job['job_id'])
            await asyncio.gather(*approvals)
            return result

        result = asyncio.run(run())
        assert result is not None and result['status'] == 'UNKNOWN_COMMIT', result
        assert [state for state, _ in notices] == ['waiting', 'verified', 'waiting', 'expired']
        assert notices[-1][1] - notices[0][1] < 2.6
        assert next(a for a in service.accounts() if a['platform'] == 'douyin')['status'] == 'AUTH_REQUIRED'
        assert service.attempts(job['job_id'])[0]['submit_count'] == 1
        assert len(site.submissions) == 1


def test_reappearing_editor_challenge_cannot_be_erased_by_reconciliation(tmp_path):
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    notices = []
    with DouyinSite('receipt-verification') as site:
        async def run():
            repeated: list[asyncio.Task] = []

            async def reprompt():
                await site.page.evaluate('''() => {
                    const dialog = document.createElement('article');
                    dialog.className = 'uc-ui-verify_qr-verify';
                    dialog.textContent = '安全验证'; document.body.append(dialog);
                }''')

            def notice(state):
                notices.append(state)
                if state == 'waiting' and len(notices) == 1:
                    site.verification_released.set()
                if state == 'verified' and not repeated:
                    repeated.append(asyncio.create_task(reprompt()))

            result = await service.run_once(adapters={'douyin': site.adapter(service,
                verification_wait=2, on_verification=notice)}, job_id=job['job_id'])
            await asyncio.gather(*repeated)
            return result

        result = asyncio.run(run())
        assert result is not None and result['status'] == 'UNKNOWN_COMMIT', result
        assert notices == ['waiting', 'verified', 'waiting', 'expired']
        assert next(a for a in service.accounts() if a['platform'] == 'douyin')['status'] == 'AUTH_REQUIRED'
        assert service.attempts(job['job_id'])[0]['submit_count'] == 1
        assert len(site.submissions) == 1


def test_verified_delivery_with_cleanup_failure_keeps_account_paused(tmp_path, monkeypatch):
    from orchestrator.browser import BrowserSession
    from orchestrator.errors import TaskError
    from tests.douyin_fixture import DouyinSite

    close = BrowserSession.close

    async def fail_shutdown(self):
        await close(self)
        raise TaskError('CLEANUP_FAILED', 'Injected shutdown failure')

    monkeypatch.setattr(BrowserSession, 'close', fail_shutdown)
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with DouyinSite('manual-verification') as site:
        result = asyncio.run(service.run_once(adapters={'douyin': site.adapter(service, verification_wait=5,
            on_verification=lambda state: site.verification_released.set() if state == 'waiting' else None)},
            job_id=job['job_id']))
        assert result is not None and result['status'] == 'COMPLETED', result
        assert result['platform_result'] == 'PUBLISHED'
        assert 'CLEANUP_FAILED' in {warning['code'] for warning in result['warnings']}
        assert next(a for a in service.accounts() if a['platform'] == 'douyin')['status'] == 'CLEANUP_FAILED'
        assert len(site.submissions) == 1
        assert service.attempts(job['job_id'])[0]['submit_count'] == 1


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


@pytest.mark.parametrize("mode", ["normal", "preview-tabs"])
def test_real_port_delivers_once_through_browser_and_durable_task_protocol(tmp_path, mode):
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with DouyinSite(mode) as site:
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


@pytest.mark.parametrize('mode', ['pending-covers', 'uncropped-covers'])
def test_unfinished_cover_upload_cannot_be_reported_ready(tmp_path, mode):
    from orchestrator.platforms.base import PreparationOnlyAdapter
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    with DouyinSite(mode) as site:
        result = asyncio.run(service.run_once(adapters={'douyin': PreparationOnlyAdapter(site.adapter(service))},
            job_id=job['job_id'], limits={'metadata': 2}))
        assert result is not None and result['status'] == 'FAILED_PRE_SUBMIT'
        assert result['error']['code'] == 'TIMEOUT'
        assert result['submission'] == 'NOT_SUBMITTED'
        assert site.submissions == []


@pytest.mark.parametrize('body', ['第一行\n\n第二行', '正文\u200b保留字符'])
def test_editor_markers_are_excluded_but_original_text_is_preserved(tmp_path, body):
    from orchestrator.platforms.base import PreparationOnlyAdapter
    from tests.douyin_fixture import DouyinSite

    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, body=body))
    with DouyinSite('rich-editor') as site:
        result = asyncio.run(service.run_once(adapters={'douyin': PreparationOnlyAdapter(site.adapter(service))},
            job_id=job['job_id']))
        assert result is not None and result['status'] == 'READY', result
        assert result['actual_metadata']['body'] == body
        assert site.submissions == []


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
    ("failed-cover-check-after-ready", "FAILED_PRE_SUBMIT", "METADATA_MISMATCH"),
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


@pytest.mark.parametrize("mode", ["normal", "collection-tip", "unknown-tip",
                                 "collection-tip-auth", "collection-tip-retained-auth"])
def test_readonly_inspection_never_creates_a_delivery(tmp_path, mode):
    from orchestrator.browser import BrowserSession
    from orchestrator.inspection import inspect_work
    from orchestrator.platforms.douyin import DouyinVideoPort
    from tests.douyin_fixture import DouyinSite
    from tests.test_browser_lifecycle import _installed_chromium_executable
    from orchestrator.errors import TaskError

    service = make_service(tmp_path)
    with DouyinSite(mode) as site:
        class Port(DouyinVideoPort):
            origin = site.origin
            upload_origin = site.origin
        operation = inspect_work(service, "douyin", "creator", "7690181573743742254", port_factory=Port,
            session_factory=lambda c: BrowserSession(c, launch_options={"headless": True,
                "executable_path": str(_installed_chromium_executable())}))
        if mode in {"unknown-tip", "collection-tip-auth", "collection-tip-retained-auth"}:
            with pytest.raises(TaskError) as caught:
                asyncio.run(operation)
            assert caught.value.code == ("UI_CHANGED" if mode == "unknown-tip" else "AUTH_REQUIRED")
            if mode != "unknown-tip":
                next_job = service.enqueue(make_manifest(tmp_path))
                result = asyncio.run(service.run_once(adapters={"douyin": site.adapter(service)}, job_id=next_job["job_id"]))
                assert result is not None and result["status"] == "QUEUED"
                assert service.attempts(next_job["job_id"]) == []
        else:
            result = asyncio.run(operation)
            assert result["status"] == "NOT_FOUND"
        if mode not in {"collection-tip-auth", "collection-tip-retained-auth"}:
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

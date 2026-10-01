"""Receipt/detail acceptance and same-attempt read-only recovery, offline only."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from orchestrator.service import TaskService
from orchestrator.platforms.base import BrowserDeliveryAdapter
from tests.test_xiaohongshu_live import job_fixture
from tests.xiaohongshu_fixture import NOTE_ID, XiaohongshuSite, fixture_adapter


def run(service, job, site):
    return asyncio.run(service.run_once(adapters={"xiaohongshu": fixture_adapter(service, site, delivery=True)},
        job_id=job["job_id"], max_attempts=1, limits={"total":30,"cleanup":4}))


@pytest.mark.parametrize('mode', ['success','readonly-write-attempt','live-result-receipt'])
def test_full_delivery_requires_receipt_detail_and_source_link_and_is_not_repeated(tmp_path, mode):
    service, job = job_fixture(tmp_path, multipart=True)
    with XiaohongshuSite(mode) as site:
        result = run(service, job, site)
        assert result is not None and result["status"] == "COMPLETED", result
        assert result["submission"] == "CONFIRMED" and result["platform_result"] == "PUBLISHED"
        assert result["content_id"] == NOTE_ID and result["verified_at"]
        assert result["actual_metadata"]["body"] == "First line\n\nSecond line\n"
        assert site.submissions == 1
        assert site.readonly_writes == 0
        response = [e for e in result['evidence'] if e.get('kind')=='submission_response']
        assert len(response) == 1 and response[0]['id_fields'] == (['data.id'] if mode=='live-result-receipt' else ['data.note_id'])
        if mode == 'live-result-receipt':
            assert response[0]['receipt_fields'] == {'success':True,'result':0,'data.id':NOTE_ID}
        attempt = service.attempts(job["job_id"])[0]
        assert attempt["receipt_id"] == NOTE_ID and attempt["submit_count"] == 1
        upload = [entry for entry in result["evidence"] if entry["kind"] == "uploaded_video"]
        assert upload == [{"kind":"uploaded_video","video_id":"spectrum/video001",
                          "sha256":job["manifest"]["media"][0]["sha256"],"attempt_id":attempt["attempt_id"],
                          "md5":hashlib.md5(Path(job["manifest"]["media"][0]["path"]).read_bytes()).hexdigest()}]
        repeated = run(service, job, site)
        assert repeated is not None and repeated["attempt_id"] == result["attempt_id"]
        assert site.submissions == 1


@pytest.mark.parametrize("mode", ["ambiguous-no-receipt", "incomplete-no-receipt", "detail-wrong-author", "detail-wrong-body",
                                  "detail-wrong-video", "detail-old-time", "post-challenge"])
def test_receipt_and_detail_faults_keep_unknown_and_never_resubmit(tmp_path, mode):
    service, job = job_fixture(tmp_path)
    with XiaohongshuSite(mode) as site:
        result = run(service, job, site)
        assert result is not None and result["status"] == "UNKNOWN_COMMIT", result
        assert result["submission"] == "MAY_HAVE_SUBMITTED"
        assert site.submissions == 1
        if mode == "post-challenge":
            assert result["error"]["code"] == "AUTH_REQUIRED"
            binding = next(account for account in service.accounts() if account["platform"] == "xiaohongshu" and account["account"] == "xhs_test")
            assert binding["status"] == "AUTH_REQUIRED"
            assert binding["pause_reason"]["code"] == "AUTH_REQUIRED"
        else:
            repeated = run(service, job, site)
            assert repeated is not None and repeated["status"] == "UNKNOWN_COMMIT"
            assert site.submissions == 1


def test_recovery_uses_same_receipt_and_upload_without_original_media(tmp_path):
    service, job = job_fixture(tmp_path)
    with XiaohongshuSite("detail-wrong-body") as site:
        result = run(service, job, site)
        assert result is not None and result["status"] == "UNKNOWN_COMMIT", result
        Path(job["manifest"]["media"][0]["path"]).unlink()
        restarted = TaskService(service.store.path, runtime_dir=service.runtime_dir, media_roots=[tmp_path/'media'])
        site.mode = 'success'
        recovered = run(restarted, job, site)
        assert recovered is not None and recovered["status"] == "COMPLETED", recovered
        assert recovered["attempt_id"] == result["attempt_id"]
        assert site.submissions == 1


@pytest.mark.parametrize('mode,part_count',[('large-parts',3),('adaptive-parts',4)])
def test_adaptive_parts_arriving_in_reverse_order_bind_the_original_video(tmp_path,mode,part_count):
    service, _ = job_fixture(tmp_path)
    from tests.task_fixtures import make_manifest
    raw = make_manifest(tmp_path,platform='xiaohongshu',account='xhs_test',key='large-parts',
                        body='First line\n\nSecond line\n')
    path = Path(raw['media'][0])
    with path.open('ab') as stream:
        stream.write(b'a' * 11000000)
    job = service.enqueue(raw)
    with XiaohongshuSite(mode) as site:
        result = run(service,job,site)
        assert result is not None and result['status']=='COMPLETED',result
        assert len(site.parts) == part_count
        assert site.submissions == 1
        assert result['evidence'][-1]['remote_media_md5'] == hashlib.md5(path.read_bytes()).hexdigest()


def test_explicit_verification_wait_keeps_original_attempt_and_clicks_once(tmp_path):
    service, job = job_fixture(tmp_path)
    with XiaohongshuSite('post-challenge') as site:
        async def exercise():
            waiting = asyncio.Event()
            notices = []
            def notice(state):
                notices.append(state)
                if state == 'waiting': waiting.set()
            adapter = fixture_adapter(service, site, delivery=True, verification_wait=4, on_verification=notice)
            assert isinstance(adapter, BrowserDeliveryAdapter)
            running = asyncio.create_task(service.run_once(adapters={'xiaohongshu':adapter}, job_id=job['job_id'],
                limits={'total':30,'cleanup':4}))
            await asyncio.wait_for(waiting.wait(), 15)
            paused = next(a for a in service.accounts() if a['platform']=='xiaohongshu' and a['account']=='xhs_test')
            assert paused['status'] == 'AUTH_REQUIRED'
            assert site.submissions == 1
            # The test emulates the user resolving only its synthetic prompt.
            await adapter.page.locator('.red-captcha').evaluate('e=>e.remove()')
            result = await running
            assert notices == ['waiting','verified']
            return result
        result = asyncio.run(exercise())
        assert result is not None and result['status']=='COMPLETED', result
        assert site.submissions == 1
        paused = next(a for a in service.accounts() if a['platform']=='xiaohongshu' and a['account']=='xhs_test')
        assert paused['status'] == 'READY'


def test_missing_receipt_recovers_only_with_complete_unique_remote_source_fingerprint(tmp_path):
    service, job = job_fixture(tmp_path)
    with XiaohongshuSite('missing-receipt') as site:
        result = run(service, job, site)
        assert result is not None and result['status'] == 'COMPLETED', result
        assert result['content_id'] == NOTE_ID
        attempt = service.attempts(job['job_id'])[0]
        assert attempt['receipt_id'] is None and attempt['submit_count'] == 1
        assert result['evidence'][-1]['remote_media_md5'] == hashlib.md5(Path(job['manifest']['media'][0]['path']).read_bytes()).hexdigest()
        assert site.submissions == 1


@pytest.mark.parametrize('changed_source', [False, True])
def test_legacy_recovery_enriches_only_the_sha_verified_source_without_new_attempt(tmp_path, changed_source):
    service, job = job_fixture(tmp_path)
    with XiaohongshuSite('detail-wrong-body') as site:
        result = run(service, job, site)
        assert result is not None and result['status'] == 'UNKNOWN_COMMIT'
        with service.store.transaction() as connection:
            evidence = result['evidence']
            for item in evidence:
                if item.get('kind') == 'uploaded_video': item.pop('md5')
            connection.execute('UPDATE jobs SET evidence_json=? WHERE job_id=?', (json.dumps(evidence),job['job_id']))
        if changed_source:
            Path(job['manifest']['media'][0]['path']).write_bytes(b'changed')
        site.mode = 'success'
        recovered = run(service, job, site)
        assert recovered is not None and recovered['status'] == ('UNKNOWN_COMMIT' if changed_source else 'COMPLETED'), recovered
        assert recovered['attempt_id'] == result['attempt_id']
        assert site.submissions == 1


@pytest.mark.parametrize('payload,expected', [
    ({'success':True,'code':0,'data':{'note_id':NOTE_ID}}, NOTE_ID),
    ({'success':True,'result':0,'data':{'id':NOTE_ID}}, NOTE_ID),
    ({'success':True,'code':0,'data':{'noteId':NOTE_ID}}, NOTE_ID),
    ({'result':{'success':True,'code':0},'noteId':NOTE_ID}, NOTE_ID),
    ({'success':False,'code':0,'data':{'id':NOTE_ID}}, None),
    ({'success':True,'code':1,'data':{'id':NOTE_ID}}, None),
    ({'success':True,'code':False,'data':{'id':NOTE_ID}}, None),
    ({'success':True,'result':False,'data':{'id':NOTE_ID}}, None),
    ({'success':True,'result':1,'data':{'id':NOTE_ID}}, None),
    ({'success':True,'result':0,'code':1,'data':{'id':NOTE_ID}}, None),
    ({'success':True,'data':{'id':NOTE_ID}}, None),
    ({'success':True,'code':0,'data':{'id':'bad'}}, None),
    ({'success':True,'code':0,'data':{'id':NOTE_ID},'noteId':'a'*24}, None),
    ({'success':True,'code':0,'data':{}}, None),
])
def test_receipt_envelopes_require_positive_unique_server_id(payload, expected):
    from orchestrator.platforms.xiaohongshu_delivery import XiaohongshuVideoPort
    assert XiaohongshuVideoPort.receipt_content_id(payload) == expected


@pytest.mark.parametrize('fault',[None,'missing-response','negative-result','boolean-result','wrong-id','wrong-attempt'])
def test_cli_recovers_only_saved_positive_receipt_without_another_action(tmp_path,monkeypatch,fault):
    from orchestrator.platforms.xiaohongshu_delivery import XiaohongshuVideoPort
    from tests.test_job_cli import run_jobs
    parser = XiaohongshuVideoPort.receipt_content_id
    monkeypatch.setattr(XiaohongshuVideoPort,'receipt_content_id',staticmethod(lambda payload:None))
    service,job = job_fixture(tmp_path)
    with XiaohongshuSite('live-result-receipt') as site:
        result = run(service,job,site)
        assert result is not None and result['status']=='COMPLETED',result
        assert service.attempts(job['job_id'])[0]['receipt_id'] is None
        monkeypatch.setattr(XiaohongshuVideoPort,'receipt_content_id',staticmethod(parser))
        if fault:
            evidence = result['evidence']
            response = next(item for item in evidence if item.get('kind')=='submission_response')
            if fault=='missing-response': evidence.remove(response)
            elif fault=='negative-result': response['receipt_fields']['result']=1
            elif fault=='boolean-result': response['receipt_fields']['result']=False
            elif fault=='wrong-id': response['receipt_fields']['data.id']='a'*24
            elif fault=='wrong-attempt': response['attempt_id']='other'
            with service.store.transaction() as connection:
                connection.execute('UPDATE jobs SET evidence_json=? WHERE job_id=?',(json.dumps(evidence),job['job_id']))
        completed,recovered = run_jobs(service.store.path,'recover-receipt',job['job_id'])
        attempt = service.attempts(job['job_id'])[0]
        assert attempt['attempt_id']==result['attempt_id'] and attempt['submit_count']==1
        assert site.submissions==1
        assert service.status(job['job_id'])['verified_at']==result['verified_at']
        if fault:
            assert completed.returncode!=0 and recovered['error']['code']=='RECONCILIATION_UNAVAILABLE'
            assert attempt['receipt_id'] is None
        else:
            assert completed.returncode==0 and recovered['receipt_id']==NOTE_ID,recovered
            assert attempt['receipt_id']==NOTE_ID
            repeated,again = run_jobs(service.store.path,'recover-receipt',job['job_id'])
            assert repeated.returncode==0 and again==recovered
            assert len([e for e in service.status(job['job_id'])['evidence'] if e.get('kind')=='receipt_recovered'])==1

"""Synthetic replay of observed Channels upload and request boundaries."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from orchestrator.errors import TaskError
from orchestrator.file_parts_process import file_parts
from orchestrator.platforms import default_adapters
from orchestrator.platforms.channels import (
    API, CREATE_PATH, ORIGIN, ChannelsVideoPreparationPort, media_identity, positive_data,
)


URL = 'https://finder.video.qq.com/251/20302/stodownload?encfilekey=' + 'k' * 64 + '&token=private'


def response(path, method, body, payload, status=200):
    async def decoded():
        return payload
    return SimpleNamespace(status=status, request=SimpleNamespace(method=method, post_data_buffer=body), json=decoded)


@pytest.mark.parametrize('fault', [None, 'wrong-part', 'missing-part', 'wrong-etag', 'wrong-url', 'failed-part', 'conflicting-part'])
def test_ordered_source_proof_ignores_arrival_order_and_never_accepts_incomplete_upload(tmp_path, fault):
    source = b'first-segment' + b'second-segment' + b'last'
    path = tmp_path / 'source.mp4'
    path.write_bytes(source)
    port = ChannelsVideoPreparationPort()
    port.asset = {'path': str(path), 'sha256': hashlib.sha256(source).hexdigest()}
    port.fingerprints = file_parts(port.asset)
    pieces = [source[:13], source[13:27], source[27:]]
    async def exercise():
        for n in (3, 1, 2):
            if fault == 'missing-part' and n == 2:
                continue
            body = pieces[n-1] if fault != 'wrong-part' or n != 1 else b'x' * len(pieces[0])
            payload = {'ETag': '"' + hashlib.md5(body).hexdigest() + '"', 'X-Errno': 0}
            if fault == 'failed-part' and n == 1:
                payload['X-Errno'] = 1
            url = urlsplit('https://finderassistancea.video.qq.com/uploadpartdfs?PartNumber=' + str(n) + '&UploadID=base+64')
            await port._observe_storage(response(url.path, 'PUT', body, payload), url)
            if fault == 'conflicting-part' and n == 1:
                await port._observe_storage(response(url.path, 'PUT', b'y'*13, {'ETag':'"'+hashlib.md5(b'y'*13).hexdigest()+'"','X-Errno':0}),url)
        parts = [{'PartNumber': n, 'ETag': '"'+hashlib.md5(body).hexdigest()+'"'} for n,body in enumerate(pieces,1)]
        if fault == 'wrong-etag':
            parts[0]['ETag'] = '"' + '0'*32 + '"'
        url = urlsplit('https://finderassistanceb.video.qq.com/completepartuploaddfs?UploadID=base+64')
        remote = URL.replace('/20302/', '/20304/') if fault == 'wrong-url' else URL
        await port._observe_storage(response(url.path,'POST',json.dumps({'PartInfo':parts}).encode(),{'DownloadURL':remote}),url)
    asyncio.run(exercise())
    assert port.completed == ({media_identity(URL)} if fault is None else set())


@pytest.mark.parametrize('value', [None, {'errCode': True,'data':{}}, {'errCode':1,'data':{}},
                                  {'errCode':0,'data':{'baseResp':{'errcode':1}}}, {'errCode':0,'data':{'baseResp':{'errcode':False}}}])
def test_http_200_and_truthy_statuses_are_not_positive_evidence(value):
    assert positive_data(value) is None


def test_signed_urls_are_reduced_to_one_non_secret_media_identity():
    assert media_identity(URL) == media_identity(URL.replace('token=private','token=changed'))
    assert media_identity(URL.replace('finder.video.qq.com','attacker.example')) is None
    assert media_identity(URL.replace('/20302/','/20304/')) is None
    assert media_identity(URL+'&encfilekey=second') is None
    identity=media_identity(URL)
    assert identity is not None and len(identity) == 72


def test_preparation_wrapper_has_no_publication_methods(tmp_path):
    adapter = default_adapters(tmp_path, prepare_only=True)['tencent']
    assert type(adapter.adapter.port_factory()) is ChannelsVideoPreparationPort
    assert not hasattr(adapter,'submit') and not hasattr(adapter,'reconcile')
    for method in (adapter.adapter.verify_ready, adapter.adapter.submit):
        with pytest.raises(TaskError) as caught:
            asyncio.run(method(None))
        assert caught.value.code == 'UNSUPPORTED'


def test_request_guard_blocks_drafts_preparation_and_frontend_publish_retries():
    port = ChannelsVideoPreparationPort()
    actions = []
    class Route:
        def __init__(self,path):
            self.request=SimpleNamespace(url=ORIGIN+path,method='POST')
        async def abort(self): actions.append('blocked')
        async def fallback(self): actions.append('allowed')
    async def exercise():
        await port._route(Route(CREATE_PATH))
        await port._route(Route('/micro/content'+API+'post/post_draft'))
        port.submitted = True
        await port._route(Route(CREATE_PATH))
        await port._route(Route(CREATE_PATH))
        port.readonly = True
        await port._route(Route(CREATE_PATH))
        await port._route(Route('/micro/content'+API+'post/get_post_info'))
    asyncio.run(exercise())
    assert actions == ['blocked','blocked','allowed','blocked','blocked','allowed']
    assert port.create_requests == 1


def test_hevc_processing_is_preparation_only_and_collection_search_is_read_only():
    port = ChannelsVideoPreparationPort()
    actions = []
    class Route:
        def __init__(self, path):
            self.request = SimpleNamespace(url=ORIGIN + path, method='POST')
        async def abort(self): actions.append('blocked')
        async def fallback(self): actions.append('allowed')
    async def exercise():
        clip = Route('/micro/content' + API + 'post/post_clip_video')
        await port._route(clip)
        port.asset = {'sha256': 'a' * 64}
        await port._route(clip)
        port.submitted = True
        await port._route(clip)
        port.submitted = False
        port.readonly = True
        await port._route(clip)
        await port._route(Route('/micro/content' + API + 'post/post_clip_video_result'))
        await port._route(Route('/micro/content' + API + 'collection/search_collection'))
    asyncio.run(exercise())
    assert actions == ['blocked', 'allowed', 'blocked', 'blocked', 'allowed', 'allowed']


@pytest.mark.parametrize('mode,expected', [('success','READY'),('wrong-account','BLOCKED'),('challenge','BLOCKED'),('dialog','BLOCKED'),('location','BLOCKED'),('scheduled','FAILED_PRE_SUBMIT'),('wrong-file','FAILED_PRE_SUBMIT'),('missing-complete','FAILED_PRE_SUBMIT'),('upload-error','FAILED_PRE_SUBMIT'),('processing-stuck','FAILED_PRE_SUBMIT')])
def test_browser_preparation_is_bounded_and_cannot_publish(tmp_path,mode,expected):
    from orchestrator.service import TaskService
    from tests.task_fixtures import make_manifest
    from tests.channels_fixture import UID,ChannelsSite,fixture_adapter
    service=TaskService(tmp_path/'jobs.sqlite3',runtime_dir=tmp_path/'runtime',media_roots=[tmp_path/'media'])
    source=tmp_path/'session.json';source.write_text('{"cookies":[],"origins":[]}')
    service.bind_account('tencent','channels_test',UID,source)
    job=service.enqueue(make_manifest(tmp_path,platform='tencent',account='channels_test',title='Synthetic title',body='Single paragraph'))
    with ChannelsSite(mode) as site:
        result=asyncio.run(service.run_once(adapters={'tencent':fixture_adapter(service,site)},job_id=job['job_id'],max_attempts=1,limits={'total':20,'processing':2,'cleanup':4}))
    assert result is not None
    assert result['status']==expected,result
    assert result['submission']=='NOT_SUBMITTED' and site.submissions==0
    assert service.attempts(job['job_id'])[0]['submit_count']==0


@pytest.mark.parametrize('mode,expected', [('success','COMPLETED'),('identity-after-list','COMPLETED'),('frontend-retry','COMPLETED'),('readonly-write','COMPLETED'),('no-id-receipt','COMPLETED'),('missing-author','COMPLETED'),('in-review','COMPLETED'),('filtered-list','UNKNOWN_COMMIT'),('incomplete-list','UNKNOWN_COMMIT'),('duplicate-match','UNKNOWN_COMMIT'),('bad-detail-settings','UNKNOWN_COMMIT'),('wrong-detail-body','UNKNOWN_COMMIT'),('wrong-detail-author','UNKNOWN_COMMIT'),('wrong-detail-media','UNKNOWN_COMMIT'),('negative-receipt','UNKNOWN_COMMIT'),('post-challenge','UNKNOWN_COMMIT')])
def test_full_delivery_requires_receipt_and_independent_details_and_clicks_once(tmp_path,mode,expected):
    from orchestrator.service import TaskService
    from tests.task_fixtures import make_manifest
    from tests.channels_fixture import UID,CONTENT_ID,ChannelsSite,fixture_adapter
    service=TaskService(tmp_path/'jobs.sqlite3',runtime_dir=tmp_path/'runtime',media_roots=[tmp_path/'media'])
    source=tmp_path/'session.json';source.write_text('{"cookies":[],"origins":[]}')
    service.bind_account('tencent','channels_test',UID,source)
    job=service.enqueue(make_manifest(tmp_path,platform='tencent',account='channels_test',title='Synthetic title',body='Single paragraph'))
    with ChannelsSite(mode) as site:
        def run():return asyncio.run(service.run_once(adapters={'tencent':fixture_adapter(service,site,delivery=True)},job_id=job['job_id'],max_attempts=1,limits={'total':20,'processing':2,'cleanup':4}))
        result=run()
        assert result is not None
        assert result['status']==expected,result
        assert site.submissions==1
        attempt=service.attempts(job['job_id'])[0]
        assert attempt['submit_count']==1
        if expected=='COMPLETED':
            assert result['submission']=='CONFIRMED' and result['content_id']==CONTENT_ID
            assert attempt['receipt_id']==(None if mode in {'no-id-receipt','missing-author','in-review'} else CONTENT_ID)
            if mode=='in-review':assert result['platform_result']=='IN_REVIEW'
            repeated=run()
            assert repeated is not None and repeated['attempt_id']==result['attempt_id'] and site.submissions==1
        elif mode=='post-challenge':
            assert service.accounts()[0]['status']=='AUTH_REQUIRED'


def test_receiptless_recovery_and_inspection_use_saved_source_proof_without_reupload(tmp_path):
    from pathlib import Path
    from orchestrator.service import TaskService
    from orchestrator.inspection import inspect_work
    from orchestrator.browser import BrowserSession
    from orchestrator.platforms.base import BrowserDeliveryAdapter
    from tests.task_fixtures import make_manifest
    from tests.channels_fixture import UID, CONTENT_ID, ChannelsSite, fixture_adapter
    from tests.test_browser_lifecycle import _installed_chromium_executable

    service = TaskService(tmp_path/'jobs.sqlite3', runtime_dir=tmp_path/'runtime', media_roots=[tmp_path/'media'])
    source = tmp_path/'session.json'; source.write_text('{"cookies":[],"origins":[]}')
    service.bind_account('tencent', 'channels_test', UID, source)
    manifest = make_manifest(tmp_path, platform='tencent', account='channels_test', title='Synthetic title', body='Single paragraph')
    job = service.enqueue(manifest)
    with ChannelsSite('filtered-list') as site:
        def run():
            return asyncio.run(service.run_once(adapters={'tencent':fixture_adapter(service,site,delivery=True)},
                job_id=job['job_id'], max_attempts=1, limits={'total':20,'processing':2,'cleanup':4}))
        first = run()
        assert first is not None and first['status'] == 'UNKNOWN_COMMIT'
        Path(manifest['media'][0]).unlink()
        site.mode = 'missing-author'
        recovered = run()
        assert recovered is not None and recovered['status'] == 'COMPLETED'
        assert recovered['attempt_id'] == first['attempt_id'] and site.submissions == 1
        assert service.attempts(job['job_id'])[0]['receipt_id'] is None
        delivery_adapter = fixture_adapter(service, site, delivery=True)
        assert isinstance(delivery_adapter, BrowserDeliveryAdapter)
        factory = delivery_adapter.port_factory
        inspected = asyncio.run(inspect_work(service, 'tencent', 'channels_test', CONTENT_ID, port_factory=factory,
            session_factory=lambda context:BrowserSession(context, launch_options={'headless':True,'executable_path':str(_installed_chromium_executable())})))
        assert inspected['status'] == 'FOUND' and site.submissions == 1
        assert inspected['work']['account_evidence'] == 'authenticated_creator_list'


def test_channels_rejects_unverified_account_id_before_browser_start(tmp_path):
    from tests.task_fixtures import make_manifest, make_service
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, platform='tencent'))
    result = asyncio.run(service.run_once(adapters=default_adapters(service.runtime_dir), job_id=job['job_id']))
    assert result is not None and result['status'] == 'FAILED_PRE_SUBMIT'
    assert result['error']['code'] == 'INVALID_INPUT'
    assert service.attempts(job['job_id'])[0]['submit_count'] == 0
    assert not any(event['stage'] == 'runtime' for event in service.events(job['job_id']))


def test_editor_resets_only_upload_resume_cache_before_proving_source_again(tmp_path):
    from pathlib import Path
    from orchestrator.service import TaskService
    from orchestrator.accounts import managed_session_path
    from tests.task_fixtures import make_manifest
    from tests.channels_fixture import UID, ChannelsSite, fixture_adapter

    service = TaskService(tmp_path/'jobs.sqlite3', runtime_dir=tmp_path/'runtime', media_roots=[tmp_path/'media'])
    with ChannelsSite() as site:
        state = {'cookies':[], 'origins':[{'origin':site.origin,'localStorage':[
            {'name':'AssistantUploadedInfoStorageKey_123','value':'[]'},
            {'name':'oauth_last_set_session','value':'retain-auth'},
            {'name':'_finger_print_device_id','value':'retain-device'}]}]}
        source = tmp_path/'session.json'; source.write_text(json.dumps(state))
        service.bind_account('tencent','channels_test',UID,source)
        job = service.enqueue(make_manifest(tmp_path,platform='tencent',account='channels_test',title='Synthetic title',body='Single paragraph'))
        result = asyncio.run(service.run_once(adapters={'tencent':fixture_adapter(service,site)},job_id=job['job_id'],
            limits={'total':20,'processing':2,'cleanup':4}))
        assert result is not None and result['status']=='READY' and site.submissions==0
        saved = json.loads(Path(managed_session_path(service.runtime_dir,'tencent','channels_test')).read_bytes())
        entries = {e['name']:e['value'] for o in saved['origins'] for e in o['localStorage']}
        assert 'AssistantUploadedInfoStorageKey_123' not in entries
        assert entries['oauth_last_set_session']=='retain-auth' and entries['_finger_print_device_id']=='retain-device'

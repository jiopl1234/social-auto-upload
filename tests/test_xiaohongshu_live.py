"""Video preparation acceptance; live accounts and publication are never used."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from typing import cast

import pytest

from orchestrator.errors import TaskError
from orchestrator.file_parts_process import file_parts
from orchestrator.platforms import default_adapters
from orchestrator.platforms.base import BrowserDeliveryAdapter
from orchestrator.platforms.xiaohongshu import XiaohongshuVideoPreparationPort
from orchestrator.worker import ExecutionContext
from orchestrator.ownership import AccountLease
from tests.task_fixtures import make_manifest, make_service
from tests.test_job_cli import run_jobs
from tests.xiaohongshu_fixture import UID, XiaohongshuSite, fixture_adapter


def job_fixture(tmp_path, *, body="First line\n\nSecond line\n", multipart=False):
    service = make_service(tmp_path)
    service.bind_account("xiaohongshu", "xhs_test", UID, tmp_path / "account-sources/xiaohongshu-creator.json")
    raw = make_manifest(tmp_path, platform="xiaohongshu", account="xhs_test", body=body)
    if multipart:
        path = tmp_path / "media/xiaohongshu-one.mp4"
        with path.open("ab") as stream:
            stream.write(b"a" * 2200000)
    return service, service.enqueue(raw)


@pytest.mark.parametrize("multipart,mode", [(False, "success"), (True, "success"), (True, "delayed-control")])
def test_prepare_preserves_paragraphs_and_never_submits(tmp_path, multipart, mode):
    service, job = job_fixture(tmp_path, multipart=multipart)
    with XiaohongshuSite(mode) as site:
        result = asyncio.run(service.run_once(adapters={"xiaohongshu": fixture_adapter(service, site)},
            job_id=job["job_id"], max_attempts=1, limits={"total": 25, "cleanup": 4}))
        assert result is not None
        assert result["status"] == "READY", result
        assert result["submission"] == "NOT_SUBMITTED"
        assert result["actual_metadata"]["body"] == "First line\n\nSecond line\n"
        assert result["actual_metadata"]["media"][0]["sha256"] == job["manifest"]["media"][0]["sha256"]
        assert site.submissions == 0
        attempt = service.attempts(job["job_id"])[0]
        assert attempt["submit_count"] == 0
        assert attempt["submission_started_at"] is None


@pytest.mark.parametrize("mode,code", [
    ("wrong-account", "IDENTITY_MISMATCH"), ("challenge", "AUTH_REQUIRED"),
    ("unknown-dialog", "UI_CHANGED"), ("scheduled", "METADATA_MISMATCH"),
    ("rich-markup", "UI_CHANGED"), ("missing-part", "TIMEOUT"),
    ("missing-complete", "TIMEOUT"), ("bad-etag", "TIMEOUT"), ("wrong-file", "TIMEOUT"),
])
def test_faults_cannot_prepare_or_publish(tmp_path, mode, code):
    service, job = job_fixture(tmp_path, multipart=True)
    with XiaohongshuSite(mode) as site:
        result = asyncio.run(service.run_once(adapters={"xiaohongshu": fixture_adapter(service, site)},
            job_id=job["job_id"], max_attempts=1, limits={"total": 25, "processing": 2, "cleanup": 4}))
        assert result is not None
        assert result["status"] != "READY", result
        assert result["error"]["code"] == code, result
        assert result["submission"] == "NOT_SUBMITTED"
        assert site.submissions == 0
        assert service.attempts(job["job_id"])[0]["submit_count"] == 0


def test_note_publication_is_rejected_before_claim_and_browser_start(tmp_path):
    service, _ = job_fixture(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, platform="xiaohongshu", account="xhs_test", key="note", content_type="note"))
    completed, result = run_jobs(service.store.path, "run", "--job", job["job_id"])
    assert completed.returncode == 5
    assert result["error"]["code"] == "UNSUPPORTED"
    assert service.attempts(job["job_id"]) == []
    assert service.status(job["job_id"])["status"] == "QUEUED"


def test_default_preparation_has_no_submission_methods(tmp_path):
    adapter = default_adapters(tmp_path, prepare_only=True)["xiaohongshu"]
    assert adapter.adapter.preparation_only is True
    assert type(adapter.adapter.port_factory()) is XiaohongshuVideoPreparationPort
    assert not hasattr(adapter, "submit")
    assert not hasattr(adapter, "reconcile")
    for method in (adapter.adapter.verify_ready, adapter.adapter.submit):
        with pytest.raises(TaskError) as error:
            asyncio.run(method(None))
        assert error.value.code == "UNSUPPORTED"
    port = XiaohongshuVideoPreparationPort()
    for method in (port.arm_submission, port.submit_once):
        with pytest.raises(TaskError) as error:
            asyncio.run(method(None))
        assert error.value.code == "UNSUPPORTED"


def test_source_changed_after_intake_cannot_create_upload_fingerprints(tmp_path):
    path = tmp_path / "file.mp4"
    path.write_bytes(b"original")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(b"replaced")
    with pytest.raises(TaskError) as error:
        file_parts({"path": str(path), "sha256": digest})
    assert error.value.code == "INVALID_INPUT"


def test_watched_fingerprint_limit_fits_child_envelope_and_rejects_oversize(tmp_path):
    from orchestrator.file_parts_process import MAX_SIZE
    from orchestrator.preflight_process import run_child_request
    path = tmp_path / "large.mp4"
    payload = b"x" * MAX_SIZE
    path.write_bytes(payload)
    observed = asyncio.run(run_child_request({"path":str(path),"sha256":hashlib.sha256(payload).hexdigest()},
                                             module="orchestrator.file_parts_process"))
    assert observed['size'] == 32 * 1024 * 1024
    assert observed['md5'] == hashlib.md5(payload).hexdigest()
    assert len(json.dumps({'ok':True,'result':observed}).encode()) < 4096
    parts = [{'size':len(payload[i:i+512*1024]),'sha256':hashlib.sha256(payload[i:i+512*1024]).hexdigest(),
              'md5':hashlib.md5(payload[i:i+512*1024]).hexdigest()} for i in range(0,len(payload),512*1024)]
    proof = asyncio.run(run_child_request({'path':str(path),'sha256':observed['sha256'],'observed_parts':parts},
                                          module='orchestrator.file_parts_process'))
    assert proof['verified'] is True
    assert len(json.dumps({'ok':True,'result':proof}).encode()) < 4096
    parts[0]['sha256'] = '0'*64
    assert file_parts({'path':str(path),'sha256':observed['sha256'],'observed_parts':parts})['verified'] is False
    with path.open('ab') as stream:
        stream.write(b'x')
    with pytest.raises(TaskError) as error:
        file_parts({'path':str(path),'sha256':observed['sha256']})
    assert error.value.code == 'UNSUPPORTED'


@pytest.mark.parametrize('first_size,completed', [(5*1024*1024,True),(2*1024*1024,False)])
def test_out_of_order_ambiguous_last_part_requires_one_complete_partition(tmp_path, first_size, completed):
    from urllib.parse import urlsplit
    path = tmp_path/'partition.mp4'
    source = b'a'*(7*1024*1024)
    path.write_bytes(source)
    port = XiaohongshuVideoPreparationPort()
    port.permitted.add('spectrum/video001')
    port.fingerprints = file_parts({'path':str(path),'sha256':hashlib.sha256(source).hexdigest()})
    port.asset = {'path':str(path),'sha256':hashlib.sha256(source).hexdigest()}
    async def exercise():
        for number,body in [(2,source[5*1024*1024:]),(1,source[:first_size])]:
            url = urlsplit('https://ros-upload-d4.xhscdn.com/spectrum/video001?partNumber='+str(number)+'&uploadId=test')
            response = SimpleNamespace(status=200,request=SimpleNamespace(method='PUT',post_data_buffer=body),
                                       headers={'etag':hashlib.md5(body).hexdigest()})
            await port._observe_storage(response,url)
            if number==2:
                assert sorted(port.parts[('spectrum/video001','test')]) == [2]
        parts = [{'md5':hashlib.md5(source[i:i+5*1024*1024]).hexdigest()}
                 for i in range(0,len(source),5*1024*1024)]
        xml = '<CompleteMultipartUpload>'+''.join('<Part><PartNumber>'+str(n)+'</PartNumber><ETag>'+p['md5']+'</ETag></Part>' for n,p in enumerate(parts,1))+'</CompleteMultipartUpload>'
        async def text():
            return '<CompleteMultipartUploadResult><Key>spectrum/video001</Key><ETag>"'+'a'*32+'-2"</ETag></CompleteMultipartUploadResult>'
        response = SimpleNamespace(status=200,request=SimpleNamespace(method='POST',post_data_buffer=xml.encode()),text=text)
        await port._observe_storage(response,urlsplit('https://ros-upload-d4.xhscdn.com/spectrum/video001?uploadId=test'))
    asyncio.run(exercise())
    assert bool(port.completed) is completed


def test_raw_delivery_adapter_cannot_open_the_preparation_port(tmp_path):
    service, job = job_fixture(tmp_path)
    def forbidden_session(context):
        raise AssertionError("No browser may start for a delivery adapter")
    adapter = BrowserDeliveryAdapter(service.runtime_dir, XiaohongshuVideoPreparationPort,
                                     session_factory=forbidden_session)
    context = SimpleNamespace(manifest=job["manifest"], attempt_id="fixture")
    with pytest.raises(TaskError) as error:
        asyncio.run(adapter.prepare(cast(ExecutionContext, context)))
    assert error.value.code == "UNSUPPORTED"


def test_unconfirmed_upload_helper_keeps_account_fenced(tmp_path, monkeypatch):
    async def unconfirmed_child(*args, **kwargs):
        raise TaskError("RESOURCES_UNCONFIRMED", "Synthetic helper shutdown failure")
    monkeypatch.setattr("orchestrator.platforms.xiaohongshu.run_child_request", unconfirmed_child)
    service, job = job_fixture(tmp_path)
    with XiaohongshuSite() as site:
        result = asyncio.run(service.run_once(adapters={"xiaohongshu": fixture_adapter(service, site)},
            job_id=job["job_id"], max_attempts=1, limits={"total": 20, "cleanup": 4}))
        assert result is not None and result["status"] == "BLOCKED"
        assert result["error"]["code"] == "RESOURCES_UNCONFIRMED"
        assert site.submissions == 0
    with pytest.raises(TaskError) as error:
        with service.maintenance("xiaohongshu", "xhs_test"):
            pass
    assert error.value.code == "ACCOUNT_BUSY"
    marker = json.loads(AccountLease(service.runtime_dir, "xiaohongshu", "xhs_test").path.read_text(encoding="utf-8"))
    assert marker["active"] is True and marker["resources_started"] is True

"""Account skill helper must stay read-only and return no identity after unsafe cleanup."""
import asyncio
import importlib.util
from pathlib import Path

import pytest

from orchestrator.errors import TaskError
from tests.task_fixtures import make_service

PATH = Path(__file__).resolve().parents[1] / 'skills/social-auto-upload/scripts/identify_account.py'
SPEC = importlib.util.spec_from_file_location('skill_identity', PATH)
assert SPEC and SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class Page:
    async def route(self, pattern, handler):
        self.guard = handler


class Session:
    def __init__(self, context, fail=False):
        self.context = context
        self.fail = fail
        self.closed = False

    async def open(self, source):
        self.context.resources_started()
        return Page()

    async def close(self):
        self.closed = True
        if self.fail:
            raise TaskError('CLEANUP_FAILED', 'Synthetic cleanup failed')
        self.context.resources_closed()


class Port:
    origin = 'https://creator.douyin.com'

    def __init__(self, ids=('creator-id', 'creator-id'), challenge=False):
        self.ids = iter(ids)
        self.challenge = challenge
        self.readonly = None

    async def navigate(self, page, content_type, *, readonly):
        assert content_type == 'video'
        self.readonly = readonly

    async def check_challenge(self, page):
        if self.challenge:
            raise TaskError('AUTH_REQUIRED', 'Synthetic challenge')

    async def identify_account(self, page):
        return next(self.ids)

    async def upload(self, *args):
        raise AssertionError('Identity observation must not upload')

    async def submit_once(self, *args):
        raise AssertionError('Identity observation must not submit')


@pytest.mark.parametrize('platform', ['douyin', 'xiaohongshu', 'tencent'])
def test_identity_has_no_binding_job_or_publication_side_effects(tmp_path, platform):
    service = make_service(tmp_path)
    port = Port()
    sessions = []
    before = service.accounts()
    def factory(context):
        session = Session(context)
        sessions.append(session)
        return session
    result = asyncio.run(helper.identify(service, platform, 'new_alias', tmp_path / 'source.json',
        port_factory=lambda: port, session_factory=factory))
    assert result['account_id'] == 'creator-id' and result['status'] == 'IDENTIFIED'
    assert port.readonly is True and sessions[0].closed
    assert service.list_jobs() == [] and service.accounts() == before


@pytest.mark.parametrize('mode,code', [('challenge', 'AUTH_REQUIRED'), ('changed', 'IDENTITY_MISMATCH'),
                                       ('cleanup', 'CLEANUP_FAILED'), ('existing', 'IDENTITY_MISMATCH')])
def test_identity_failures_never_return_a_bindable_result(tmp_path, mode, code):
    service = make_service(tmp_path)
    if mode == 'existing':
        source = tmp_path / 'session.json'
        source.write_text('{"cookies": [], "origins": []}', encoding='utf-8')
        service.bind_account('douyin', 'new_alias', 'another-id', source)
    before = service.accounts()
    port = Port(ids=('creator-id', 'changed-id') if mode == 'changed' else ('creator-id', 'creator-id'),
                challenge=mode == 'challenge')
    sessions = []
    def factory(context):
        session = Session(context, fail=mode == 'cleanup')
        sessions.append(session)
        return session
    with pytest.raises(TaskError) as failure:
        asyncio.run(helper.identify(service, 'douyin', 'new_alias', tmp_path / 'source.json',
            port_factory=lambda: port, session_factory=factory))
    assert failure.value.code == code and sessions[0].closed
    assert service.list_jobs() == [] and service.accounts() == before

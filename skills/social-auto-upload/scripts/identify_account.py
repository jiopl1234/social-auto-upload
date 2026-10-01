"""Bounded online identity observation; never bind, enqueue, upload or publish."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from orchestrator.accounts import validate_account
from orchestrator.browser import BrowserSession
from orchestrator.errors import TaskError
from orchestrator.inspection import InspectionContext
from orchestrator.platforms.channels_delivery import ChannelsVideoPort
from orchestrator.platforms.profiles import create_port
from orchestrator.service import TaskService
from orchestrator.worker import _cleanup


async def identify(service, platform, account, source, *, headed=False, port_factory=None, session_factory=None):
    validate_account(platform, account)
    if platform not in {'douyin', 'xiaohongshu', 'tencent'}:
        raise TaskError('UNSUPPORTED', 'Online identity helper supports three video platforms', field='platform')
    with service.maintenance(platform, account) as lease:
        context = InspectionContext(lease)
        session = session_factory(context) if session_factory else BrowserSession(
            context, launch_options={'headless': not headed, 'channel': 'chromium'})
        error = None
        uid = None
        warnings = []
        try:
            port = port_factory() if port_factory else create_port(platform)
            if isinstance(port, ChannelsVideoPort):
                port.bind_context(context)
            page = await session.open(Path(source))
            if platform == 'douyin':
                # Fixed creator-origin guard; no injected global route or CDN interception.
                origin = 'https://creator.douyin.com'
                async def block_writes(route):
                    if (urlsplit(route.request.url).netloc == urlsplit(origin).netloc
                            and route.request.method not in {'GET', 'HEAD', 'OPTIONS'}):
                        await route.abort()
                    else:
                        await route.fallback()
                await context.run_stage('navigation', lambda: page.route(origin + '/**', block_writes))
            # XHS and Channels implement their own read-only request guards.
            await context.run_stage('navigation', lambda: port.navigate(page, 'video', readonly=True))
            await context.run_stage('metadata', lambda: port.check_challenge(page))
            uid = await context.run_stage('metadata', lambda: port.identify_account(page))
            await context.run_stage('metadata', lambda: port.check_challenge(page))
            refreshed = await context.run_stage('metadata', lambda: port.identify_account(page))
            if (not isinstance(uid, str) or not uid or len(uid) > 256 or any(ord(c) < 32 for c in uid)
                    or uid != refreshed):
                raise TaskError('IDENTITY_MISMATCH', 'Online account identity is inconsistent', field='account')
            existing = next((a for a in service.accounts() if a['platform'] == platform and a['account'] == account), None)
            if existing and existing['expected_id'] not in (None, uid):
                raise TaskError('IDENTITY_MISMATCH', 'Use a new alias for a different account', field='account')
        except BaseException as exc:
            error = exc
        finally:
            cleanup = await _cleanup(session, context, warnings)  # type: ignore[arg-type]
        if cleanup is not None:
            error = cleanup
        if error is not None:
            raise error
        if warnings:
            raise TaskError('RESOURCES_UNCONFIRMED', 'Identity observation cleanup requires attention')
        return {'status': 'IDENTIFIED', 'platform': platform, 'account': account, 'account_id': uid,
                'observed_at': datetime.now(timezone.utc).isoformat()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=['douyin', 'xiaohongshu', 'tencent'], required=True)
    parser.add_argument('--account', required=True)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--runtime-dir', type=Path)
    parser.add_argument('--session-source', type=Path, required=True)
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args(argv)
    try:
        service = TaskService(args.db, runtime_dir=args.runtime_dir)
        result = asyncio.run(identify(service, args.platform, args.account, args.session_source, headed=args.headed))
    except TaskError as error:
        result = {'status': 'ERROR', 'error': error.as_dict()}
    except (KeyboardInterrupt, asyncio.CancelledError):
        result = {'status': 'ERROR', 'error': {'code': 'CANCELLED', 'message': 'Identity observation cancelled'}}
    except Exception:
        result = {'status': 'ERROR', 'error': {'code': 'INTERNAL_ERROR', 'message': 'Identity observation failed; no identity was inferred'}}
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result['status'] == 'IDENTIFIED' else 1


if __name__ == '__main__':
    raise SystemExit(main())

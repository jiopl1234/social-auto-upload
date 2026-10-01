"""Bounded, read-only inspection using the same account/session ownership."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import re

from orchestrator.accounts import get_binding, managed_session_path
from orchestrator.browser import BrowserSession
from orchestrator.errors import TaskError
from orchestrator.runtime import ExecutionBudget, validate_limits


@dataclass
class InspectionContext:
    lease: Any
    budget: ExecutionBudget = field(default_factory=lambda: ExecutionBudget(validate_limits({"total": 120})))

    def assert_owner(self):
        self.lease.assert_owner()

    def resources_started(self):
        self.lease.resources_started()

    def resources_closed(self):
        self.lease.resources_closed()

    def record_runtime(self, details):
        pass

    def time_left(self, stage):
        return self.budget.time_left(stage)

    async def run_stage(self, stage, operation):
        if stage != "cleanup":
            self.assert_owner()
        return await self.budget.run(stage, operation)


async def inspect_work(service, platform: str, account: str, content_id: str, *, headed: bool = False,
                       port_factory=None, session_factory=None) -> dict:
    from orchestrator.platforms.douyin import DouyinVideoPort
    from orchestrator.platforms.channels_delivery import ChannelsVideoPort, CONTENT_PATTERN
    from orchestrator.worker import PAUSE_CODES, _cleanup

    if (platform not in {'douyin', 'tencent'} or not isinstance(content_id, str)
            or (not content_id.isdigit() if platform == 'douyin' else not re.fullmatch(CONTENT_PATTERN, content_id))):
        raise TaskError("UNSUPPORTED", "Requested capability is unsupported", field="platform")
    binding = get_binding(service.store, service.runtime_dir, platform, account)
    with service.maintenance(platform, account) as lease:
        context = InspectionContext(lease)
        session = (session_factory(context) if session_factory else BrowserSession(context,
            launch_options={"headless": not headed, "channel": "chromium"}))
        error = None
        result = None
        warnings: list[dict] = []
        try:
            port = port_factory() if port_factory else DouyinVideoPort() if platform == 'douyin' else ChannelsVideoPort()
            if isinstance(port, ChannelsVideoPort):
                port.bind_context(context)
                port.set_reconciliation_target(content_id)
            page = await session.open(managed_session_path(service.runtime_dir, platform, account))
            await context.run_stage("navigation", lambda: port.navigate(page, "video", readonly=True))
            await context.run_stage("metadata", lambda: port.check_challenge(page))
            uid = await context.run_stage("metadata", lambda: port.identify_account(page))
            if uid != binding["expected_id"]:
                raise TaskError("IDENTITY_MISMATCH", "The active account does not match the bound account", field="account")
            works = await context.run_stage("reconcile", lambda: port.list_works(page, "video"))
            if platform == 'tencent':
                await context.run_stage('metadata', lambda: port.check_challenge(page))
                refreshed = await context.run_stage('metadata', lambda: port.identify_account(page))
                if refreshed != binding['expected_id']:
                    raise TaskError('IDENTITY_MISMATCH', 'The active account does not match the bound account', field='account')
            matches = [work for work in works if work["content_id"] == content_id and work["account_id"] == uid]
            result = {"platform": platform, "account": account, "account_id": uid,
                      "content_id": content_id, "status": "FOUND" if len(matches) == 1 else "NOT_FOUND",
                      "work": matches[0] if len(matches) == 1 else None,
                      "verified_at": datetime.now(timezone.utc).isoformat()}
        except BaseException as exc:
            error = exc
        finally:
            # The coordinator cleanup contract is structural: it requires the
            # budget and lease methods shared by both context types.
            cleanup = await _cleanup(session, context, warnings)  # type: ignore[arg-type]
        if cleanup is not None:
            if error is None:
                error = cleanup
            else:
                warnings.append(cleanup.as_dict())
        pause = next((item for item in ([error.as_dict()] if isinstance(error, TaskError) else []) + warnings
                      if item["code"] in PAUSE_CODES), None)
        if pause is not None:
            service.store.pause_account(platform, account, pause)
        if error is not None:
            raise error
        assert result is not None
        result["warnings"] = warnings
        return result

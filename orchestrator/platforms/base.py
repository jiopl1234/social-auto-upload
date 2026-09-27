"""Browser delivery owns one attempt; worker owns the durable submission boundary."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

from orchestrator.accounts import managed_session_path
from orchestrator.browser import BrowserSession
from orchestrator.errors import TaskError
from orchestrator.metadata import validate_prepared
from orchestrator.worker import ExecutionContext


class PagePort(Protocol):
    platform: str
    content_types: frozenset[str]
    capability_version: str
    verification_scope: str
    edit_url: str

    async def navigate(self, page: Any, content_type: str, *, readonly: bool) -> None: ...
    async def identify_account(self, page: Any) -> str: ...
    async def check_challenge(self, page: Any) -> None: ...
    async def upload(self, page: Any, manifest: Mapping[str, Any]) -> None: ...
    async def wait_media(self, page: Any, manifest: Mapping[str, Any]) -> None: ...
    async def apply_fields(self, page: Any, manifest: Mapping[str, Any]) -> None: ...
    async def readback(self, page: Any) -> dict: ...
    async def arm_submission(self, page: Any) -> None: ...
    async def submit_once(self, page: Any) -> dict | None: ...
    async def list_works(self, page: Any, content_type: str) -> list[dict]: ...


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port


class BrowserDeliveryAdapter:
    def __init__(self, runtime_dir: Path, port_factory: Callable[[], PagePort], *,
                 session_factory: Callable[[ExecutionContext], BrowserSession] | None = None):
        self.runtime_dir = Path(runtime_dir)
        self.port_factory = port_factory
        self.session_factory = session_factory or BrowserSession
        self.session: BrowserSession | None = None
        self.port: PagePort | None = None
        self.page: Any = None
        self.attempt_id: str | None = None
        self.submission_started = False
        self.verified_for: str | None = None
        self.cleanup_warnings: list[dict] = []

    def supports(self, manifest: Mapping[str, Any]) -> bool:
        port = self.port_factory()
        return port.platform == manifest["platform"] and manifest["content_type"] in port.content_types

    async def _open(self, context: ExecutionContext) -> tuple[PagePort, Any]:
        if self.session is not None:
            if self.attempt_id != context.attempt_id or self.port is None:
                raise TaskError("INVALID_STATE", "Adapter is already serving another attempt")
            return self.port, self.page
        port = self.port_factory()
        if port.platform != context.manifest["platform"] or context.manifest["content_type"] not in port.content_types:
            raise TaskError("UNSUPPORTED", "Content path is not supported", field="content_type")
        from orchestrator.platforms.douyin import DouyinVideoPort

        live = (type(port) is DouyinVideoPort and port.verification_scope == "douyin_video_v1"
                and port.edit_url == "https://creator.douyin.com/creator-micro/content/upload")
        if not live and port.verification_scope != "local_fixture":
            raise TaskError("UNSUPPORTED", "Live account and work evidence has not been verified", field="platform")
        parsed = urlsplit(port.edit_url)
        if not live and not (parsed.scheme == "file" or (parsed.scheme in {"http", "https"}
                and parsed.hostname in {"127.0.0.1", "localhost", "::1"})):
            raise TaskError("UNSUPPORTED", "Local evidence cannot enable a production origin", field="platform")
        if live and not str(context.manifest["expected_account_id"]).isdigit():
            raise TaskError("INVALID_INPUT", "Expected account ID is required", field="expected_id")
        validator = getattr(port, "validate_scope", None)
        if callable(validator):
            validator(context.manifest)
        self.port, self.attempt_id = port, context.attempt_id
        self.submission_started = False
        self.verified_for = None
        self.cleanup_warnings.clear()
        session = self.session_factory(context)
        self.session = session
        session_path = managed_session_path(self.runtime_dir, port.platform, str(context.manifest["account"]))
        self.page = await session.open(session_path)

        async def local_only(route: Any) -> None:
            if self._allowed_url(route.request.url):
                await route.continue_()
            else:
                await route.abort()

        if not live:
            await self.page.context.route("**/*", local_only)
        context.record_runtime({"capability_version": port.capability_version,
                                "evidence_scope": "live" if live else "offline"})
        return port, self.page

    def _allowed_url(self, url: str) -> bool:
        if self.port is None:
            return False
        expected, actual = urlsplit(self.port.edit_url), urlsplit(url)
        if expected.scheme == "file":
            return actual.scheme == "file" and actual.path == expected.path
        return _origin(url) == _origin(self.port.edit_url)

    async def _guard(self, context: ExecutionContext) -> None:
        port = self.port
        if port is None or not self._allowed_url(self.page.url):
            raise TaskError("UI_CHANGED", "Page origin no longer matches its evidence scope")
        context.assert_owner()
        await port.check_challenge(self.page)
        identity = await port.identify_account(self.page)
        if not isinstance(identity, str) or not identity.strip():
            raise TaskError("UI_CHANGED", "Stable account identity could not be read", field="account")
        if identity != context.manifest["expected_account_id"]:
            raise TaskError("IDENTITY_MISMATCH", "The active account does not match the bound account", field="account")

    async def prepare(self, context: ExecutionContext) -> dict:
        port, page = await self._open(context)
        await context.run_stage("navigation", lambda: port.navigate(page, str(context.manifest["content_type"]), readonly=False))
        await context.run_stage("metadata", lambda: self._guard(context))
        await context.run_stage("upload", lambda: port.upload(page, context.manifest))
        await context.run_stage("processing", lambda: port.wait_media(page, context.manifest))
        media_observation = getattr(port, "media_observation", None)
        if isinstance(media_observation, dict):
            context.store.record_video_upload(context.job_id, context.lease.token, **media_observation)
        await context.run_stage("metadata", lambda: self._guard(context))
        await context.run_stage("metadata", lambda: port.apply_fields(page, context.manifest))
        actual = await context.run_stage("metadata", lambda: port.readback(page))
        await context.run_stage("metadata", lambda: self._guard(context))
        if self.session is None:
            raise TaskError("INVALID_STATE", "Browser session is unavailable")
        await self.session.save_state(managed_session_path(self.runtime_dir, port.platform, str(context.manifest["account"])))
        return actual

    async def verify_ready(self, context: ExecutionContext) -> None:
        if self.session is None or self.port is None or self.attempt_id != context.attempt_id or self.submission_started:
            raise TaskError("INVALID_STATE", "This attempt cannot submit again")
        await self._guard(context)
        actual, _ = validate_prepared(context.manifest, await self.port.readback(self.page))
        persisted = context.store.get(context.job_id)
        if actual != persisted["actual_metadata"]:
            raise TaskError("METADATA_MISMATCH", "Ready metadata changed before submission", field="actual_metadata")
        await self.port.arm_submission(self.page)
        await self._guard(context)
        self.verified_for = context.attempt_id

    async def submit(self, context: ExecutionContext) -> dict | None:
        port = self.port
        if port is None or self.verified_for != context.attempt_id or self.submission_started:
            raise TaskError("INVALID_STATE", "Final action was not prepared for this attempt")
        # All definitely-no-action checks ran before the durable boundary.
        # From here on, a click error or cancellation is conservatively unknown.
        self.submission_started = True
        try:
            return await port.submit_once(self.page)
        finally:
            receipt = getattr(port, "observed_receipt", None)
            if isinstance(receipt, dict) and isinstance(receipt.get("content_id"), str):
                context.store.record_receipt(context.job_id, context.lease.token, receipt["content_id"])
            observer = getattr(port, "receipt_task", None)
            if isinstance(observer, asyncio.Task):
                context.budget.track(observer)

    async def reconcile(self, context: ExecutionContext) -> list[dict]:
        port, page = await self._open(context)
        if self.submission_started:
            # On this attempt the editor is still open. Inspect it before a
            # read-only navigation removes a post-click verification prompt.
            # Recovery opens a blank page and has no editor to inspect yet.
            await port.check_challenge(page)
        await context.run_stage("navigation", lambda: port.navigate(page, str(context.manifest["content_type"]), readonly=True))
        await self._guard(context)
        return await port.list_works(page, str(context.manifest["content_type"]))

    async def close(self) -> None:
        if self.session is not None:
            try:
                await self.session.close()
            finally:
                self.cleanup_warnings = list(self.session.cleanup_warnings)
                # A failed close remains owned by this adapter and by the lease.
                if not self.cleanup_warnings:
                    self.session, self.page, self.port = None, None, None


class PreparationOnlyAdapter:
    """Explicit CLI preparation, with no submission or reconciliation methods."""
    def __init__(self, adapter: BrowserDeliveryAdapter):
        self.adapter = adapter

    @property
    def cleanup_warnings(self) -> list[dict]:
        return self.adapter.cleanup_warnings

    def supports(self, manifest: Mapping[str, Any]) -> bool:
        return self.adapter.supports(manifest)

    async def prepare(self, context: ExecutionContext) -> dict:
        actual = await self.adapter.prepare(context)
        checked, _ = validate_prepared(context.manifest, actual)
        return checked

    async def close(self) -> None:
        await self.adapter.close()

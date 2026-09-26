"""Bounded ownership of a Patchright browser and managed storage state."""

from __future__ import annotations

import asyncio
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Mapping

from orchestrator.accounts import _storage_state_bytes
from orchestrator.errors import TaskError
from orchestrator.preflight_process import run_child_request


PATCHRIGHT_PIN = "1.58.2"
_SAFE_VERSION = re.compile(r"[A-Za-z0-9 ._+\-]{1,100}\Z")
_SAFE_CHANNELS = frozenset({"chromium", "chrome", "chrome-beta", "chrome-dev", "msedge"})


async def _start_patchright() -> Any:
    # Import only when a task actually needs a browser. The uploader dependency
    # is pinned in pyproject.toml; this path intentionally uses the same driver.
    from patchright.async_api import async_playwright

    return await async_playwright().start()


def _driver_version() -> str:
    try:
        installed = version("patchright")
    except PackageNotFoundError:
        return "unavailable"
    return installed if _SAFE_VERSION.fullmatch(installed) else "unavailable"


def _safe_launch_options(options: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    if type(options.get("headless")) is bool:
        safe["headless"] = options["headless"]
    channel = options.get("channel")
    if isinstance(channel, str):
        safe["channel"] = channel if channel in _SAFE_CHANNELS else "custom"
    return safe


def _browser_version(browser: Any) -> str:
    try:
        value = browser.version
    except Exception:
        return "unavailable"
    return value if isinstance(value, str) and _SAFE_VERSION.fullmatch(value) else "unavailable"


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    # A resource close that ignored cancellation is deliberately left fenced.
    # Consume any late exception without printing browser internals to logs.
    if not task.cancelled():
        try:
            task.exception()
        except BaseException:
            pass


class BrowserSession:
    """One task's browser, context, page, and managed storage state."""

    def __init__(
        self,
        context: Any,
        *,
        driver_factory: Callable[[], Awaitable[Any]] | None = None,
        launch_options: Mapping[str, Any] | None = None,
    ) -> None:
        self.context = context
        self.driver_factory = driver_factory or _start_patchright
        self.launch_options = dict(launch_options) if launch_options is not None else {
            "headless": True, "channel": "chromium",
        }
        self.driver: Any | None = None
        self.browser: Any | None = None
        self.browser_context: Any | None = None
        self.page: Any | None = None
        self.cleanup_warnings: list[dict[str, Any]] = []
        self._open_attempted = False
        self._resources_started = False
        self._creation_uncertain = False
        self._fully_closed = False

    async def open(self, session_path: Path, init_script: Path | None = None) -> Any:
        if self._open_attempted or self._fully_closed:
            raise TaskError("INVALID_STATE", "Browser session cannot be opened again")
        self._open_attempted = True
        path = Path(session_path)

        async def initialize() -> Any:
            # The caller supplies an account's managed storage-state path. Check
            # its shape before any external process is started.
            _storage_state_bytes(path)
            self.context.resources_started()
            self._resources_started = True
            self._creation_uncertain = True
            driver = await self.driver_factory()
            self.driver = driver
            browser = await driver.chromium.launch(**self.launch_options)
            self.browser = browser
            self._creation_uncertain = False
            self.context.record_runtime({
                "driver": "patchright",
                "driver_version": _driver_version(),
                "driver_pin": PATCHRIGHT_PIN,
                "browser_version": _browser_version(browser),
                "launch_options": _safe_launch_options(self.launch_options),
            })
            browser_context = await browser.new_context(storage_state=str(path))
            self.browser_context = browser_context
            if init_script is not None:
                await browser_context.add_init_script(path=str(init_script))
            page = await browser_context.new_page()
            self.page = page
            return page

        return await self.context.run_stage("navigation", initialize)

    async def save_state(self, path: Path) -> None:
        browser_context = self.browser_context
        if browser_context is None:
            raise TaskError("INVALID_STATE", "Browser context is unavailable")

        async def save() -> None:
            try:
                state = await browser_context.storage_state()
                payload = json.dumps(state, ensure_ascii=False, allow_nan=False).encode("utf-8")
                if len(payload) > 5_000_000:
                    raise ValueError("Storage state exceeds the managed limit")
                await run_child_request({"path": str(path), "state": state},
                                        module="orchestrator.session_process")
            except TaskError as exc:
                if exc.code == "RESOURCES_UNCONFIRMED":
                    self._creation_uncertain = True
                    raise
                raise TaskError("STORAGE_ERROR", "Browser session state could not be saved") from None
            except Exception:
                # Exception strings can contain cookie values or local paths.
                raise TaskError("STORAGE_ERROR", "Browser session state could not be saved") from None

        await self.context.run_stage("session_save", save)

    async def _close_one(self, name: str, resource: Any, remaining_count: int) -> BaseException | None:
        try:
            remaining = self.context.time_left("cleanup")
        except BaseException as exc:
            self.cleanup_warnings.append({
                "code": "CLEANUP_FAILED", "message": f"{name} shutdown budget could not be read", "field": None,
            })
            return exc
        if not isinstance(remaining, (int, float)) or not math.isfinite(remaining) or remaining <= 0:
            self.cleanup_warnings.append({
                "code": "CLEANUP_FAILED", "message": f"{name} shutdown exceeded the cleanup deadline", "field": None,
            })
            return TimeoutError()
        budget = remaining / remaining_count
        operation: asyncio.Task[Any] | None = None
        try:
            close_method = resource.stop if name == "driver" else resource.close
            operation = asyncio.create_task(self.context.run_stage("cleanup", close_method))
            done, _ = await asyncio.wait({operation}, timeout=budget)
            if not done:
                operation.cancel()
                operation.add_done_callback(_consume_task_result)
                self.cleanup_warnings.append({
                    "code": "CLEANUP_FAILED", "message": f"{name} shutdown exceeded its time limit", "field": None,
                })
                return TimeoutError()
            await operation
            return None
        except BaseException as exc:
            if operation is not None and not operation.done():
                operation.cancel()
                operation.add_done_callback(_consume_task_result)
            self.cleanup_warnings.append({
                "code": "CLEANUP_FAILED", "message": f"{name} shutdown could not be confirmed", "field": None,
            })
            return exc

    async def close(self) -> None:
        if self._fully_closed:
            return
        self.cleanup_warnings.clear()
        resources = [
            ("page", self.page),
            ("context", self.browser_context),
            ("browser", self.browser),
            ("driver", self.driver),
        ]
        resources = [(name, resource) for name, resource in resources if resource is not None]
        failures: list[BaseException] = []
        for index, (name, resource) in enumerate(resources):
            failure = await self._close_one(name, resource, len(resources) - index)
            if failure is None:
                if name == "page":
                    self.page = None
                elif name == "context":
                    self.browser_context = None
                elif name == "browser":
                    self.browser = None
                else:
                    self.driver = None
            else:
                failures.append(failure)
        if self._creation_uncertain:
            failures.append(TaskError("RESOURCES_UNCONFIRMED", "Browser creation did not return a confirmed resource"))
            self.cleanup_warnings.append({
                "code": "CLEANUP_FAILED", "message": "Browser creation was not confirmed", "field": None,
            })
        if self._resources_started and not failures:
            try:
                self.context.resources_closed()
            except BaseException as exc:
                failures.append(exc)
                self.cleanup_warnings.append({
                    "code": "CLEANUP_FAILED", "message": "Browser shutdown could not be confirmed", "field": None,
                })
        if not failures:
            self._fully_closed = True
            return
        cancellation = next((item for item in failures if isinstance(item, asyncio.CancelledError)), None)
        if cancellation is not None:
            raise cancellation
        fatal = next((item for item in failures if not isinstance(item, Exception)), None)
        if fatal is not None:
            raise fatal
        raise TaskError("RESOURCES_UNCONFIRMED", "Browser resource shutdown could not be confirmed")

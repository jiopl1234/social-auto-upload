"""Browser resource behavior through the public task service and a local driver."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil

import pytest

from orchestrator.accounts import managed_session_path
from orchestrator.browser import BrowserSession
from tests.task_fixtures import make_manifest, make_service


class LocalPage:
    def __init__(self, harness):
        self.harness = harness
        self.closed = False

    async def close(self):
        self.harness.calls.append("page.close")
        self.closed = True
        if "page.close" in self.harness.faults:
            raise RuntimeError("private page content")


class LocalContext:
    def __init__(self, harness):
        self.harness = harness
        self.page: LocalPage | None = None
        self.closed = False

    async def add_init_script(self, *, path):
        self.harness.calls.append("context.add_init_script")
        assert Path(path).is_file()
        if "init" in self.harness.faults:
            raise RuntimeError("private script content")

    async def new_page(self):
        self.harness.calls.append("context.new_page")
        if "page" in self.harness.faults:
            raise RuntimeError("private page content")
        if "page_stall" in self.harness.faults:
            self.harness.page_started.set()
            await asyncio.Future()
        self.page = LocalPage(self.harness)
        return self.page

    async def storage_state(self):
        self.harness.calls.append("context.storage_state")
        if "save" in self.harness.faults:
            raise RuntimeError("secret cookie value")
        if "invalid_state" in self.harness.faults:
            return {"cookies": "secret cookie value", "origins": []}
        if "fresh_state" in self.harness.faults:
            return {"cookies": [], "origins": [{"origin": "https://local.invalid", "localStorage": [
                {"name": "saved", "value": "yes"},
            ]}]}
        return {"cookies": [], "origins": []}

    async def close(self):
        self.harness.calls.append("context.close")
        if "context.stall" in self.harness.faults:
            await asyncio.Future()
        self.closed = True
        if "context.close" in self.harness.faults:
            raise RuntimeError("secret cookie value")


class LocalBrowser:
    version = "Chromium-local-127"

    def __init__(self, harness):
        self.harness = harness
        self.context: LocalContext | None = None
        self.closed = False

    async def new_context(self, *, storage_state):
        self.harness.calls.append("browser.new_context")
        assert Path(storage_state).is_file()
        if "context" in self.harness.faults:
            raise RuntimeError("secret cookie value")
        self.context = LocalContext(self.harness)
        return self.context

    async def close(self):
        self.harness.calls.append("browser.close")
        self.closed = True
        if "browser.close" in self.harness.faults:
            raise RuntimeError("secret cookie value")


class LocalChromium:
    def __init__(self, harness):
        self.harness = harness

    async def launch(self, **kwargs):
        self.harness.calls.append("chromium.launch")
        self.harness.launch_options = kwargs
        context = self.harness.context
        assert context is not None
        self.harness.fenced_at_launch = context.lease.record["resources_started"]
        if "launch" in self.harness.faults:
            raise RuntimeError("private launch path")
        self.harness.browser = LocalBrowser(self.harness)
        return self.harness.browser


class LocalDriver:
    def __init__(self, harness):
        self.harness = harness
        self.chromium = LocalChromium(harness)
        self.stopped = False

    async def stop(self):
        self.harness.calls.append("driver.stop")
        self.stopped = True
        if "driver.stop" in self.harness.faults:
            raise RuntimeError("private driver state")


class DriverHarness:
    def __init__(self, *faults):
        self.faults = set(faults)
        self.calls = []
        self.context = None
        self.driver: LocalDriver | None = None
        self.browser: LocalBrowser | None = None
        self.launch_options = None
        self.fenced_at_launch = False
        self.page_started = asyncio.Event()

    async def start(self):
        self.calls.append("driver.start")
        driver = LocalDriver(self)
        self.driver = driver
        return driver


class BrowserPreparation:
    def __init__(self, session_path: Path, harness: DriverHarness, *, script: Path | None = None,
                 save: bool = False):
        self.session_path = session_path
        self.harness = harness
        self.script = script
        self.save = save
        self.session: BrowserSession | None = None
        self.cleanup_warnings = []

    async def prepare(self, context):
        self.harness.context = context
        self.session = BrowserSession(context, driver_factory=self.harness.start,
                                      launch_options={"headless": True, "channel": "chromium"})
        await self.session.open(self.session_path, init_script=self.script)
        if self.save:
            await self.session.save_state(self.session_path)
        return {"title": context.manifest["title"], "body": context.manifest["body"]}

    async def close(self):
        session = self.session
        if session is not None:
            try:
                await session.close()
            finally:
                self.cleanup_warnings = session.cleanup_warnings


def _session_path(service):
    return managed_session_path(service.runtime_dir, "douyin", "creator")


def _run(service, job, adapter, **kwargs):
    result = asyncio.run(service.run_once(adapters={"douyin": adapter}, job_id=job["job_id"], **kwargs))
    assert result is not None
    return result


def _assert_outer_closed(harness: DriverHarness) -> LocalBrowser:
    browser = harness.browser
    driver = harness.driver
    assert browser is not None and browser.closed
    assert driver is not None and driver.stopped
    return browser


def _assert_context_closed(harness: DriverHarness, *, page: bool = False) -> None:
    browser = _assert_outer_closed(harness)
    browser_context = browser.context
    assert browser_context is not None and browser_context.closed
    if page:
        browser_page = browser_context.page
        assert browser_page is not None and browser_page.closed


def test_context_creation_failure_closes_browser_and_driver_then_next_job_runs(tmp_path):
    service = make_service(tmp_path)
    failed = service.enqueue(make_manifest(tmp_path, key="first"))
    next_job = service.enqueue(make_manifest(tmp_path, key="second"))
    first_driver = DriverHarness("context")
    first = _run(service, failed, BrowserPreparation(_session_path(service), first_driver))
    assert first["status"] == "FAILED_PRE_SUBMIT"
    assert first["error"]["code"] == "PREPARATION_FAILED"
    assert first_driver.fenced_at_launch
    _assert_outer_closed(first_driver)
    second_driver = DriverHarness()
    second = _run(service, next_job, BrowserPreparation(_session_path(service), second_driver))
    assert second["status"] == "READY"
    _assert_context_closed(second_driver)


@pytest.mark.parametrize("fault", ["init", "page"])
def test_initialization_failures_close_every_created_resource(tmp_path, fault):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    script = tmp_path / "local-init.js"
    script.write_text("window.localOnly = true", encoding="utf-8")
    harness = DriverHarness(fault)
    result = _run(service, job, BrowserPreparation(_session_path(service), harness, script=script))
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "PREPARATION_FAILED"
    _assert_context_closed(harness)


@pytest.mark.parametrize("fault", ["save", "invalid_state"])
def test_state_save_failure_keeps_previous_session_and_closes_resources(tmp_path, fault):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    previous = _session_path(service).read_bytes()
    harness = DriverHarness(fault)
    result = _run(service, job, BrowserPreparation(_session_path(service), harness, save=True))
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert _session_path(service).read_bytes() == previous
    _assert_context_closed(harness)
    assert "secret cookie value" not in json.dumps(result)


def test_context_close_failure_still_closes_browser_and_driver_and_keeps_primary_error(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    other = service.enqueue(make_manifest(tmp_path, platform="tencent", key="other"))
    harness = DriverHarness("page", "context.close")
    result = _run(service, job, BrowserPreparation(_session_path(service), harness))
    assert result["status"] == "BLOCKED"
    assert result["error"]["code"] == "PREPARATION_FAILED"
    assert any(warning["code"] == "RESOURCES_UNCONFIRMED" for warning in result["warnings"])
    assert harness.calls.index("browser.close") > harness.calls.index("context.close")
    _assert_outer_closed(harness)
    assert "secret cookie value" not in json.dumps(result)
    other_result = asyncio.run(service.run_once(
        adapters={"tencent": BrowserPreparation(
            managed_session_path(service.runtime_dir, "tencent", "creator"), DriverHarness())},
        job_id=other["job_id"],
    ))
    assert other_result is not None
    assert other_result["status"] == "READY"


def test_successful_session_save_records_versions_without_session_data(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    harness = DriverHarness("fresh_state")
    result = _run(service, job, BrowserPreparation(_session_path(service), harness, save=True))
    assert result["status"] == "READY"
    assert harness.launch_options == {"headless": True, "channel": "chromium"}
    _assert_context_closed(harness, page=True)
    assert json.loads(_session_path(service).read_text(encoding="utf-8")) == {
        "cookies": [], "origins": [{"origin": "https://local.invalid", "localStorage": [
            {"name": "saved", "value": "yes"},
        ]}],
    }
    events = json.dumps(service.events(job["job_id"]))
    assert "Chromium-local-127" in events
    assert "patchright" in events
    assert "cookies" not in events


def test_stalled_context_close_has_a_slice_and_browser_driver_are_still_attempted(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    harness = DriverHarness("context.stall")
    result = _run(service, job, BrowserPreparation(_session_path(service), harness),
                  limits={"total": 1.0, "cleanup": 0.2})
    assert result["status"] == "BLOCKED"
    assert result["error"]["code"] == "RESOURCES_UNCONFIRMED"
    assert "context.close" in harness.calls
    assert "browser.close" in harness.calls and "driver.stop" in harness.calls
    _assert_outer_closed(harness)


def test_multiple_close_failures_are_sanitized_and_all_closes_are_attempted(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    harness = DriverHarness("page.close", "context.close", "browser.close", "driver.stop")
    result = _run(service, job, BrowserPreparation(_session_path(service), harness))
    assert result["status"] == "BLOCKED"
    assert result["error"]["code"] == "RESOURCES_UNCONFIRMED"
    assert harness.calls[-4:] == ["page.close", "context.close", "browser.close", "driver.stop"]
    diagnostic_messages = {warning["message"] for warning in result["warnings"]}
    assert {f"{name} shutdown could not be confirmed" for name in (
        "page", "context", "browser", "driver",
    )} <= diagnostic_messages
    assert "private" not in json.dumps(result)


def test_cancellation_during_page_creation_releases_resources_and_allows_next_job(tmp_path):
    service = make_service(tmp_path)
    cancelled = service.enqueue(make_manifest(tmp_path, key="cancelled"))
    next_job = service.enqueue(make_manifest(tmp_path, key="next"))
    harness = DriverHarness("page_stall")

    async def cancel_running_job():
        running = asyncio.create_task(service.run_once(
            adapters={"douyin": BrowserPreparation(_session_path(service), harness)},
            job_id=cancelled["job_id"],
        ))
        await asyncio.wait_for(harness.page_started.wait(), timeout=2)
        running.cancel()
        return await running

    result = asyncio.run(cancel_running_job())
    assert result is not None
    assert result["status"] == "CANCELLED"
    assert result["error"]["code"] == "CANCELLED"
    _assert_context_closed(harness)
    next_result = _run(service, next_job, BrowserPreparation(_session_path(service), DriverHarness()))
    assert next_result["status"] == "READY"


def _installed_chromium_executable() -> Path | None:
    candidates: list[Path] = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        root = os.environ.get(variable)
        if root:
            candidates.extend((
                Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ))
    for command in ("google-chrome", "chromium", "chromium-browser", "msedge"):
        executable = shutil.which(command)
        if executable:
            candidates.append(Path(executable))
    return next((candidate for candidate in candidates if candidate.is_file()), None)


class HtmlBrowserPreparation:
    def __init__(self, session_path: Path, fixture: Path, executable: Path, *, mode: str):
        self.session_path = session_path
        self.fixture = fixture
        self.executable = executable
        self.mode = mode
        self.session: BrowserSession | None = None
        self.cleanup_warnings: list[dict] = []
        self.local_text: str | None = None

    async def prepare(self, context):
        session = BrowserSession(context, launch_options={
            "headless": True, "executable_path": str(self.executable),
        })
        self.session = session
        page = await session.open(self.session_path)
        await context.run_stage("navigation", lambda: page.goto(self.fixture.as_uri()))
        self.local_text = await context.run_stage(
            "metadata", lambda: page.locator("#local-marker").inner_text(),
        )
        if self.mode == "deadline":
            await context.run_stage(
                "processing", lambda: page.wait_for_selector("#never-created", timeout=0),
            )
        if self.mode == "page_closed":
            await context.run_stage("navigation", page.close)
            await context.run_stage("navigation", lambda: page.goto(self.fixture.as_uri()))
        return {"title": context.manifest["title"], "body": context.manifest["body"]}

    async def close(self):
        session = self.session
        if session is not None:
            try:
                await session.close()
            finally:
                self.cleanup_warnings = session.cleanup_warnings


def test_real_patchright_local_page_deadline_and_closed_page_release_account(tmp_path):
    pytest.importorskip("patchright")
    executable = _installed_chromium_executable()
    if executable is None:
        pytest.skip("No local Chromium browser executable is available")
    fixture = Path(__file__).parent / "fixtures" / "browser_lifecycle.html"
    service = make_service(tmp_path)
    jobs = [service.enqueue(make_manifest(tmp_path, key=key)) for key in (
        "real-deadline", "real-closed", "real-next",
    )]
    adapters = [HtmlBrowserPreparation(_session_path(service), fixture, executable, mode=mode)
                for mode in ("deadline", "page_closed", "healthy")]
    limits = {"total": 30.0, "navigation": 10.0, "processing": 0.1, "cleanup": 5.0}
    deadline_result = _run(service, jobs[0], adapters[0], limits=limits)
    assert deadline_result["status"] == "FAILED_PRE_SUBMIT"
    assert deadline_result["error"]["code"] == "TIMEOUT"
    assert deadline_result["error"]["field"] == "processing"
    closed_result = _run(service, jobs[1], adapters[1], limits=limits)
    assert closed_result["status"] == "FAILED_PRE_SUBMIT"
    assert closed_result["error"]["code"] == "PAGE_CLOSED"
    next_result = _run(service, jobs[2], adapters[2], limits=limits)
    assert next_result["status"] == "READY"
    assert all(adapter.local_text == "local page ready" for adapter in adapters)
    for job in jobs:
        runtime = [event["details"] for event in service.events(job["job_id"])
                   if event["stage"] == "runtime"]
        assert len(runtime) == 1
        assert runtime[0]["driver_pin"] == "1.58.2"
        assert runtime[0]["browser_version"] != "unavailable"

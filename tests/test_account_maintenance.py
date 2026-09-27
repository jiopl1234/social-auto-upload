"""Offline account-lease checks for the supported legacy CLI entry points."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from importlib import import_module
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import sau_cli
from orchestrator.errors import ResourceCleanupError, TaskError
from orchestrator.ownership import AccountLease


PLATFORMS = (
    ("douyin", "douyin_setup", sau_cli.login_douyin_account, sau_cli.check_douyin_account),
    ("xiaohongshu", "xiaohongshu_setup", sau_cli.login_xiaohongshu_account, sau_cli.check_xiaohongshu_account),
    ("tencent", "tencent_setup", sau_cli.login_tencent_account, sau_cli.check_tencent_account),
)


@pytest.fixture
def installed_chromium(monkeypatch):
    """Keep stubbed Douyin logins independent of locally installed browsers."""
    from patchright import async_api

    driver = MagicMock()
    driver.chromium.executable_path = sys.executable
    driver_context = MagicMock()
    driver_context.__aenter__ = AsyncMock(return_value=driver)
    driver_context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(async_api, "async_playwright", lambda: driver_context)


@contextmanager
def external_process_lock(lock_path: Path):
    """Hold the real OS lock from a second Python process."""
    ready_file = lock_path.with_name(f"{lock_path.name}.{uuid4().hex}.ready")
    code = (
        "from pathlib import Path\n"
        "from orchestrator.locking import ProcessLock\n"
        "import sys\n"
        "with ProcessLock(Path(sys.argv[1])):\n"
        "    Path(sys.argv[2]).write_text('ready', encoding='utf-8')\n"
        "    sys.stdin.readline()\n"
    )
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root), env.get("PYTHONPATH"))))
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(lock_path), str(ready_file)],
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready_file.exists():
            if process.poll() is not None:
                assert process.stderr is not None
                pytest.fail(f"lock holder failed: {process.stderr.read()}")
            if time.monotonic() >= deadline:
                pytest.fail("lock holder did not become ready within 10 seconds")
            time.sleep(0.025)
        yield
    finally:
        if process.poll() is None:
            try:
                process.communicate(input="\n", timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


def _fake_setup_module(platform: str, setup_name: str, setup):
    module = ModuleType(f"uploader.{platform}_uploader.main")
    setattr(module, setup_name, setup)
    return module


def probe_external_lock(lock_path: Path) -> str:
    root = Path(__file__).resolve().parents[1]
    code = (
        "from pathlib import Path\n"
        "from orchestrator.errors import TaskError\n"
        "from orchestrator.locking import ProcessLock\n"
        "import sys\n"
        "try:\n"
        "    with ProcessLock(Path(sys.argv[1])):\n"
        "        print('acquired')\n"
        "except TaskError as exc:\n"
        "    print(exc.code)\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root), env.get("PYTHONPATH"))))
    result = subprocess.run(
        [sys.executable, "-c", code, str(lock_path)],
        cwd=root, env=env, capture_output=True, text=True, timeout=10, check=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize("platform,setup_name,login,check", PLATFORMS)
def test_public_maintenance_and_upload_entries_obey_external_process_lock(
    tmp_path, monkeypatch, platform, setup_name, login, check
):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")
    lease = AccountLease(runtime, platform, "creator")

    if platform == "douyin":
        video = lambda: sau_cli.upload_video(sau_cli.DouyinVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0))
        note = lambda: sau_cli.upload_note(sau_cli.DouyinNoteUploadRequest("creator", [Path("unused.png")], "title", "body", [], 0))
    elif platform == "xiaohongshu":
        video = lambda: sau_cli.upload_xiaohongshu_video(sau_cli.XiaohongshuVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0))
        note = lambda: sau_cli.upload_xiaohongshu_note(sau_cli.XiaohongshuNoteUploadRequest("creator", [Path("unused.png")], "title", "body", [], 0))
    else:
        video = lambda: sau_cli.upload_tencent_video(sau_cli.TencentVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0))
        note = None

    operations = (lambda: login("creator"), lambda: check("creator"))
    with external_process_lock(lease.lock.path):
        for operation in operations:
            with pytest.raises(TaskError) as caught:
                asyncio.run(operation())
            assert caught.value.code == "ACCOUNT_BUSY"
        with pytest.raises(TaskError) as caught:
            asyncio.run(video())
        assert caught.value.code == "UNSUPPORTED"
        if note is not None:
            with pytest.raises(TaskError) as caught:
                asyncio.run(note())
            assert caught.value.code == "UNSUPPORTED"


@pytest.mark.parametrize(
    "platform,setup_name,class_name,method_name,upload_request,upload",
    (
        ("douyin", "douyin_setup", "DouYinVideo", "douyin_upload_video", sau_cli.DouyinVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0), sau_cli.upload_video),
        ("xiaohongshu", "xiaohongshu_setup", "XiaoHongShuVideo", "main", sau_cli.XiaohongshuVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0), sau_cli.upload_xiaohongshu_video),
        ("tencent", "tencent_setup", "TencentVideo", "tencent_upload_video", sau_cli.TencentVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0), sau_cli.upload_tencent_video),
    ),
)
def test_legacy_video_rejects_before_check_and_allows_same_account_maintenance(
    tmp_path, monkeypatch, platform, setup_name, class_name, method_name, upload_request, upload
):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")
    lease = AccountLease(runtime, platform, "creator")
    phases = []

    async def setup(*_args, **_kwargs):
        phases.append("check")
        return True

    class FakeUploader:
        def __init__(self, *_args, **_kwargs):
            pass

    async def run_upload(self):
        phases.append("upload")
        from orchestrator.platforms.legacy import require_task_delivery

        require_task_delivery()

    setattr(FakeUploader, method_name, run_upload)
    module = _fake_setup_module(platform, setup_name, setup)
    setattr(module, class_name, FakeUploader)
    check_account = next(row[3] for row in PLATFORMS if row[0] == platform)
    with patch.dict(sys.modules, {f"uploader.{platform}_uploader.main": module}):
        with pytest.raises(TaskError) as caught:
            asyncio.run(upload(upload_request))
        assert caught.value.code == "UNSUPPORTED"
        assert phases == []
        assert not lease.path.exists()
        assert asyncio.run(check_account("creator")) is False

    with AccountLease(runtime, platform, "creator"):
        pass
    assert probe_external_lock(lease.lock.path) == "acquired"


def test_login_sessions_remain_separate_and_release_platform_lease(tmp_path, monkeypatch, installed_chromium):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")
    fake_modules = {}
    for platform, setup_name, _login, _check in PLATFORMS:
        async def setup(account_file, *, handle, return_detail, headless, _platform=platform):
            assert handle and return_detail
            Path(account_file).write_text(f"{_platform}:session", encoding="utf-8")
            return {"success": True, "account_file": account_file}

        fake_modules[f"uploader.{platform}_uploader.main"] = _fake_setup_module(platform, setup_name, setup)

    with patch.dict(sys.modules, fake_modules):
        for platform, _setup_name, login, _check in PLATFORMS:
            for account in ("creator", "second"):
                result = asyncio.run(login(account))
                cookie = tmp_path / "legacy" / "cookies" / f"{platform}_{account}.json"
                assert result["account_file"] == str(cookie)
                assert cookie.read_text(encoding="utf-8") == f"{platform}:session"
                with AccountLease(runtime, platform, "another alias"):
                    pass


def test_login_error_leaves_unconfirmed_resource_marker(tmp_path, monkeypatch, installed_chromium):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")

    async def fail_setup(*_args, **_kwargs):
        raise RuntimeError("controlled login failure")

    module = _fake_setup_module("douyin", "douyin_setup", fail_setup)
    with patch.dict(sys.modules, {"uploader.douyin_uploader.main": module}):
        with pytest.raises(RuntimeError, match="controlled login failure"):
            asyncio.run(sau_cli.login_douyin_account("creator"))
    marker = json.loads(AccountLease(runtime, "douyin", "creator").path.read_text(encoding="utf-8"))
    assert marker["active"] is True
    assert marker["resources_started"] is True
    with pytest.raises(TaskError) as caught:
        asyncio.run(sau_cli.login_douyin_account("another alias"))
    assert caught.value.code == "ACCOUNT_BUSY"


def test_unsupported_video_does_not_check_missing_cookie_or_acquire_lease(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")

    called = []

    async def invalid_cookie(*_args, **_kwargs):
        called.append("cookie-check")
        return False

    module = _fake_setup_module("douyin", "douyin_setup", invalid_cookie)
    setattr(module, "DouYinVideo", object)
    request = sau_cli.DouyinVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0)
    with patch.dict(sys.modules, {"uploader.douyin_uploader.main": module}):
        with pytest.raises(TaskError) as caught:
            asyncio.run(sau_cli.upload_video(request))
    assert caught.value.code == "UNSUPPORTED"
    assert called == []
    assert not AccountLease(runtime, "douyin", "creator").path.exists()
    with AccountLease(runtime, "douyin", "creator"):
        pass




@pytest.mark.parametrize("platform", ("xiaohongshu", "tencent"))
@pytest.mark.parametrize(
    "inner_close_fails,outer_close_fails",
    ((False, False), (True, False), (True, True), (False, True)),
)
def test_nested_login_cleanup_controls_public_lease(
    tmp_path, monkeypatch, platform, inner_close_fails, outer_close_fails
):
    """A nested cookie check must never turn an unknown browser close into a safe login result."""
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")
    module = import_module(f"uploader.{platform}_uploader.main")
    login = sau_cli.login_xiaohongshu_account if platform == "xiaohongshu" else sau_cli.login_tencent_account

    outer_page = MagicMock()
    outer_page.goto = AsyncMock()
    outer_page.url = "https://example.invalid/creator"
    outer_context = MagicMock()
    outer_context.new_page = AsyncMock(return_value=outer_page)

    async def save_state(*, path):
        Path(path).write_text("session", encoding="utf-8")

    outer_context.storage_state = AsyncMock(side_effect=save_state)
    outer_context.close = AsyncMock(
        side_effect=OSError("outer context close failed") if outer_close_fails else None
    )
    outer_browser = MagicMock()
    outer_browser.new_context = AsyncMock(return_value=outer_context)
    outer_browser.close = AsyncMock()

    inner_context = MagicMock()
    inner_context.new_page = AsyncMock(side_effect=RuntimeError("controlled cookie check failure"))
    inner_context.close = AsyncMock()
    inner_browser = MagicMock()
    inner_browser.new_context = AsyncMock(return_value=inner_context)
    inner_browser.close = AsyncMock(
        side_effect=OSError("nested browser close failed; cookie=raw-secret") if inner_close_fails else None
    )

    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(side_effect=(outer_browser, inner_browser))

    class PlaywrightContext:
        async def __aenter__(self):
            return playwright

        async def __aexit__(self, *_args):
            return False

    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "async_playwright", side_effect=PlaywrightContext))
        stack.enter_context(patch.object(module, "set_init_script", new=AsyncMock(side_effect=lambda context: context)))
        stack.enter_context(patch.object(module.asyncio, "sleep", new=AsyncMock()))
        if platform == "xiaohongshu":
            stack.enter_context(patch.object(module, "_save_xhs_qrcode", new=AsyncMock(return_value={"image_path": str(tmp_path / "qr.png")})))
            stack.enter_context(patch.object(module, "_is_xhs_login_completed", new=AsyncMock(return_value=True)))
            stack.enter_context(patch.object(module, "remove_qrcode_file", return_value=False))
        else:
            stack.enter_context(patch.object(module, "_save_tencent_qrcode", new=AsyncMock(return_value={"image_path": str(tmp_path / "qr.png")})))
            stack.enter_context(patch.object(module, "_wait_for_tencent_login", new=AsyncMock(return_value={"success": True, "message": "complete"})))
            stack.enter_context(patch.object(module, "_get_qrcode_utils", return_value={"remove_qrcode_file": lambda _path: False}))

        if inner_close_fails or outer_close_fails:
            with pytest.raises(TaskError) as caught:
                asyncio.run(login("creator"))
            assert caught.value.code == "RESOURCES_UNCONFIRMED"
            assert isinstance(caught.value, ResourceCleanupError)
            assert caught.value.stage == ("COOKIE_CHECK" if inner_close_fails else "LOGIN")
            assert "raw-secret" not in str(caught.value)
        else:
            result = asyncio.run(login("creator"))
            assert result["success"] is False
            assert result["status"] == "cookie_invalid"

    outer_context.close.assert_awaited_once()
    outer_browser.close.assert_awaited_once()
    inner_context.close.assert_awaited_once()
    inner_browser.close.assert_awaited_once()

    lease = AccountLease(runtime, platform, "creator")
    marker = json.loads(lease.path.read_text(encoding="utf-8"))
    if inner_close_fails or outer_close_fails:
        assert marker["active"] is True
        assert marker["resources_started"] is True
        with pytest.raises(TaskError) as blocked:
            asyncio.run(login("different account"))
        assert blocked.value.code == "ACCOUNT_BUSY"
        assert not (tmp_path / "legacy" / "cookies" / f"{platform}_different account.json").exists()
    else:
        assert marker["active"] is False
        assert marker["resources_started"] is False
        with AccountLease(runtime, platform, "another alias"):
            pass


@pytest.mark.parametrize("platform", ("xiaohongshu", "tencent"))
@pytest.mark.parametrize("operation", ("login", "check"))
def test_account_browser_closes_when_context_creation_fails(tmp_path, monkeypatch, platform, operation):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")
    module = import_module(f"uploader.{platform}_uploader.main")
    if operation == "check":
        sau_cli.resolve_account_file(platform, "creator").write_text("session", encoding="utf-8")
    account_operation = (
        sau_cli.login_xiaohongshu_account if platform == "xiaohongshu" else sau_cli.login_tencent_account
    ) if operation == "login" else (
        sau_cli.check_xiaohongshu_account if platform == "xiaohongshu" else sau_cli.check_tencent_account
    )

    browser = MagicMock()
    browser.new_context = AsyncMock(side_effect=RuntimeError("context unavailable"))
    browser.close = AsyncMock()
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)

    class PlaywrightContext:
        async def __aenter__(self):
            return playwright

        async def __aexit__(self, *_args):
            return False

    with patch.object(module, "async_playwright", side_effect=PlaywrightContext):
        result = asyncio.run(account_operation("creator"))

    if operation == "login":
        assert isinstance(result, dict)
        assert result["success"] is False
    else:
        assert result is False
    browser.close.assert_awaited_once()
    marker = json.loads(AccountLease(runtime, platform, "creator").path.read_text(encoding="utf-8"))
    assert marker["active"] is False
    assert marker["resources_started"] is False


@pytest.mark.parametrize("platform", ("xiaohongshu", "tencent"))
def test_cookie_check_reports_context_close_failure_and_closes_browser(tmp_path, monkeypatch, platform):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")
    account_file = sau_cli.resolve_account_file(platform, "creator")
    account_file.write_text("session", encoding="utf-8")
    module = import_module(f"uploader.{platform}_uploader.main")
    check = sau_cli.check_xiaohongshu_account if platform == "xiaohongshu" else sau_cli.check_tencent_account

    page = MagicMock()
    page.url = "https://example.invalid/creator"
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.wait_for_url = AsyncMock(side_effect=TimeoutError())
    page.frames = []
    page.locator.return_value.first.count = AsyncMock(return_value=0)
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock(side_effect=OSError("context close failed"))
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)

    class PlaywrightContext:
        async def __aenter__(self):
            return playwright

        async def __aexit__(self, *_args):
            return False

    with (
        patch.object(module, "async_playwright", side_effect=PlaywrightContext),
        patch.object(module, "set_init_script", new=AsyncMock(return_value=context)),
        pytest.raises(ResourceCleanupError) as caught,
    ):
        asyncio.run(check("creator"))

    assert caught.value.stage == "COOKIE_CHECK"
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
    marker = json.loads(AccountLease(runtime, platform, "creator").path.read_text(encoding="utf-8"))
    assert marker["active"] is True
    assert marker["resources_started"] is True


@pytest.mark.parametrize("platform", ("xiaohongshu", "tencent"))
def test_cookie_check_preserves_cancellation_while_attempting_all_closes(tmp_path, monkeypatch, platform):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")
    sau_cli.resolve_account_file(platform, "creator").write_text("session", encoding="utf-8")
    module = import_module(f"uploader.{platform}_uploader.main")
    check = sau_cli.check_xiaohongshu_account if platform == "xiaohongshu" else sau_cli.check_tencent_account

    page = MagicMock()
    page.goto = AsyncMock(side_effect=asyncio.CancelledError())
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock(side_effect=OSError("context close failed"))
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock(side_effect=OSError("browser close failed"))
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)

    class PlaywrightContext:
        async def __aenter__(self):
            return playwright

        async def __aexit__(self, *_args):
            return False

    with (
        patch.object(module, "async_playwright", side_effect=PlaywrightContext),
        patch.object(module, "set_init_script", new=AsyncMock(return_value=context)),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(check("creator"))

    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
    marker = json.loads(AccountLease(runtime, platform, "creator").path.read_text(encoding="utf-8"))
    assert marker["active"] is True
    assert marker["resources_started"] is True

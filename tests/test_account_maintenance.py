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
        uploads = (
            lambda: sau_cli.upload_video(sau_cli.DouyinVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0)),
            lambda: sau_cli.upload_note(sau_cli.DouyinNoteUploadRequest("creator", [Path("unused.png")], "title", "body", [], 0)),
        )
    elif platform == "xiaohongshu":
        uploads = (
            lambda: sau_cli.upload_xiaohongshu_video(sau_cli.XiaohongshuVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0)),
            lambda: sau_cli.upload_xiaohongshu_note(sau_cli.XiaohongshuNoteUploadRequest("creator", [Path("unused.png")], "title", "body", [], 0)),
        )
    else:
        uploads = (
            lambda: sau_cli.upload_tencent_video(sau_cli.TencentVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0)),
        )

    operations = (lambda: login("creator"), lambda: check("creator"), *uploads)
    with external_process_lock(lease.lock.path):
        for operation in operations:
            with pytest.raises(TaskError) as caught:
                asyncio.run(operation())
            assert caught.value.code == "ACCOUNT_BUSY"


@pytest.mark.parametrize(
    "platform,setup_name,class_name,method_name,upload_request,upload",
    (
        ("douyin", "douyin_setup", "DouYinVideo", "douyin_upload_video", sau_cli.DouyinVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0), sau_cli.upload_video),
        ("xiaohongshu", "xiaohongshu_setup", "XiaoHongShuVideo", "main", sau_cli.XiaohongshuVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0), sau_cli.upload_xiaohongshu_video),
        ("tencent", "tencent_setup", "TencentVideo", "tencent_upload_video", sau_cli.TencentVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0), sau_cli.upload_tencent_video),
    ),
)
def test_legacy_upload_holds_process_lock_through_check_and_upload(
    tmp_path, monkeypatch, platform, setup_name, class_name, method_name, upload_request, upload
):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")
    lock_path = AccountLease(runtime, platform, "creator").lock.path
    phases = []

    async def setup(*_args, **_kwargs):
        phases.append("check")
        assert probe_external_lock(lock_path) == "ACCOUNT_BUSY"
        return True

    class FakeUploader:
        def __init__(self, *_args, **_kwargs):
            pass

    async def run_upload(self):
        phases.append("upload")
        assert probe_external_lock(lock_path) == "ACCOUNT_BUSY"

    setattr(FakeUploader, method_name, run_upload)
    module = _fake_setup_module(platform, setup_name, setup)
    setattr(module, class_name, FakeUploader)
    with patch.dict(sys.modules, {f"uploader.{platform}_uploader.main": module}):
        asyncio.run(upload(upload_request))

    assert phases == ["check", "upload"]
    assert probe_external_lock(lock_path) == "acquired"


def test_login_sessions_remain_separate_and_release_platform_lease(tmp_path, monkeypatch):
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


def test_login_error_leaves_unconfirmed_resource_marker(tmp_path, monkeypatch):
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


def test_invalid_upload_cookie_releases_lease_after_completed_check(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    (tmp_path / "legacy").mkdir()
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path / "legacy")

    async def invalid_cookie(*_args, **_kwargs):
        return False

    module = _fake_setup_module("douyin", "douyin_setup", invalid_cookie)
    setattr(module, "DouYinVideo", object)
    request = sau_cli.DouyinVideoUploadRequest("creator", Path("unused.mp4"), "title", "body", [], 0)
    with patch.dict(sys.modules, {"uploader.douyin_uploader.main": module}):
        with pytest.raises(RuntimeError, match="cookie is missing or expired"):
            asyncio.run(sau_cli.upload_video(request))
    with AccountLease(runtime, "douyin", "creator"):
        pass


def test_douyin_video_close_failure_is_reported_after_both_closes(tmp_path):
    from uploader.douyin_uploader.main import DouYinVideo

    video = DouYinVideo("title", str(tmp_path / "unused.mp4"), [], 0, str(tmp_path / "cookie.json"))
    video.validate_upload_args = AsyncMock()
    video.fill_title_and_description = AsyncMock()
    video.apply_self_declaration = AsyncMock()
    video.apply_collection = AsyncMock()
    video.set_thumbnail = AsyncMock()

    locator = MagicMock()
    locator.first = locator
    locator.count = AsyncMock(return_value=1)
    locator.wait_for = AsyncMock()
    locator.set_input_files = AsyncMock()
    absent = MagicMock()
    absent.first = absent
    absent.count = AsyncMock(return_value=0)
    button = MagicMock()
    button.count = AsyncMock(return_value=1)
    button.click = AsyncMock()
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_url = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock()
    page.get_by_role.return_value = button
    page.locator.side_effect = (
        lambda selector: absent
        if "semi-switch" in selector or "input[placeholder" in selector else locator
    )

    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.storage_state = AsyncMock()
    context.close = AsyncMock(side_effect=OSError("close failed"))
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)

    with (
        patch("uploader.douyin_uploader.main.set_init_script", AsyncMock(return_value=context)),
        patch("uploader.douyin_uploader.main.asyncio.sleep", AsyncMock()),
        pytest.raises(RuntimeError, match="browser resource shutdown"),
    ):
        asyncio.run(video.upload(playwright))
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


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

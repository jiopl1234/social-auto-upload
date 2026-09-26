"""Legacy note entry points stop before account or browser side effects."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sau_cli
from orchestrator.errors import TaskError
from orchestrator.ownership import AccountLease
from uploader.douyin_uploader.main import DouYinNote
from uploader.xiaohongshu_uploader.main import XiaoHongShuNote


NOTE_CASES = (
    ("douyin", "douyin_setup", "DouYinNote", "douyin_upload_note",
     sau_cli.DouyinNoteUploadRequest("creator", [Path("unused.png")], "title", "body", [], 0),
     sau_cli.upload_note, sau_cli.check_douyin_account),
    ("xiaohongshu", "xiaohongshu_setup", "XiaoHongShuNote", "main",
     sau_cli.XiaohongshuNoteUploadRequest("creator", [Path("unused.png")], "title", "body", [], 0),
     sau_cli.upload_xiaohongshu_note, sau_cli.check_xiaohongshu_account),
)


@pytest.mark.parametrize("platform,setup_name,class_name,method_name,upload_request,upload,check", NOTE_CASES)
def test_public_note_rejects_before_lease_and_same_account_maintenance_still_works(
    tmp_path, monkeypatch, platform, setup_name, class_name, method_name, upload_request, upload, check,
):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(sau_cli, "resolve_runtime_home", lambda: tmp_path)
    lease = AccountLease(runtime, platform, "creator")
    phases: list[str] = []

    async def setup(*_args, **_kwargs):
        phases.append("cookie-check")
        return True

    class FakeUploader:
        def __init__(self, *_args, **_kwargs):
            phases.append("construct-uploader")

    async def submit(self):
        phases.append("upload")
        from orchestrator.platforms.legacy import require_task_delivery

        require_task_delivery()

    setattr(FakeUploader, method_name, submit)
    module = ModuleType(f"uploader.{platform}_uploader.main")
    setattr(module, setup_name, setup)
    setattr(module, class_name, FakeUploader)
    with patch.dict(sys.modules, {module.__name__: module}):
        with pytest.raises(TaskError) as caught:
            asyncio.run(upload(upload_request))
        assert caught.value.code == "UNSUPPORTED"
        assert phases == []
        assert not lease.path.exists()
        assert asyncio.run(check("creator")) is False

    with AccountLease(runtime, platform, "creator"):
        pass


@pytest.mark.parametrize("uploader_class", (DouYinNote, XiaoHongShuNote))
def test_legacy_note_content_and_upload_methods_reject_before_page_or_driver(uploader_class):
    uploader = uploader_class(["unused.png"], "body", [], 0, "unused.json", title="title")
    with pytest.raises(TaskError) as caught:
        asyncio.run(uploader.upload_note_content(object()))
    assert caught.value.code == "UNSUPPORTED"

    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(side_effect=AssertionError("driver started"))
    with patch.object(uploader, "validate_upload_args", new=AsyncMock()) as validate:
        with pytest.raises(TaskError) as caught:
            asyncio.run(uploader.upload(playwright))
    assert caught.value.code == "UNSUPPORTED"
    validate.assert_not_awaited()
    playwright.chromium.launch.assert_not_awaited()


@pytest.mark.parametrize("module_path,uploader_class,wrapper", (
    ("uploader.douyin_uploader.main", DouYinNote, "douyin_upload_note"),
    ("uploader.xiaohongshu_uploader.main", XiaoHongShuNote, "xiaohongshu_upload_note"),
    ("uploader.xiaohongshu_uploader.main", XiaoHongShuNote, "main"),
))
def test_legacy_note_wrapper_rejects_before_playwright(module_path, uploader_class, wrapper):
    uploader = uploader_class(["unused.png"], "body", [], 0, "unused.json", title="title")
    with patch(f"{module_path}.async_playwright", side_effect=AssertionError("driver started")) as start:
        with pytest.raises(TaskError) as caught:
            asyncio.run(getattr(uploader, wrapper)())
    assert caught.value.code == "UNSUPPORTED"
    start.assert_not_called()


def test_note_positional_options_keep_their_meaning():
    request = sau_cli.DouyinNoteUploadRequest(
        "creator", [Path("unused.png")], "title", "body", [], 0, "scheduled", False, False, "music",
    )
    assert (request.publish_strategy, request.debug, request.headless, request.bgm) == (
        "scheduled", False, False, "music",
    )
    douyin = DouYinNote(["unused.png"], "body", [], 0, "unused.json",
                        "title", "scheduled", False, False, "music")
    assert (douyin.publish_strategy, douyin.debug, douyin.headless, douyin.bgm) == (
        "scheduled", False, False, "music",
    )
    xiaohongshu = XiaoHongShuNote(["unused.png"], "body", [], 0, "unused.json",
                                  "title", "desc", "scheduled", False, False)
    assert (xiaohongshu.title, xiaohongshu.desc, xiaohongshu.publish_strategy,
            xiaohongshu.debug, xiaohongshu.headless) == (
        "title", "desc", "scheduled", False, False,
    )

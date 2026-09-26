import asyncio
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import sau_cli
from orchestrator.errors import TaskError
from uploader.douyin_uploader.main import DouYinVideo
from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo
from uploader.tencent_uploader.main import TencentVideo


class BrowserCliParserTests(unittest.TestCase):
    def test_build_parser_accepts_xiaohongshu_login(self):
        parser = sau_cli.build_parser()
        args = parser.parse_args(["xiaohongshu", "login", "--account", "creator"])
        self.assertEqual(args.platform, "xiaohongshu")
        self.assertEqual(args.action, "login")

    def test_douyin_upload_video_accepts_desc(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            video_path.write_bytes(b"video")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "douyin",
                    "upload-video",
                    "--account",
                    "creator",
                    "--file",
                    str(video_path),
                    "--title",
                    "标题",
                    "--desc",
                    "视频简介",
                ]
            )

        self.assertEqual(args.desc, "视频简介")

    def test_douyin_upload_video_accepts_dual_thumbnail_aspects(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            landscape_path = Path(tmp_dir) / "landscape.png"
            portrait_path = Path(tmp_dir) / "portrait.png"
            video_path.write_bytes(b"video")
            landscape_path.write_bytes(b"image")
            portrait_path.write_bytes(b"image")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "douyin",
                    "upload-video",
                    "--account",
                    "creator",
                    "--file",
                    str(video_path),
                    "--title",
                    "标题",
                    "--thumbnail-landscape",
                    str(landscape_path),
                    "--thumbnail-portrait",
                    str(portrait_path),
                ]
            )

        self.assertEqual(args.thumbnail_landscape, landscape_path)
        self.assertEqual(args.thumbnail_portrait, portrait_path)

    def test_douyin_upload_video_accepts_explicit_declaration(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            video_path.write_bytes(b"video")
            parser = sau_cli.build_parser()
            args = parser.parse_args([
                "douyin", "upload-video", "--account", "creator",
                "--file", str(video_path), "--title", "标题",
                "--declaration", "已确认声明原文",
            ])
        self.assertEqual(args.declaration, "已确认声明原文")

    def test_douyin_upload_video_has_no_implicit_declaration(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            video_path.write_bytes(b"video")
            args = sau_cli.build_parser().parse_args([
                "douyin", "upload-video", "--account", "creator",
                "--file", str(video_path), "--title", "标题",
            ])
        self.assertIsNone(args.declaration)

    def test_douyin_request_legacy_positional_runtime_flags_keep_their_meaning(self):
        request = sau_cli.DouyinVideoUploadRequest(
            "creator", Path("demo.mp4"), "标题", "简介", [], 0,
            None, None, None, "", "", "scheduled", False, False,
        )
        self.assertEqual(request.publish_strategy, "scheduled")
        self.assertFalse(request.debug)
        self.assertFalse(request.headless)
        self.assertIsNone(request.declaration)

    def test_tencent_upload_video_accepts_dual_thumbnail_aspects(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            landscape_path = Path(tmp_dir) / "landscape.png"
            portrait_path = Path(tmp_dir) / "portrait.png"
            video_path.write_bytes(b"video")
            landscape_path.write_bytes(b"image")
            portrait_path.write_bytes(b"image")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "tencent",
                    "upload-video",
                    "--account",
                    "creator",
                    "--file",
                    str(video_path),
                    "--title",
                    "标题",
                    "--thumbnail-landscape",
                    str(landscape_path),
                    "--thumbnail-portrait",
                    str(portrait_path),
                ]
            )

        self.assertEqual(args.thumbnail_landscape, landscape_path)
        self.assertEqual(args.thumbnail_portrait, portrait_path)

    def test_kuaishou_upload_note_accepts_title_and_note(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "1.png"
            image_path.write_bytes(b"image")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "kuaishou",
                    "upload-note",
                    "--account",
                    "creator",
                    "--images",
                    str(image_path),
                    "--title",
                    "图文标题",
                    "--note",
                    "图文正文",
                ]
            )

        self.assertEqual(args.title, "图文标题")
        self.assertEqual(args.note, "图文正文")

    def test_xiaohongshu_upload_video_defaults_to_headless(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            video_path.write_bytes(b"video")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "xiaohongshu",
                    "upload-video",
                    "--account",
                    "creator",
                    "--file",
                    str(video_path),
                    "--title",
                    "视频标题",
                ]
            )

        self.assertTrue(args.headless)

    def test_xiaohongshu_upload_note_accepts_headed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "1.png"
            image_path.write_bytes(b"image")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "xiaohongshu",
                    "upload-note",
                    "--account",
                    "creator",
                    "--images",
                    str(image_path),
                    "--title",
                    "图文标题",
                    "--note",
                    "图文正文",
                    "--headed",
                ]
            )

        self.assertFalse(args.headless)


class BrowserCliDispatchTests(unittest.TestCase):
    def test_tencent_video_request_keeps_collection_but_rejects_legacy_upload(self):
        request = sau_cli.TencentVideoUploadRequest(
            account_name="creator",
            video_file=Path("demo.mp4"),
            title="视频标题",
            description="视频简介",
            tags=["测试"],
            publish_date=0,
            collection_name="我的合集",
        )

        self.assertEqual(request.collection_name, "我的合集")
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch("sau_cli.default_runtime_dir", return_value=Path(tmp_dir) / "runtime"),
                patch("sau_cli.resolve_runtime_home", return_value=Path(tmp_dir)),
                patch("uploader.tencent_uploader.main.tencent_setup", new=AsyncMock(return_value=True)) as setup,
                patch.object(TencentVideo, "tencent_upload_video", new=AsyncMock()) as mock_upload,
            ):
                with self.assertRaises(TaskError) as caught:
                    asyncio.run(sau_cli.upload_tencent_video(request))

        self.assertEqual(caught.exception.code, "UNSUPPORTED")
        setup.assert_not_awaited()
        mock_upload.assert_not_awaited()

    def test_dispatch_xiaohongshu_check_prints_valid(self):
        args = Namespace(platform="xiaohongshu", action="check", account="creator")
        with patch("sau_cli.check_xiaohongshu_account", new=AsyncMock(return_value=True)):
            code = asyncio.run(sau_cli.dispatch(args))
        self.assertEqual(code, 0)

    def test_dispatch_douyin_upload_note_uses_new_request_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "1.png"
            image.write_bytes(b"image")
            args = sau_cli.build_parser().parse_args([
                "douyin", "upload-note", "--account", "creator", "--images", str(image),
                "--title", "图文标题", "--note", "图文正文", "--tags", "测试,图文",
            ])
            with patch("sau_cli.upload_note", new=AsyncMock()) as mock_upload:
                code = asyncio.run(sau_cli.dispatch(args))
        request = mock_upload.await_args.args[0]
        self.assertEqual(code, 0)
        self.assertEqual(request.title, "图文标题")
        self.assertEqual(request.note, "图文正文")
        self.assertEqual(request.bgm, "")

    def test_dispatch_douyin_upload_video_uses_dual_thumbnail_request_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            video, landscape, portrait = [Path(directory) / name for name in ("demo.mp4", "landscape.png", "portrait.png")]
            for media in (video, landscape, portrait):
                media.write_bytes(b"media")
            args = sau_cli.build_parser().parse_args([
                "douyin", "upload-video", "--account", "creator", "--file", str(video),
                "--title", "视频标题", "--desc", "视频简介", "--tags", "测试,视频",
                "--thumbnail-landscape", str(landscape), "--thumbnail-portrait", str(portrait),
                "--declaration", "已确认声明原文", "--collection", "系列 A",
            ])
            with patch("sau_cli.upload_video", new=AsyncMock()) as mock_upload:
                code = asyncio.run(sau_cli.dispatch(args))
        request = mock_upload.await_args.args[0]
        self.assertEqual(code, 0)
        self.assertEqual(request.thumbnail_landscape_file, landscape)
        self.assertEqual(request.thumbnail_portrait_file, portrait)
        self.assertEqual(request.declaration, "已确认声明原文")
        self.assertEqual(request.collection_name, "系列 A")

    def test_dispatch_tencent_upload_video_uses_dual_thumbnail_request_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            video, landscape, portrait = [Path(directory) / name for name in ("demo.mp4", "landscape.png", "portrait.png")]
            for media in (video, landscape, portrait):
                media.write_bytes(b"media")
            args = sau_cli.build_parser().parse_args([
                "tencent", "upload-video", "--account", "creator", "--file", str(video),
                "--title", "视频标题", "--desc", "视频简介", "--tags", "测试,视频",
                "--thumbnail-landscape", str(landscape), "--thumbnail-portrait", str(portrait),
                "--collection", "系列 B",
            ])
            with patch("sau_cli.upload_tencent_video", new=AsyncMock()) as mock_upload:
                code = asyncio.run(sau_cli.dispatch(args))
        request = mock_upload.await_args.args[0]
        self.assertEqual(code, 0)
        self.assertEqual(request.thumbnail_landscape_file, landscape)
        self.assertEqual(request.thumbnail_portrait_file, portrait)
        self.assertEqual(request.collection_name, "系列 B")

    def test_dispatch_xiaohongshu_upload_video_uses_headed_request(self):
        args = Namespace(
            platform="xiaohongshu",
            action="upload-video",
            account="creator",
            file=Path("demo.mp4"),
            title="视频标题",
            desc="视频简介",
            tags="测试,视频",
            schedule=0,
            thumbnail=None,
            debug=False,
            headless=False,
        )
        with patch("sau_cli.upload_xiaohongshu_video", new=AsyncMock()) as mock_upload:
            asyncio.run(sau_cli.dispatch(args))

        request = mock_upload.await_args.args[0]
        self.assertEqual(request.title, "视频标题")
        self.assertEqual(request.description, "视频简介")
        self.assertFalse(request.headless)

    def test_dispatch_xiaohongshu_upload_note_uses_headless_request(self):
        args = Namespace(
            platform="xiaohongshu",
            action="upload-note",
            account="creator",
            images=[Path("1.png"), Path("2.png")],
            title="图文标题",
            note="图文正文",
            tags="测试,图文",
            schedule=0,
            debug=False,
            headless=True,
        )
        with patch("sau_cli.upload_xiaohongshu_note", new=AsyncMock()) as mock_upload:
            asyncio.run(sau_cli.dispatch(args))

        request = mock_upload.await_args.args[0]
        self.assertEqual(request.title, "图文标题")
        self.assertEqual(request.note, "图文正文")
        self.assertTrue(request.headless)
        self.assertEqual(len(request.image_files), 2)


@pytest.mark.parametrize("module_path,uploader_class,method_name", [
    ("uploader.douyin_uploader.main", DouYinVideo, "douyin_upload_video"),
    ("uploader.xiaohongshu_uploader.main", XiaoHongShuVideo, "xiaohongshu_upload_video"),
    ("uploader.tencent_uploader.main", TencentVideo, "tencent_upload_video"),
])
def test_video_wrapper_rejects_before_starting_driver(module_path, uploader_class, method_name):
    uploader = uploader_class("title", "unused.mp4", [], 0, "unused.json")
    with patch(f"{module_path}.async_playwright", side_effect=AssertionError("driver started")) as start:
        with pytest.raises(TaskError) as caught:
            asyncio.run(getattr(uploader, method_name)())
    assert caught.value.code == "UNSUPPORTED"
    start.assert_not_called()


if __name__ == "__main__":
    unittest.main()

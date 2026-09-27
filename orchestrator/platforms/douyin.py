"""Douyin creator video evidence observed on 2026-09-27.

Only browser-generated responses are observed. No signed API calls are forged,
and a work-list response never claims to contain the source file's SHA256.
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from orchestrator.errors import TaskError
from orchestrator.platforms.dom import DomPagePort, EditorSelectors


IDENTITY_PATH = "/web/api/media/user/info/"
WORKS_PATH = "/janus/douyin/creator/pc/work_list"
SUBMIT_PATH = "/web/api/media/aweme/create_v2/"


class DouyinVideoPort(DomPagePort):
    origin = "https://creator.douyin.com"
    upload_origin = "https://vod.bytedanceapi.com"
    verification_scope = "douyin_video_v1"
    capability_version = "1.1.0"

    def __init__(self):
        scope = type(self).verification_scope
        super().__init__("douyin", EditorSelectors(
            upload='input[type="file"][accept*="video"]',
            title='input[placeholder^="填写作品标题"]',
            body='div.zone-container[contenteditable="true"]', submit='button:has-text("发布")',
        ), edit_url=self.origin + "/creator-micro/content/upload",
            works_url=self.origin + "/creator-micro/content/manage")
        self.verification_scope = scope
        self.pending_responses: deque[Any] = deque(maxlen=32)
        self.observing = False
        self.uploading = False
        self.commit: dict | None = None
        self.asset: Mapping[str, Any] | None = None
        self.media_ready = False
        self.cover_ready = False
        self.in_cover_editor = False
        self.observed_receipt: dict | None = None
        self.receipt_failed = False
        self.works: list[dict] | None = None
        self.identity: str | None = None
        self.identity_cookie_digest: str | None = None
        self.page: Any = None

    def _observe(self, response: Any) -> None:
        url = urlsplit(response.url)
        origin = f"{url.scheme}://{url.netloc}"
        action = parse_qs(url.query).get("Action")
        if ((origin == self.origin and url.path in {IDENTITY_PATH, WORKS_PATH, SUBMIT_PATH})
                or (self.uploading and origin == self.upload_origin and action == ["CommitUploadInner"])):
            self.pending_responses.append(response)

    async def _drain(self) -> None:
        while self.pending_responses:
            response = self.pending_responses.popleft()
            try:
                payload = await response.json()
            except Exception:
                continue
            if not isinstance(payload, dict) or response.status != 200:
                continue
            url = urlsplit(response.url)
            if url.path == IDENTITY_PATH:
                uid = payload.get("user", {}).get("uid")
                if payload.get("status_code") != 0 or not isinstance(uid, str) or not uid:
                    raise TaskError("AUTH_REQUIRED", "Account authentication is required")
                self.identity = uid
                self.identity_cookie_digest = await self._cookie_digest()
            elif url.path == WORKS_PATH:
                if payload.get("status_code") == 0:
                    self.works = self.work_observations(payload)
            elif url.path == SUBMIT_PATH and self.submitted and response.request.method == "POST":
                content_id = payload.get("item_id")
                if payload.get("status_code") == 0 and isinstance(content_id, str) and content_id.isdigit():
                    self.observed_receipt = {"content_id": content_id}
                else:
                    self.receipt_failed = True
            elif self.uploading and parse_qs(url.query).get("Action") == ["CommitUploadInner"]:
                results = payload.get("Result", {}).get("Results", [])
                if isinstance(results, list) and len(results) == 1 and isinstance(results[0], dict):
                    self.commit = results[0]

    async def navigate(self, page: Any, content_type: str, *, readonly: bool) -> None:
        if content_type != "video":
            raise TaskError("UNSUPPORTED", "Requested capability is unsupported", field="content_type")
        if not self.observing:
            self.page = page
            page.on("response", self._observe)
            self.observing = True
        self.works = None
        await page.goto(self.works_url if readonly else self.edit_url, wait_until="domcontentloaded", timeout=0)

    async def _cookie_digest(self) -> str:
        cookies = await self.page.context.cookies(self.origin)
        identity = sorted((item["name"], item["domain"], item["value"]) for item in cookies
                          if item["name"] in {"sessionid", "sessionid_ss", "uid_tt", "uid_tt_ss"})
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    async def identify_account(self, page: Any) -> str:
        # Observe the site's own authenticated response. Recheck the session
        # cookie identity at each guard; a session change needs a new verified
        # observation, never a guessed account or replay of a signed URL.
        while self.identity is None:
            await self.check_challenge(page)
            await asyncio.sleep(0.1)
        await self._drain()
        if await self._cookie_digest() != self.identity_cookie_digest:
            raise TaskError("AUTH_REQUIRED", "Account authentication is required")
        return self.identity

    async def check_challenge(self, page: Any) -> None:
        if page.is_closed():
            raise TaskError("PAGE_CLOSED", "Browser page was closed")
        await self._drain()
        challenge = page.locator('article.uc-ui-verify_qr-verify:visible, input[placeholder*="验证码"]:visible, '
                                 '[class*="captcha_verify"]:visible')
        if await challenge.count():
            raise TaskError("AUTH_REQUIRED", "Account verification or login is required")
        for text in ("实名验证", "短信验证", "扫码登录", "安全验证", "接收短信验证码", "为保障账号安全"):
            if await page.get_by_text(text, exact=False).first.is_visible():
                raise TaskError("AUTH_REQUIRED", "Account verification or login is required")
        if not self.in_cover_editor and await page.locator('[role="dialog"]:visible').count():
            raise TaskError("UI_CHANGED", "An unrecognized dialog blocks editing")

    @staticmethod
    def validate_scope(manifest: Mapping[str, Any]) -> None:
        if manifest["content_type"] != "video":
            raise TaskError("UNSUPPORTED", "Requested capability is unsupported", field="content_type")
        if manifest["facts"] != {"ownership": "original", "ai_usage": "none", "repost_source": None}:
            raise TaskError("UNSUPPORTED", "Requested capability is unsupported", field="facts")
        for field in ("tags", "cover", "collection", "declarations"):
            if manifest.get(field):
                raise TaskError("UNSUPPORTED", "Requested capability is unsupported", field=field)
        for field, maximum in (("title", 30), ("body", 1000)):
            if len(manifest[field].encode("utf-16-le")) // 2 > maximum:
                raise TaskError("INVALID_INPUT", "Content exceeds the observed page limit", field=field)

    async def upload(self, page: Any, manifest: Mapping[str, Any]) -> None:
        self.validate_scope(manifest)
        while not await page.locator(self.selectors.upload).count():
            await self.check_challenge(page)
            await asyncio.sleep(0.1)
        asset = manifest["media"][0]
        self.asset = asset
        self.uploading, self.commit, self.media_ready = True, None, False
        control = await self._control(page, self.selectors.upload, "media", visible=False)
        await control.set_input_files(str(asset["path"]), timeout=0)

    async def wait_media(self, page: Any, manifest: Mapping[str, Any]) -> None:
        while True:
            await self.check_challenge(page)
            if self.commit is not None:
                meta, vid = self.commit.get("VideoMeta"), self.commit.get("Vid")
                if (not isinstance(meta, dict) or meta.get("FileType") != "video"
                        or meta.get("Size") != manifest["media"][0]["version"]["size_bytes"]
                        or not isinstance(vid, str) or not vid or not isinstance(meta.get("Uri"), str)):
                    raise TaskError("UPLOAD_FAILED", "One or more media files failed processing", field="media")
                videos = await page.locator("video").evaluate_all("""els => els.map(v => ({
                    ready:v.readyState, width:v.videoWidth, height:v.videoHeight,
                    duration:v.duration, src:v.currentSrc || v.src}))""")
                if len(videos) == 1:
                    video = videos[0]
                    if (video["ready"] >= 2 and video["width"] == meta.get("Width")
                            and video["height"] == meta.get("Height") and video["duration"] > 0
                            and urlsplit(video["src"]).path.endswith("/" + meta["Uri"])):
                        self.media_ready = True
                        return
            await asyncio.sleep(0.1)

    @property
    def media_observation(self) -> dict | None:
        if self.media_ready and self.commit is not None and self.asset is not None:
            return {"video_id": self.commit["Vid"], "sha256": self.asset["sha256"]}
        return None

    async def _select_covers(self, page: Any) -> None:
        await self.check_challenge(page)
        await page.get_by_text("选择封面", exact=True).first.click(timeout=5000)
        self.in_cover_editor = True
        try:
            dialog = page.locator('[role="dialog"]:visible')
            await dialog.wait_for(state="visible", timeout=5000)
            if not await dialog.get_by_text("设置竖封面", exact=True).count():
                raise TaskError("UI_CHANGED", "Required page control is missing or ambiguous", field="cover")
            await dialog.locator('[class^="preview-frame-"]').first.click(timeout=5000)
            await dialog.get_by_role("button", name="设置横封面", exact=True).click(timeout=5000)
            await self.check_challenge(page)
            await dialog.locator('[class^="preview-frame-"]').first.click(timeout=5000)
            await dialog.get_by_role("button", name="完成", exact=True).click(timeout=5000)
            await dialog.wait_for(state="hidden", timeout=5000)
            self.cover_ready = True
        finally:
            self.in_cover_editor = False

    async def _settings(self, page: Any) -> None:
        for name in ("公开", "立即发布"):
            label = page.locator("label").filter(has_text=name)
            if await label.count() != 1 or not await label.locator("input").is_checked():
                raise TaskError("METADATA_MISMATCH", "Prepared work does not match the queued content", field="declarations")
        for name in ("请选择合集", "请选择自主声明"):
            if not await page.get_by_text(name, exact=True).is_visible():
                raise TaskError("UI_CHANGED", "Required page control is missing or ambiguous", field="declarations")
        if await page.locator('input[role="switch"]:checked').count():
            raise TaskError("UNSUPPORTED", "Requested capability is unsupported", field="declarations")

    async def apply_fields(self, page: Any, manifest: Mapping[str, Any]) -> None:
        await self._fill(page, self.selectors.title, "title", manifest["title"])
        await self._fill(page, self.selectors.body, "body", manifest["body"])
        await self._select_covers(page)
        await self._settings(page)

    async def readback(self, page: Any) -> dict:
        await self.check_challenge(page)
        await self._settings(page)
        if not self.media_ready or not self.cover_ready or self.asset is None:
            raise TaskError("UPLOAD_FAILED", "Media is no longer ready", field="media")
        videos = await page.locator("video").evaluate_all("""els => els.map(v => ({
            ready:v.readyState, width:v.videoWidth, height:v.videoHeight, src:v.currentSrc || v.src}))""")
        meta = self.commit["VideoMeta"] if self.commit is not None else {}
        if (len(videos) != 1 or videos[0]["ready"] < 2 or videos[0]["width"] != meta.get("Width")
                or videos[0]["height"] != meta.get("Height")
                or not urlsplit(videos[0]["src"]).path.endswith("/" + meta.get("Uri", ""))):
            raise TaskError("UPLOAD_FAILED", "Media is no longer ready", field="media")
        title = await self._control(page, self.selectors.title, "title")
        body = await self._control(page, self.selectors.body, "body")
        return {"title": await title.input_value(), "body": await body.inner_text(), "tags": [],
                "media": [{"kind": "video", "sha256": self.asset["sha256"]}],
                "cover": None, "collection": None, "declarations": {}}

    async def arm_submission(self, page: Any) -> None:
        await self.check_challenge(page)
        await self._settings(page)
        button = page.get_by_role("button", name="发布", exact=True)
        if await button.count() != 1 or not await button.is_enabled():
            raise TaskError("UI_CHANGED", "Final action is not enabled", field="submit")
        self.submit_button = button

    async def submit_once(self, page: Any) -> dict | None:
        if self.submitted or self.submit_button is None:
            raise TaskError("INVALID_STATE", "Final action was already attempted")
        self.submitted = True
        await self.submit_button.click(timeout=0)
        while True:
            await self.check_challenge(page)
            if self.observed_receipt is not None:
                return self.observed_receipt
            if self.receipt_failed:
                raise TaskError("SUBMISSION_UNCERTAIN", "The final action may have reached the platform")
            await asyncio.sleep(0.1)

    async def list_works(self, page: Any, content_type: str) -> list[dict]:
        while self.works is None:
            await self.check_challenge(page)
            await asyncio.sleep(0.1)
        return self.works

    @staticmethod
    def work_observations(payload: dict) -> list[dict]:
        if payload.get("status_code") != 0:
            return []
        data = payload.get("data", payload)
        rows = data.get("aweme_list", []) if isinstance(data, dict) else []
        if not isinstance(rows, list):
            return []
        observations = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            status, video, author = (row.get(key) for key in ("status", "video", "author"))
            if not all(isinstance(value, dict) for value in (status, video, author)):
                continue
            assert isinstance(status, dict) and isinstance(video, dict) and isinstance(author, dict)
            title, desc, created = row.get("item_title"), row.get("desc"), row.get("create_time")
            content_id, account_id = row.get("aweme_id"), author.get("uid")
            if (row.get("aweme_type") != 4 or not isinstance(content_id, str) or not content_id
                    or not isinstance(account_id, str) or not account_id
                    or not isinstance(title, str) or not title or not isinstance(desc, str)
                    or not desc.startswith(title + " ") or type(created) is not int
                    or row.get("text_extra") != [] or not isinstance(video.get("vid"), str)
                    or not video["vid"] or type(video.get("width")) is not int or video["width"] <= 0
                    or type(video.get("height")) is not int or video["height"] <= 0
                    or status.get("is_delete") is not False):
                continue
            if status.get("is_prohibited") is True:
                result = "REJECTED"
            elif status.get("is_prohibited") is False and status.get("in_reviewing") is True:
                result = "IN_REVIEW"
            elif (status.get("is_prohibited") is False and status.get("in_reviewing") is False
                    and status.get("is_private") is False and type(status.get("private_status")) is int
                    and status["private_status"] == 0 and status.get("self_see") is False):
                result = "PUBLISHED"
            else:
                continue
            try:
                created_at = datetime.fromtimestamp(created, timezone.utc).isoformat()
            except (ValueError, OverflowError, OSError):
                continue
            observations.append({
                "platform": "douyin", "content_type": "video", "content_id": content_id,
                "account_id": account_id, "created_at": created_at, "platform_result": result,
                "source": "work_list", "evidence_scope": "douyin_video_v1",
                "metadata": {"title": title, "body": desc[len(title) + 1:], "tags": []},
                "remote_video_id": video["vid"],
            })
        return observations

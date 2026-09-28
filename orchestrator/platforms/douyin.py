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
        self.uploading = False
        self.commit: dict | None = None
        self.asset: Mapping[str, Any] | None = None
        self.media_ready = False
        self.saved_covers: list[str] | None = None
        self.in_cover_editor = False
        self.collection_tip_dismissed = False
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
            url = urlsplit(response.url)
            account_response = url.path in {IDENTITY_PATH, WORKS_PATH, SUBMIT_PATH}
            if account_response and response.status in {401, 403}:
                raise TaskError("AUTH_REQUIRED", "Account authentication is required")
            try:
                payload = await response.json()
            except Exception:
                continue
            if not isinstance(payload, dict) or response.status != 200:
                continue
            if account_response and payload.get("status_code") == 8:
                raise TaskError("AUTH_REQUIRED", "Account authentication is required")
            if url.path == IDENTITY_PATH:
                uid = payload.get("user", {}).get("uid")
                if payload.get("status_code") != 0 or not isinstance(uid, str) or not uid:
                    raise TaskError("AUTH_REQUIRED", "Account authentication is required")
                self.identity = uid
                self.identity_cookie_digest = await self._cookie_digest()
            elif url.path == WORKS_PATH:
                if payload.get("status_code") != 0:
                    raise TaskError("RECONCILIATION_UNAVAILABLE", "Read-only work verification is unavailable")
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
        if self.page is not page:
            self.page = page
            page.on("response", self._observe)
        self.works = None
        await page.goto(self.works_url if readonly else self.edit_url, wait_until="domcontentloaded", timeout=0)

    async def _cookie_digest(self) -> str:
        cookies = await self.page.context.cookies(self.origin)
        identity = sorted((item["name"], item["domain"], item["value"]) for item in cookies
                          if item["name"] in {"sessionid", "sessionid_ss", "uid_tt", "uid_tt_ss"})
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    def require_fresh_identity(self) -> None:
        """A resolved challenge needs a new site-observed identity response."""
        self.identity = None
        self.identity_cookie_digest = None

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
        dialogs = page.locator('[role="dialog"]:visible')
        if not self.in_cover_editor and await dialogs.count():
            # This exact informational tip was observed on the work-list page.
            # Acknowledging it cannot alter a work or answer an auth challenge.
            if (not self.collection_tip_dismissed and await dialogs.count() == 1
                    and urlsplit(page.url)[:3] == urlsplit(self.works_url)[:3]
                    and "".join((await dialogs.inner_text()).split()) == "合集管理移到这里啦知道了"
                    and await dialogs.get_by_role("button").count() == 1):
                button = dialogs.get_by_role("button", name="知道了", exact=True)
                if await button.count() == 1:
                    self.collection_tip_dismissed = True
                    tip = dialogs.filter(has_text="合集管理移到这里啦")
                    try:
                        await button.click(timeout=3000)
                        await tip.wait_for(state="hidden", timeout=3000)
                    except Exception:
                        # A new challenge can replace or cover the original
                        # tip. Classify it even when dismissal cannot finish.
                        await self.check_challenge(page)
                        raise
                    return await self.check_challenge(page)
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
                if await self._preview_ready(page):
                    self.media_ready = True
                    self.uploading = False
                    return
            await asyncio.sleep(0.1)

    async def _preview_ready(self, page: Any) -> bool:
        meta = self.commit.get("VideoMeta", {}) if self.commit is not None else {}
        uri = meta.get("Uri")
        videos = await page.locator("video").evaluate_all("""els => els.map(v => ({
            ready:v.readyState, width:v.videoWidth, height:v.videoHeight,
            duration:v.duration, src:v.currentSrc || v.src}))""")
        return (isinstance(uri, str) and bool(uri) and len(videos) == 1
                and videos[0]["ready"] >= 2 and videos[0]["width"] == meta.get("Width")
                and videos[0]["height"] == meta.get("Height") and videos[0]["duration"] > 0
                and urlsplit(videos[0]["src"]).path.endswith("/" + uri))

    @property
    def media_observation(self) -> dict | None:
        if self.media_ready and self.commit is not None and self.asset is not None:
            return {"video_id": self.commit["Vid"], "sha256": self.asset["sha256"]}
        return None

    async def _restore_video_preview(self, page: Any) -> None:
        if await page.locator("video").count():
            return
        # Saving a cover switches the editor to its cover/title preview tab.
        # Reopen the observed video tab so final validation reads a live media
        # element again, rather than relying on the old processing flag.
        tab = page.get_by_text("预览视频", exact=True)
        if await tab.count() != 1 or not await tab.is_visible():
            raise TaskError("UI_CHANGED", "Required page control is missing or ambiguous", field="media")
        await tab.click(timeout=5000)

        async def wait_ready() -> None:
            while not await self._preview_ready(page):
                await self.check_challenge(page)
                await asyncio.sleep(.1)

        try:
            await asyncio.wait_for(wait_ready(), timeout=5)
        except asyncio.TimeoutError:
            raise TaskError("UPLOAD_FAILED", "Media is no longer ready", field="media") from None

    async def _select_covers(self, page: Any) -> None:
        await self.check_challenge(page)
        await page.get_by_text("选择封面", exact=True).first.click(timeout=5000)
        self.in_cover_editor = True
        try:
            dialog = page.locator('[role="dialog"]:visible')
            await dialog.wait_for(state="visible", timeout=5000)
            if not await dialog.get_by_text("设置竖封面", exact=True).count():
                raise TaskError("UI_CHANGED", "Required page control is missing or ambiguous", field="cover")
            # The selected source-frame handle overlays the thumbnail strip;
            # click its real hit target, never force-click through the handle.
            frame = dialog.locator('[class^="slider-preview-"]')
            if await frame.count() != 1:
                raise TaskError("UI_CHANGED", "Required page control is missing or ambiguous", field="cover")
            await frame.click(timeout=5000)
            await dialog.get_by_role("button", name="设置横封面", exact=True).click(timeout=5000)
            await self.check_challenge(page)
            await frame.click(timeout=5000)
            await dialog.get_by_role("button", name="完成", exact=True).click(timeout=5000)
            await dialog.wait_for(state="hidden", timeout=5000)
        finally:
            self.in_cover_editor = False
        self.saved_covers = await self._cover_observation(page, wait_for_upload=True)

    async def _cover_observation(self, page: Any, *, wait_for_upload: bool = False) -> list[str]:
        # Anchor each image to its visible editor label. Never infer a saved
        # cover from a dismissed dialog; keep source identities only in memory.
        while True:
            check = await self._control(page, '.cover-check', 'cover')
            if not (await check.inner_text()).startswith('封面检测通过'):
                if not wait_for_upload:
                    raise TaskError("METADATA_MISMATCH", "Cover validation is no longer ready", field="cover")
                await asyncio.sleep(.1)
                continue
            observed = []
            pending = False
            for label, ratio_width, ratio_height in (("竖封面3:4", 3, 4), ("横封面4:3", 4, 3)):
                control = page.get_by_text(label, exact=True)
                if await control.count() != 1 or not await control.is_visible():
                    raise TaskError("UI_CHANGED", "Required page control is missing or ambiguous", field="cover")
                details = await control.evaluate("""async el => {
                    for(let node=el.parentElement; node && node !== document.body; node=node.parentElement) {
                        if(node.textContent.includes('竖封面3:4') && node.textContent.includes('横封面4:3')) break;
                        const images=[...node.querySelectorAll('img')].filter(img=>img.getClientRects().length);
                        if(images.length) {
                            const img=images[0];
                            if(images.length!==1) return null;
                            try { await img.decode(); } catch { return null; }
                            return img.complete && img.naturalWidth>0 && img.naturalHeight>0
                                ? {source:img.currentSrc || img.src,width:img.naturalWidth,height:img.naturalHeight} : null;
                        }
                    }
                    return null;
                }""")
                if not isinstance(details, dict) or not details.get('source'):
                    raise TaskError("METADATA_MISMATCH", "Saved cover is not ready", field="cover")
                source = details['source']
                scheme = urlsplit(source).scheme
                # The editor first shows the uncropped video poster, then
                # installs the uploaded crops. Allow one pixel of rounding.
                cropped = abs(details['width'] * ratio_height - details['height'] * ratio_width) <= max(ratio_width, ratio_height)
                if scheme not in {"https", "http"} or not cropped:
                    if wait_for_upload and scheme in {"https", "http", "blob", "data"}:
                        pending = True
                        break
                    raise TaskError("METADATA_MISMATCH", "Saved cover upload is not complete", field="cover")
                observed.append(hashlib.sha256(source.encode()).hexdigest())
            if not pending:
                return observed
            await self.check_challenge(page)
            await asyncio.sleep(.1)

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
        await self._restore_video_preview(page)
        if not self.media_ready or self.asset is None or not await self._preview_ready(page):
            raise TaskError("UPLOAD_FAILED", "Media is no longer ready", field="media")
        if self.saved_covers is None or await self._cover_observation(page) != self.saved_covers:
            raise TaskError("METADATA_MISMATCH", "Saved cover changed before submission", field="cover")
        title = await self._control(page, self.selectors.title, "title")
        body = await self._control(page, self.selectors.body, "body")
        actual_body = await body.evaluate(r"""root => {
            const lines=[...root.children];
            if(!lines.length || [...root.childNodes].some(n=>n.nodeType===3 && n.textContent)
                || !lines.every(line=>line.matches('.ace-line[data-node="true"]')
                    && line.children.length===1 && line.firstElementChild.matches('[data-line-wrapper="true"]')))
                return root.innerText;
            return lines.map(line=>{
                const copy=line.firstElementChild.cloneNode(true);
                // The editor inserts this explicitly marked cursor sentinel.
                // Preserve user-authored zero-width characters and line breaks.
                for(const marker of copy.querySelectorAll('[data-string="true"][data-enter="true"]'))
                    if(!marker.childElementCount && marker.textContent==='\u200b') marker.remove();
                return copy.textContent;
            }).join('\n');
        }""")
        return {"title": await title.input_value(), "body": actual_body, "tags": [],
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
        return await self.wait_for_receipt(page)

    async def wait_for_receipt(self, page: Any) -> dict:
        """Continue observing an existing submission without another click."""
        if not self.submitted:
            raise TaskError("INVALID_STATE", "No submission is awaiting a receipt")
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

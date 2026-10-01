"""Observed Xiaohongshu video preparation; final publication stays disabled."""
from __future__ import annotations

import asyncio
from collections import deque
import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

from orchestrator.errors import TaskError
from orchestrator.file_parts_process import MAX_PARTS, MAX_SIZE
from orchestrator.platforms.dom import DomPagePort, EditorSelectors
from orchestrator.preflight_process import run_child_request


IDENTITY_PATH = "/api/galaxy/user/info"
PERMIT_PATH = "/api/media/v1/upload/creator/permit"
AUTH_COOKIES = frozenset({"access-token-creator.xiaohongshu.com", "customer-sso-sid",
                          "galaxy.creator.beaker.session.id", "galaxy_creator_session_id",
                          "x-user-id-creator.xiaohongshu.com"})


class XiaohongshuVideoPreparationPort(DomPagePort):
    origin = "https://creator.xiaohongshu.com"
    verification_scope = "xiaohongshu_video_prepare_v1"
    capability_version = "1.1.0"
    upload_hosts = frozenset({"ros-upload.xiaohongshu.com", "ros-upload-d4.xhscdn.com"})

    def __init__(self):
        scope = type(self).verification_scope
        super().__init__("xiaohongshu", EditorSelectors(
            upload="input.upload-input", title='input[placeholder="填写标题会有更多赞哦"]',
            body='.tiptap[contenteditable="true"]', submit='[data-disabled-publication-control]',
        ), edit_url=self.origin + "/publish/publish?from=homepage&target=video",
            works_url=self.origin + "/new/note-manager")
        self.verification_scope = scope
        self.pending: deque[Any] = deque(maxlen=128)
        self.page: Any = None
        self.identity: str | None = None
        self.identity_digest: str | None = None
        self.asset: Mapping[str, Any] | None = None
        self.fingerprints: dict | None = None
        self.permitted: set[str] = set()
        self.parts: dict[tuple[str, str], dict[int, tuple[str, str, int]]] = {}
        self.invalid_uploads: set[tuple[str,str]] = set()
        self.completed: set[str] = set()
        self.uploaded_id: str | None = None
        self.media_ready = False
        self.observer_overflow = False
        self.resources_unconfirmed = False

    @staticmethod
    def validate_scope(manifest: Mapping[str, Any]) -> None:
        if manifest["content_type"] != "video":
            raise TaskError("UNSUPPORTED", "Only video preparation is available", field="content_type")
        if manifest["facts"] != {"ownership": "original", "ai_usage": "none", "repost_source": None}:
            raise TaskError("UNSUPPORTED", "Requested facts are outside the verified scope", field="facts")
        for field in ("tags", "cover", "collection", "declarations"):
            if manifest.get(field):
                raise TaskError("UNSUPPORTED", "Requested field is outside the verified scope", field=field)
        if len(manifest["media"]) != 1 or manifest["media"][0]["version"]["size_bytes"] > MAX_SIZE:
            raise TaskError("UNSUPPORTED", "One video of at most 32 MiB is supported", field="media")
        for field, limit in (("title", 20), ("body", 1000)):
            if len(manifest[field].encode("utf-16-le")) // 2 > limit:
                raise TaskError("INVALID_INPUT", "Content exceeds the preparation limit", field=field)

    def _observe(self, response: Any) -> None:
        url = urlsplit(response.url)
        if ((f"{url.scheme}://{url.netloc}" == self.origin and url.path in {IDENTITY_PATH, PERMIT_PATH})
                or (self.asset is not None and url.netloc in self.upload_hosts and url.path.startswith("/spectrum/"))):
            if len(self.pending) == self.pending.maxlen:
                self.observer_overflow = True
            self.pending.append(response)

    async def _cookie_digest(self) -> str:
        cookies = await self.page.context.cookies(self.origin)
        selected = sorted((cookie["name"], cookie["domain"], cookie["value"])
                          for cookie in cookies if cookie["name"] in AUTH_COOKIES)
        return hashlib.sha256(json.dumps(selected).encode()).hexdigest()

    async def _drain(self) -> None:
        if self.observer_overflow:
            raise TaskError("UI_CHANGED", "Upload observation exceeded its bounded capacity")
        while self.pending:
            response = self.pending.popleft()
            url = urlsplit(response.url)
            if url.path == IDENTITY_PATH:
                if response.status in {401, 403}:
                    raise TaskError("AUTH_REQUIRED", "Account authentication is required")
                payload = await response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                uid = data.get("userId") if isinstance(data, dict) else None
                if (response.status != 200 or not isinstance(payload, dict) or payload.get("success") is not True
                        or payload.get("code") != 0 or not isinstance(uid, str) or not re.fullmatch(r"[a-f0-9]{24}", uid)):
                    raise TaskError("AUTH_REQUIRED", "Verified account identity is unavailable")
                self.identity = uid
                self.identity_digest = await self._cookie_digest()
            elif url.path == PERMIT_PATH and self.asset is not None:
                if response.status in {401, 403}:
                    raise TaskError("AUTH_REQUIRED", "Account authentication is required")
                payload = await response.json()
                if (response.status != 200 or not isinstance(payload, dict)
                        or payload.get("success") is not True or payload.get("code") != 0):
                    raise TaskError("UPLOAD_FAILED", "Upload permission was not granted", field="media")
                for permit in payload.get("data", {}).get("uploadTempPermits", []):
                    self.permitted.update(value for value in permit.get("fileIds", []) if isinstance(value, str))
            elif self.fingerprints is not None and self.asset is not None:
                await self._observe_storage(response, url)

    async def _observe_storage(self, response: Any, url: Any) -> None:
        key = url.path.lstrip("/")
        if key not in self.permitted or response.status != 200:
            return
        assert self.fingerprints is not None
        fingerprints = self.fingerprints
        query = parse_qs(url.query)
        method = response.request.method
        body = response.request.post_data_buffer
        if method == "PUT" and body is not None:
            digest = hashlib.sha256(body).hexdigest()
            etag = response.headers.get("etag", "").strip('"')
            if not query.get("partNumber") and digest == fingerprints["sha256"] and etag == fingerprints["md5"]:
                self.completed.add(key)
                return
            numbers, uploads = query.get("partNumber", []), query.get("uploadId", [])
            if len(numbers) != 1 or not numbers[0].isdigit() or len(uploads) != 1:
                return
            number = int(numbers[0])
            if 1 <= number <= MAX_PARTS and 0 < len(body) <= MAX_SIZE and etag == hashlib.md5(body).hexdigest():
                transaction = (key, uploads[0])
                parts = self.parts.setdefault(transaction,{})
                observation = (digest,etag,len(body))
                if number in parts and parts[number] != observation:
                    self.invalid_uploads.add(transaction)
                parts[number] = observation
        elif method == "POST" and len(query.get("uploadId", [])) == 1:
            if body is None or len(body) > 4096:
                return
            request_tree = ET.fromstring(body)
            requested: dict[int, str] = {}
            if request_tree.tag.rsplit("}", 1)[-1] != "CompleteMultipartUpload":
                return
            for node in request_tree:
                fields = {field.tag.rsplit("}", 1)[-1]: field.text for field in node}
                number = fields.get("PartNumber")
                if (node.tag.rsplit("}", 1)[-1] != "Part" or not isinstance(number, str)
                        or not number.isdigit() or int(number) in requested):
                    return
                requested[int(number)] = (fields.get("ETag") or "").strip('"')
            transaction = (key, query["uploadId"][0])
            parts = self.parts.get(transaction,{})
            if (transaction in self.invalid_uploads or not 1 <= len(requested) <= MAX_PARTS
                    or set(requested) != set(range(1,len(requested)+1))
                    or requested != {number:part[1] for number,part in parts.items()}
                    or sum(part[2] for part in parts.values()) != fingerprints["size"]):
                return
            text = await response.text()
            if len(text) > 4096:
                raise TaskError("UI_CHANGED", "Upload completion response exceeds its verified scope")
            tree = ET.fromstring(text)
            fields = {node.tag.rsplit("}", 1)[-1]: node.text for node in tree}
            if (tree.tag.rsplit("}", 1)[-1] == "CompleteMultipartUploadResult" and fields.get("Key") == key
                    and re.fullmatch(r"[a-f0-9]{32}-" + str(len(requested)), (fields.get("ETag") or "").strip('"'))):
                assert self.asset is not None
                try:
                    verified = await run_child_request({"path":self.asset["path"],"sha256":self.asset["sha256"],
                        "observed_parts":[{"sha256":parts[number][0],"md5":parts[number][1],"size":parts[number][2]}
                                          for number in range(1,len(parts)+1)]},module="orchestrator.file_parts_process")
                except TaskError as exc:
                    if exc.code == "RESOURCES_UNCONFIRMED":
                        self.resources_unconfirmed = True
                    raise
                if verified.get("verified") is True and verified.get("md5") == fingerprints["md5"]:
                    self.completed.add(key)

    async def navigate(self, page: Any, content_type: str, *, readonly: bool) -> None:
        if content_type != "video" or readonly:
            raise TaskError("UNSUPPORTED", "This port only prepares a video", field="content_type")
        self.page = page
        page.on("response", self._observe)
        await page.goto(self.edit_url, wait_until="domcontentloaded", timeout=0)

    async def check_challenge(self, page: Any) -> None:
        if page.is_closed():
            raise TaskError("PAGE_CLOSED", "Browser page was closed")
        await self._drain()
        if urlsplit(page.url).path.startswith("/login"):
            raise TaskError("AUTH_REQUIRED", "Account login is required")
        for selector in ("div[class*='login-box']", "[class*='captcha']"):
            for node in await page.locator(selector).all():
                if await node.is_visible():
                    raise TaskError("AUTH_REQUIRED", "Account verification is required")
        for label in ("实名验证", "短信验证", "扫码登录", "安全验证", "请完成验证"):
            for node in await page.get_by_text(label, exact=True).all():
                if await node.is_visible():
                    raise TaskError("AUTH_REQUIRED", "Account verification is required")
        for node in await page.locator('[role="dialog"], .d-modal').all():
            if await node.is_visible():
                raise TaskError("UI_CHANGED", "An unrecognized dialog blocks preparation")

    async def identify_account(self, page: Any) -> str:
        while self.identity is None:
            await self.check_challenge(page)
            await asyncio.sleep(.1)
        await self._drain()
        if await self._cookie_digest() != self.identity_digest:
            raise TaskError("AUTH_REQUIRED", "Account authentication changed")
        return self.identity

    async def upload(self, page: Any, manifest: Mapping[str, Any]) -> None:
        while True:
            await self.check_challenge(page)
            count = await page.locator(self.selectors.upload).count()
            if count > 1:
                raise TaskError("UI_CHANGED", "Video upload control is ambiguous", field="media")
            if count == 1:
                break
            await asyncio.sleep(.1)
        asset = manifest["media"][0]
        self.asset = asset
        try:
            self.fingerprints = await run_child_request({"path": asset["path"], "sha256": asset["sha256"]},
                                                        module="orchestrator.file_parts_process")
        except TaskError as exc:
            if exc.code == "RESOURCES_UNCONFIRMED":
                self.resources_unconfirmed = True
            raise
        await super().upload(page, manifest)

    async def _preview(self, page: Any) -> bool:
        video = page.locator("video.exact-video")
        if await video.count() != 1 or self.asset is None:
            return False
        observed = await video.evaluate("""async e => {
          if(e.readyState<2 || !e.videoWidth || !e.videoHeight || !Number.isFinite(e.duration) || e.duration<=0)
            return null;
          if(!e.currentSrc.startsWith('blob:'+location.origin+'/')) return null;
          const data=await fetch(e.currentSrc).then(r=>r.arrayBuffer());
          const hash=await crypto.subtle.digest('SHA-256', data);
          return {sha256:Array.from(new Uint8Array(hash)).map(b=>b.toString(16).padStart(2,'0')).join(''),size:data.byteLength};
        }""")
        return (isinstance(observed, dict) and observed.get("sha256") == self.asset["sha256"]
                and observed.get("size") == self.asset["version"]["size_bytes"])

    async def wait_media(self, page: Any, manifest: Mapping[str, Any]) -> None:
        while True:
            await self.check_challenge(page)
            if len(self.completed) > 1:
                raise TaskError("UI_CHANGED", "Uploaded media identity is ambiguous", field="media")
            if len(self.completed) == 1 and await self._preview(page):
                self.uploaded_id = next(iter(self.completed))
                self.media_ready = True
                return
            for text in ("上传失败", "视频处理失败"):
                for node in await page.get_by_text(text, exact=True).all():
                    if await node.is_visible():
                        raise TaskError("UPLOAD_FAILED", "Video upload failed", field="media")
            await asyncio.sleep(.1)

    async def apply_fields(self, page: Any, manifest: Mapping[str, Any]) -> None:
        await self._fill(page, self.selectors.title, "title", manifest["title"])
        await self._fill(page, self.selectors.body, "body", manifest["body"])

    async def _settings(self, page: Any) -> None:
        public = page.locator('.d-select-description').filter(has_text=re.compile("^公开可见$"))
        declaration = page.locator('.d-select-content').filter(has=page.get_by_text("添加内容类型声明", exact=True))
        collection = page.locator('.collection-plugin-create').filter(has_text="创建合集")
        for control in (public, declaration, collection, page.get_by_text("默认截取第一帧作为封面，优质的封面会吸引更多人浏览笔记", exact=True)):
            if await control.count() != 1 or not await control.is_visible():
                raise TaskError("UI_CHANGED", "Default editor settings cannot be verified")
        if await declaration.locator('.d-select-description').inner_text() != "":
            raise TaskError("METADATA_MISMATCH", "A content declaration is selected", field="declarations")
        for label in ("定时发布", "原创声明"):
            control = page.locator('.custom-switch-card').filter(has=page.get_by_text(label, exact=True)).locator('input[type="checkbox"]')
            if await control.count() != 1 or await control.is_checked():
                raise TaskError("METADATA_MISMATCH", "An unrequested setting is selected", field=label)
        pk = page.locator('.cover-plugin-preview input[type="checkbox"]')
        if await pk.count() != 1 or await pk.is_checked():
            raise TaskError("METADATA_MISMATCH", "PK cover setting cannot be verified", field="cover")

    async def readback(self, page: Any) -> dict:
        await self.check_challenge(page)
        await self._settings(page)
        if not self.media_ready or self.uploaded_id not in self.completed or not await self._preview(page):
            raise TaskError("UPLOAD_FAILED", "Uploaded video is no longer ready", field="media")
        title = await self._control(page, self.selectors.title, "title")
        body = await self._control(page, self.selectors.body, "body")
        text = await body.evaluate("""e => {
          if(Array.from(e.childNodes).some(n=>n.nodeType!==1 || n.tagName!=='P')) return null;
          const paragraphs=[];
          for(const p of e.children) {
            if(Array.from(p.childNodes).some(n=>n.nodeType!==3 && !(n.nodeType===1 && n.tagName==='BR'))) return null;
            paragraphs.push(p.childNodes.length===1 && p.firstChild.nodeName==='BR' ? '' :
              Array.from(p.childNodes).map(n=>n.nodeType===3?n.textContent:'\\n').join(''));
          }
          return paragraphs.join('\\n');
        }""")
        if not isinstance(text, str):
            raise TaskError("UI_CHANGED", "Unrecognized rich-text structure", field="body")
        assert self.asset is not None
        return {"title": await title.input_value(), "body": text, "tags": [],
                "media": [{"kind": "video", "sha256": self.asset["sha256"]}],
                "cover": None, "collection": None, "declarations": {}}

    async def arm_submission(self, page: Any) -> None:
        raise TaskError("UNSUPPORTED", "Xiaohongshu publication has not been verified", field="platform")

    async def submit_once(self, page: Any) -> dict | None:
        raise TaskError("UNSUPPORTED", "Xiaohongshu publication has not been verified", field="platform")

    async def list_works(self, page: Any, content_type: str) -> list[dict]:
        raise TaskError("UNSUPPORTED", "Full work-detail readback has not been verified", field="platform")

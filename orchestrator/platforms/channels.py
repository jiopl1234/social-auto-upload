"""Observed Channels video editor and bounded, source-linked upload proof."""
from __future__ import annotations

import asyncio
from collections import deque
import hashlib
import json
import re
from types import SimpleNamespace
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from orchestrator.errors import TaskError
from orchestrator.file_parts_process import MAX_PARTS, MAX_SIZE
from orchestrator.platforms.dom import DomPagePort, EditorSelectors
from orchestrator.preflight_process import run_child_request


ORIGIN = "https://channels.weixin.qq.com"
API = "/cgi-bin/mmfinderassistant-bin/"
MICRO_API = "/micro/content" + API
IDENTITY_PATH = API + "auth/auth_data"
CREATE_PATH = MICRO_API + "post/post_create"
LIST_PATH = MICRO_API + "post/post_list"
DETAIL_PATH = MICRO_API + "post/get_post_info"
ACCOUNT_PATTERN = r"v2_[a-f0-9]{32,160}@finder"


def media_identity(url: Any) -> str | None:
    """Retain a media-key digest, never a signed download URL."""
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    if (parsed.scheme not in {"https", "http"}
            or parsed.hostname not in {"wxapp.tc.qq.com", "finder.video.qq.com"}
            or parsed.path != "/251/20302/stodownload"):
        return None
    keys = parse_qs(parsed.query).get("encfilekey", [])
    if len(keys) != 1 or not 16 <= len(keys[0]) <= 2048:
        return None
    return "wxvideo_" + hashlib.sha256(keys[0].encode()).hexdigest()


def positive_data(payload: Any) -> dict | None:
    if not isinstance(payload, dict) or type(payload.get("errCode")) is not int or payload["errCode"] != 0:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    base = data.get("baseResp")
    if base is not None and (not isinstance(base, dict) or type(base.get("errcode")) is not int or base["errcode"] != 0):
        return None
    return data


class ChannelsVideoPreparationPort(DomPagePort):
    origin = ORIGIN
    verification_scope = "channels_video_prepare_v1"
    capability_version = "1.1.0"
    upload_hosts = frozenset("finderassistance" + letter + ".video.qq.com" for letter in "abcde")

    def __init__(self):
        scope = type(self).verification_scope
        super().__init__("tencent", EditorSelectors(
            upload='input[type="file"][accept*="video"]',
            title='input[placeholder="填写短标题有机会获得更多流量"]',
            body='.input-editor[contenteditable]', submit='[data-disabled-publication-control]',
        ), edit_url=ORIGIN + "/platform/post/create", works_url=ORIGIN + "/platform/post/list")
        self.verification_scope = scope
        self.page: Any = None
        self.pending: deque[asyncio.Task] = deque(maxlen=128)
        self.budget: Any = None
        self.observer_overflow = False
        self.identity: str | None = None
        self.identity_digest: str | None = None
        self.identity_request: dict | None = None
        self.asset: Mapping[str, Any] | None = None
        self.fingerprints: dict | None = None
        self.parts: dict[str, dict[int, dict]] = {}
        self.invalid_uploads: set[str] = set()
        self.completed: set[str] = set()
        self.uploaded_id: str | None = None
        self.media_ready = False
        self.resources_unconfirmed = False
        self.create_requests = 0
        self.readonly = False

    @staticmethod
    def validate_scope(manifest: Mapping[str, Any]) -> None:
        if manifest["content_type"] != "video":
            raise TaskError("UNSUPPORTED", "Only video delivery is available", field="content_type")
        if manifest["facts"] != {"ownership": "original", "ai_usage": "none", "repost_source": None}:
            raise TaskError("UNSUPPORTED", "Requested facts are outside the verified scope", field="facts")
        for field in ("tags", "cover", "collection", "declarations"):
            if manifest.get(field):
                raise TaskError("UNSUPPORTED", "Requested field is outside the verified scope", field=field)
        if len(manifest["media"]) != 1 or manifest["media"][0]["version"]["size_bytes"] > MAX_SIZE:
            raise TaskError("UNSUPPORTED", "One video of at most 32 MiB is supported", field="media")
        title = manifest["title"]
        if not 6 <= len(title.encode("utf-16-le")) // 2 <= 16 or not re.fullmatch(r'[\u2103\u4E00-\u9FA5A-Za-z0-9《》"“”:：+?？%\s]+', title):
            raise TaskError("INVALID_INPUT", "Short title is outside the observed editor limits", field="title")
        if len(manifest["body"].encode("utf-16-le")) // 2 > 1000:
            raise TaskError("INVALID_INPUT", "Description exceeds the verified scope", field="body")
        if "\n" in manifest["body"] or "\r" in manifest["body"]:
            raise TaskError("UNSUPPORTED", "Only a single-paragraph description is verified", field="body")

    def _observe(self, response: Any) -> None:
        parsed = urlsplit(response.url)
        if ((f"{parsed.scheme}://{parsed.netloc}" == self.origin and parsed.path == IDENTITY_PATH)
                or (self.asset is not None and parsed.hostname in self.upload_hosts
                    and parsed.path in {"/uploadpartdfs", "/completepartuploaddfs"})):
            if len(self.pending) == self.pending.maxlen:
                self.observer_overflow = True
                return
            task = asyncio.create_task(self._cache_response(response))
            task.add_done_callback(_consume_cache_task)
            if self.budget is not None:
                self.budget.track(task)
            self.pending.append(task)

    def bind_context(self, context: Any) -> None:
        self.budget = context.budget

    def preparation_storage_state(self, state: dict) -> dict:
        """Start a new upload transaction while retaining auth/device state."""
        return {**state, 'origins': [{**origin, 'localStorage': [entry for entry in origin['localStorage']
            if not (origin['origin'] == self.origin
                    and re.fullmatch(r'AssistantUploadedInfoStorageKey_[0-9]+', entry['name']))]}
            for origin in state['origins']]}

    async def _cache_response(self, response: Any) -> Any:
        # Capture small protocol replies at arrival, before later driver work
        # can evict their response bodies. Raw replies stay in attempt memory.
        text = await response.text()
        if len(text) > 1048576:
            raise TaskError("UI_CHANGED", "Protocol response exceeds the bounded scope")
        payload = json.loads(text)
        async def decoded():
            return payload
        return SimpleNamespace(url=response.url, status=response.status, request=response.request, json=decoded)

    async def _cookie_digest(self) -> str:
        cookies = await self.page.context.cookies(self.origin)
        selected = sorted((c["name"], c["domain"], c["value"]) for c in cookies if c["name"] in {"sessionid", "wxuin"})
        return hashlib.sha256(json.dumps(selected).encode()).hexdigest()

    async def _drain(self) -> None:
        if self.observer_overflow:
            raise TaskError("UI_CHANGED", "Observation exceeded its bounded capacity")
        while self.pending:
            response = await self.pending.popleft()
            parsed = urlsplit(response.url)
            if parsed.path == IDENTITY_PATH:
                data = positive_data(await response.json()) if response.status in {200, 201} else None
                user = data.get("finderUser") if data is not None else None
                uid = user.get("finderUsername") if isinstance(user, dict) else None
                if not isinstance(uid, str) or not re.fullmatch(ACCOUNT_PATTERN, uid):
                    raise TaskError("AUTH_REQUIRED", "Verified account identity is unavailable")
                self.identity, self.identity_digest = uid, await self._cookie_digest()
                self.identity_request = response.request.post_data_json
            elif self.asset is not None and self.fingerprints is not None:
                await self._observe_storage(response, parsed)

    async def _source_parts(self, observed: list[dict] | None = None) -> dict:
        assert self.asset is not None
        request: dict = {"path": self.asset["path"], "sha256": self.asset["sha256"]}
        if observed is not None:
            request["observed_parts"] = observed
        try:
            return await run_child_request(request, module="orchestrator.file_parts_process")
        except TaskError as exc:
            if exc.code == "RESOURCES_UNCONFIRMED":
                self.resources_unconfirmed = True
            raise

    async def _observe_storage(self, response: Any, parsed: Any) -> None:
        if response.status != 200 or parsed.hostname not in self.upload_hosts:
            return
        query = parse_qs(parsed.query)
        uploads = query.get("UploadID", [])
        if len(uploads) != 1 or not 1 <= len(uploads[0]) <= 2048:
            return
        # The observed frontend concatenates unescaped base64 into its query.
        upload_id = uploads[0].replace(" ", "+")
        payload = await response.json()
        body = response.request.post_data_buffer
        if body is None or not 0 < len(body) <= MAX_SIZE:
            return
        if parsed.path == "/uploadpartdfs" and response.request.method == "PUT":
            numbers = query.get("PartNumber", [])
            if len(numbers) != 1 or not numbers[0].isdigit() or not 1 <= int(numbers[0]) <= MAX_PARTS:
                return
            etag = payload.get("ETag") if isinstance(payload, dict) else None
            md5 = hashlib.md5(body).hexdigest()
            if not isinstance(etag, str) or etag.strip('"') != md5 or type(payload.get("X-Errno")) is not int or payload["X-Errno"] != 0:
                return
            if upload_id not in self.parts and len(self.parts) >= 8:
                raise TaskError("UI_CHANGED", "Upload transaction capacity exceeded")
            parts = self.parts.setdefault(upload_id, {})
            number = int(numbers[0])
            observation = {"size": len(body), "sha256": hashlib.sha256(body).hexdigest(), "md5": md5}
            if number in parts and parts[number] != observation:
                self.invalid_uploads.add(upload_id)
            parts[number] = observation
        elif parsed.path == "/completepartuploaddfs" and response.request.method == "POST":
            if len(body) > 16384 or not isinstance(payload, dict):
                return
            video_id = media_identity(payload.get("DownloadURL"))
            if video_id is None:
                return  # A generated cover uses file type 20304, not the video.
            request = json.loads(body)
            requested = request.get("PartInfo") if isinstance(request, dict) else None
            parts = self.parts.get(upload_id, {})
            if (upload_id in self.invalid_uploads or not isinstance(requested, list) or not 1 <= len(requested) <= MAX_PARTS
                    or len(parts) != len(requested) or set(parts) != set(range(1, len(parts) + 1))
                    or any(not isinstance(p, dict) or type(p.get("PartNumber")) is not int
                           or p["PartNumber"] != n or p.get("ETag") != '"' + parts[n]["md5"] + '"'
                           for n, p in enumerate(requested, 1))):
                return
            assert self.fingerprints is not None
            if sum(p["size"] for p in parts.values()) != self.fingerprints["size"]:
                return
            verified = await self._source_parts([parts[n] for n in range(1, len(parts) + 1)])
            if verified.get("verified") is True and verified.get("md5") == self.fingerprints["md5"]:
                self.completed.add(video_id)

    async def _route(self, route: Any) -> None:
        """Block implicit draft writes and the frontend's own create retries."""
        parsed = urlsplit(route.request.url)
        if parsed.hostname == urlsplit(self.origin).hostname and "/cgi-bin/" in parsed.path and route.request.method not in {"GET", "HEAD", "OPTIONS"}:
            path = parsed.path.removeprefix("/micro/content")
            if self.readonly and not self._readonly_endpoint(path):
                await route.abort()
                return
            if path == API + "post/post_create":
                if self.readonly or not self.submitted or self.create_requests:
                    await route.abort()
                    return
                self.create_requests += 1
            elif path == API + "post/post_clip_video":
                # The creator uses this upload-processing RPC for HEVC input;
                # it creates no published work. Never permit it in recovery.
                if self.readonly or self.submitted or self.asset is None:
                    await route.abort()
                    return
            elif ("/post/" in path or "/collection/" in path) and not self._read_endpoint(path):
                await route.abort()
                return
        await route.fallback()

    @staticmethod
    def _read_endpoint(path: str) -> bool:
        return path in {API + p for p in (
            "post/post_list", "post/get_post_info", "post/get_finder_post_comm_info",
            "post/finder_author_get_post_verify_info", "post/get-finder-post-trace-key",
            "post/check_finder_comm_face", "post/finder_get_object_tag_list", "collection/get_collection_list",
            "post/post_clip_video_result", "collection/search_collection",
        )}

    @classmethod
    def _readonly_endpoint(cls, path: str) -> bool:
        if cls._read_endpoint(path):
            return True
        return path in {API + p for p in (
            "auth/auth_data", "auth/get_auth_info", "auth/list_talent_relation_by_bind_uin",
            "auth/mp_finder_window_init", "active-auth/is-in-finder-whitelist",
            "helper/helper_upload_params", "notification/notification_list",
            "component/get_admin_bound_component_list", "vip/get-user-member-service-status",
            "statistic/new_post_total_data", "statistic/fans_trend", "shop/get_finder_ec_info_for_opening_page",
        )}

    async def navigate(self, page: Any, content_type: str, *, readonly: bool) -> None:
        if content_type != "video" or readonly:
            raise TaskError("UNSUPPORTED", "This port only prepares a video", field="content_type")
        await self._navigate(page, readonly=False)

    async def _navigate(self, page: Any, *, readonly: bool) -> None:
        if self.page is not None:
            self.page.remove_listener("response", self._observe)
        self.page, self.readonly = page, readonly
        self.identity = self.identity_digest = None
        while self.pending:
            task = self.pending.popleft()
            if not task.done():
                task.cancel()
        page.on("response", self._observe)
        # Intercept creator APIs only. Intercepting binary CDN PUTs can evict
        # their small replies from the pinned driver's inspector cache.
        await page.route(self.origin + "/cgi-bin/**", self._route)
        await page.route(self.origin + "/micro/content/cgi-bin/**", self._route)
        await page.goto(self.works_url if readonly else self.edit_url, wait_until="domcontentloaded", timeout=0)

    async def check_challenge(self, page: Any) -> None:
        if page.is_closed():
            raise TaskError("PAGE_CLOSED", "Browser page was closed")
        await self._drain()
        if urlsplit(page.url).path.startswith("/login"):
            raise TaskError("AUTH_REQUIRED", "Account login is required")
        # Locators pierce the editor's open Shadow DOM; document.body does not.
        for selector in ('[class*="captcha"]:visible', 'input[placeholder*="验证码"]:visible', '[data-challenge]:visible'):
            if await page.locator(selector).count():
                raise TaskError("AUTH_REQUIRED", "Account verification is required", field="verification")
        for label in ("实名验证", "短信验证", "扫码登录", "安全验证", "请完成验证", "管理员扫码验证"):
            for node in await page.get_by_text(label, exact=False).all():
                if await node.is_visible():
                    raise TaskError("AUTH_REQUIRED", "Account verification is required", field="verification")
        if await page.locator('[role="dialog"]:visible, .weui-desktop-dialog__wrp:visible').count():
            raise TaskError("UI_CHANGED", "An unrecognized dialog blocks the operation")

    async def identify_account(self, page: Any) -> str:
        while self.identity is None:
            await self.check_challenge(page)
            await asyncio.sleep(.1)
        await self._drain()
        if await self._cookie_digest() != self.identity_digest:
            raise TaskError("AUTH_REQUIRED", "Account authentication changed")
        return self.identity

    async def upload(self, page: Any, manifest: Mapping[str, Any]) -> None:
        while await page.locator(self.selectors.upload).count() == 0:
            await self.check_challenge(page)
            await asyncio.sleep(.1)
        self.asset = manifest["media"][0]
        self.fingerprints = await self._source_parts()
        await super().upload(page, manifest)

    async def _preview(self, page: Any) -> bool:
        if self.asset is None:
            return False
        videos = await page.locator('video').all()
        if len(videos) > 8:
            raise TaskError("UI_CHANGED", "Preview capacity exceeded", field="media")
        source_matches = False
        remote_matches = False
        for video in videos:
            value = await video.evaluate("""async e=>{
                if(e.readyState<2 || !e.videoWidth || !e.videoHeight || !Number.isFinite(e.duration) || e.duration<=0)return null;
                if(e.currentSrc.startsWith('blob:'+location.origin+'/')){
                  const data=await fetch(e.currentSrc).then(r=>r.arrayBuffer());
                  if(data.byteLength>33554432)return null;
                  const hash=await crypto.subtle.digest('SHA-256',data);
                  return {sha256:Array.from(new Uint8Array(hash)).map(x=>x.toString(16).padStart(2,'0')).join(''),size:data.byteLength};
                }return {url:e.currentSrc};
            }""")
            if isinstance(value, dict):
                source_matches |= value.get("sha256") == self.asset["sha256"] and value.get("size") == self.asset["version"]["size_bytes"]
                remote_matches |= media_identity(value.get("url")) in self.completed
        return source_matches and remote_matches

    async def wait_media(self, page: Any, manifest: Mapping[str, Any]) -> None:
        while True:
            await self.check_challenge(page)
            for label in ("上传失败", "视频处理失败", "网络出错，请重新上传。"):
                for node in await page.get_by_text(label, exact=True).all():
                    if await node.is_visible():
                        raise TaskError("UPLOAD_FAILED", "Video upload or processing failed", field="media")
            if len(self.completed) > 1:
                raise TaskError("UI_CHANGED", "Uploaded media identity is ambiguous", field="media")
            if len(self.completed) == 1 and await self._preview(page):
                # Decoding the uploaded source is insufficient: the editor
                # may still be transcoding it or generating its default cover.
                button = page.get_by_role("button", name="发表", exact=True)
                if (await button.count() != 1 or not await button.is_enabled()
                        or "weui-desktop-btn_disabled" in (await button.get_attribute("class") or "")):
                    await asyncio.sleep(.1)
                    continue
                self.uploaded_id = next(iter(self.completed))
                self.media_ready = True
                return
            await asyncio.sleep(.1)

    async def apply_fields(self, page: Any, manifest: Mapping[str, Any]) -> None:
        await self._fill(page, self.selectors.title, "title", manifest["title"])
        await self._fill(page, self.selectors.body, "body", manifest["body"])
        position = await self._control(page, '.position-display', "location")
        if await position.inner_text() != "不显示位置":
            await position.click(timeout=0)
            option = await self._control(page, '.location-filter-wrap .option-item:has(.name:text-is("不显示位置"))', "location")
            await option.click(timeout=0)
        await page.locator(self.selectors.title).blur()

    async def _settings(self, page: Any) -> None:
        expected = {'.position-display': "不显示位置", '.post-album-display': "选择合集",
                    '.link-display-wrap': "选择链接", '.activity-display': "不参与活动",
                    '.mark-tag-select .select-display': "选择视频标注"}
        for selector, text in expected.items():
            node = await self._control(page, selector, "settings")
            if (await node.inner_text()).strip() != text:
                raise TaskError("METADATA_MISMATCH", "An unrequested editor setting is selected", field="settings")
        radios = page.locator('.form input[type="radio"]')
        if await radios.count() != 2 or not await radios.nth(0).is_checked() or await radios.nth(1).is_checked():
            raise TaskError("METADATA_MISMATCH", "Immediate publication is not selected", field="schedule")
        if await radios.nth(0).input_value() != "0" or await radios.nth(1).input_value() != "1":
            raise TaskError("UI_CHANGED", "Publication schedule controls changed")
        if await page.locator('.form input[type="checkbox"]:checked').count():
            raise TaskError("METADATA_MISMATCH", "An unrequested declaration is selected", field="declarations")
        if await page.locator('.input-editor a, .input-editor [contenteditable="false"]').count():
            raise TaskError("METADATA_MISMATCH", "Unrequested topics or mentions are present", field="tags")

    async def readback(self, page: Any) -> dict:
        await self.check_challenge(page)
        await self._settings(page)
        if not self.media_ready or self.uploaded_id not in self.completed or not await self._preview(page):
            raise TaskError("UPLOAD_FAILED", "Uploaded video is no longer ready", field="media")
        title = await self._control(page, self.selectors.title, "title")
        body = await self._control(page, self.selectors.body, "body")
        assert self.asset is not None
        return {"title": await title.input_value(), "body": await body.inner_text(), "tags": [],
                "media": [{"kind": "video", "sha256": self.asset["sha256"]}],
                "cover": None, "collection": None, "declarations": {}}

    @property
    def media_observation(self) -> dict | None:
        if not self.media_ready or self.asset is None or self.fingerprints is None:
            return None
        return {"video_id": self.uploaded_id, "sha256": self.asset["sha256"], "md5": self.fingerprints["md5"]}

    async def arm_submission(self, page: Any) -> None:
        raise TaskError("UNSUPPORTED", "This port only prepares a video")

    async def submit_once(self, page: Any) -> dict | None:
        raise TaskError("UNSUPPORTED", "This port only prepares a video")

    async def list_works(self, page: Any, content_type: str) -> list[dict]:
        raise TaskError("UNSUPPORTED", "Work readback is not available in preparation")


def _consume_cache_task(task: asyncio.Task) -> None:
    if not task.cancelled():
        try:
            task.exception()
        except BaseException:
            pass

"""One-shot creator publication with independently observed work details."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from orchestrator.errors import TaskError
from orchestrator.platforms.xiaohongshu import XiaohongshuVideoPreparationPort
from orchestrator.preflight_process import run_child_request

PUBLISH_PATH = "/web_api/sns/v2/note"
DETAIL_PATH = "/web_api/sns/capa/postgw/note/detail"
LIST_PATH = "/api/galaxy/v2/creator/note/user/posted"


class XiaohongshuVideoPort(XiaohongshuVideoPreparationPort):
    verification_scope = "xiaohongshu_video_v1"
    capability_version = "1.2.1"
    api_origin = "https://edith.xiaohongshu.com"

    def __init__(self):
        super().__init__()
        self.read_responses: deque[Any] = deque(maxlen=32)
        self.listed: list[dict] | None = None
        self.listing_complete = False
        self.details: dict[str, dict] = {}
        self.reconciliation_target: str | None = None
        self.submit_button: Any = None
        self.submitted = False
        self.observed_receipt: dict | None = None
        self.receipt_failed = False
        self.receipt_auth_required = False
        self.receipt_task: asyncio.Task | None = None
        self.receipt_observation: dict | None = None
        self.receipt_responses: deque[Any] = deque(maxlen=4)
        self.identity_refresh_required = False

    @property
    def media_observation(self) -> dict | None:
        if not self.media_ready or self.asset is None or self.uploaded_id is None:
            return None
        if self.fingerprints is None:
            return None
        return {"video_id": self.uploaded_id, "sha256": self.asset["sha256"], "md5": self.fingerprints["md5"]}

    async def ensure_reconciliation_evidence(self, context: Any) -> None:
        job = context.store.get(context.job_id)
        uploads = [item for item in job["evidence"] if item.get("kind") == "uploaded_video"
                   and item.get("attempt_id") == context.attempt_id]
        if job["status"] != "UNKNOWN_COMMIT" or len(uploads) != 1 or uploads[0].get("md5") is not None:
            return
        asset = context.manifest["media"][0]
        if uploads[0].get("sha256") != asset["sha256"]:
            raise TaskError("RECONCILIATION_UNAVAILABLE", "Recovery upload does not match its source snapshot")
        try:
            fingerprints = await run_child_request({"path": asset["path"], "sha256": asset["sha256"]},
                                                   module="orchestrator.file_parts_process")
        except TaskError as exc:
            if exc.code == "RESOURCES_UNCONFIRMED":
                self.resources_unconfirmed = True
            raise
        context.assert_owner()
        context.store.record_recovery_source_md5(context.job_id, context.lease.token,
                                                 sha256=fingerprints["sha256"], md5=fingerprints["md5"])

    def set_reconciliation_target(self, receipt_id: str | None) -> None:
        self.reconciliation_target = receipt_id if isinstance(receipt_id, str) and re.fullmatch(r"[a-f0-9]{24}", receipt_id) else None

    def _observe(self, response: Any) -> None:
        super()._observe(response)
        parsed = urlsplit(response.url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if (origin, parsed.path) in {(self.origin, LIST_PATH), (self.api_origin, DETAIL_PATH)}:
            if len(self.read_responses) == self.read_responses.maxlen:
                self.observer_overflow = True
            self.read_responses.append(response)
        if origin == self.api_origin and parsed.path == PUBLISH_PATH and response.request.method == "POST" and self.submitted:
            if len(self.receipt_responses) == self.receipt_responses.maxlen:
                self.receipt_failed = True
                self.observed_receipt = None
                return
            self.receipt_responses.append(response)
            if self.receipt_task is None or self.receipt_task.done():
                self.receipt_task = asyncio.create_task(self._capture_receipts())

    async def _capture_receipts(self) -> None:
        while self.receipt_responses:
            await self._capture_receipt(self.receipt_responses.popleft())

    async def _capture_receipt(self, response: Any) -> None:
        try:
            if response.status in {401, 403, 461, 471}:
                self.receipt_auth_required = True
                return
            payload = await response.json()
            containers = [("",payload)] + ([("data.",payload["data"])] if isinstance(payload, dict) and isinstance(payload.get("data"),dict) else [])
            self.receipt_observation = {"http_status":response.status,
                "envelope":"result" if isinstance(payload,dict) and isinstance(payload.get("result"),dict) else "top",
                "id_fields":[prefix+key for prefix, container in containers if isinstance(container,dict)
                             for key in ("id","noteId","note_id") if key in container]}
            # Persist only the semantic fields needed to re-check the actual
            # envelope. No message text, signed URLs or arbitrary body is kept.
            semantic = {}
            for prefix, container in [("",payload)] + [(key+".",payload.get(key)) for key in ("data","result") if isinstance(payload,dict)]:
                if not isinstance(container,dict):
                    continue
                for key in ("code","success","result","id","noteId","note_id"):
                    value = container.get(key)
                    if type(value) is bool or (type(value) is int and -(2**31) <= value < 2**31):
                        if key in {"code","success","result"}:
                            semantic[prefix+key] = value
                    elif key in {"id","noteId","note_id"} and isinstance(value,str) and re.fullmatch(r"[a-f0-9]{24}",value):
                        semantic[prefix+key] = value
            self.receipt_observation["receipt_fields"] = semantic
            content_id = self.receipt_content_id(payload) if response.status == 200 else None
            if content_id is not None and not self.receipt_failed:
                if self.observed_receipt is not None and self.observed_receipt["content_id"] != content_id:
                    self.receipt_failed = True
                    self.observed_receipt = None
                    return
                self.observed_receipt = {"content_id": content_id}
                self.receipt_auth_required = False
            else:
                self.receipt_failed = True
                self.observed_receipt = None
        except Exception:
            self.receipt_failed = True
            self.observed_receipt = None

    @staticmethod
    def receipt_content_id(payload: Any) -> str | None:
        """Accept only positive envelopes with one unambiguous server work ID.

        The frontend forwards the unextracted creation response. Envelope and
        ID-field variants are supported conservatively; HTTP 200 alone is not
        acceptance. The live creator envelope uses success=true/result=0;
        code=0 and nested status envelopes remain conservative compatibility.
        """
        if not isinstance(payload, dict):
            return None
        result = payload["result"] if isinstance(payload.get("result"),dict) else payload
        statuses = [result[key] for key in ("code","result") if key in result]
        if (result.get("success") is not True or not statuses
                or any(type(value) is not int or value != 0 for value in statuses)):
            return None
        data = payload.get("data")
        containers = [payload] + ([data] if isinstance(data, dict) else [])
        ids = [container[key] for container in containers for key in ("id", "noteId", "note_id") if key in container]
        if not ids or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{24}", value) for value in ids):
            return None
        return ids[0] if len(set(ids)) == 1 else None

    async def _drain_reads(self) -> None:
        while self.read_responses:
            response = self.read_responses.popleft()
            if response.status in {401, 403, 461, 471}:
                raise TaskError("AUTH_REQUIRED", "Work verification requires account authentication")
            payload = await response.json()
            if (response.status != 200 or response.request.method != "GET" or not isinstance(payload, dict)
                    or payload.get("success") is not True or payload.get("code") != 0
                    or not isinstance(payload.get("data"), dict)):
                raise TaskError("RECONCILIATION_UNAVAILABLE", "Work verification returned no positive evidence")
            data = payload["data"]
            parsed = urlsplit(response.url)
            if parsed.path == LIST_PATH:
                rows = data.get("notes")
                if not isinstance(rows, list) or len(rows) > 100:
                    raise TaskError("UI_CHANGED", "Work listing is outside the verified scope")
                self.listed = rows
                query = parse_qs(parsed.query)
                tags = data.get("tags")
                self.listing_complete = (
                    query.get("tab") == ["0"] and query.get("page") == ["0"]
                    and type(data.get("page")) is int and data["page"] == -1
                    and isinstance(tags, list) and len(tags) > 0 and isinstance(tags[0], dict)
                    and tags[0].get("id") == "special.note_time_desc" and tags[0].get("checked") is True
                    and type(tags[0].get("notes_count")) is int and tags[0]["notes_count"] == len(rows)
                    and all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in rows)
                    and all(row.get("type") in {"video","normal"} for row in rows)
                    and len({row["id"] for row in rows}) == len(rows)
                )
            else:
                requested = parse_qs(parsed.query).get("note_id", [])
                if len(requested) != 1 or data.get("id") != requested[0]:
                    raise TaskError("RECONCILIATION_UNAVAILABLE", "Work detail does not identify the requested work")
                self.details[requested[0]] = data

    async def check_challenge(self, page: Any) -> None:
        await super().check_challenge(page)
        if self.receipt_auth_required:
            raise TaskError("AUTH_REQUIRED", "The submission requires account authentication")
        await self._drain_reads()

    def require_fresh_identity(self) -> None:
        self.identity = self.identity_digest = None
        self.identity_refresh_required = True
        self.receipt_auth_required = False

    async def identify_account(self, page: Any) -> str:
        if self.identity_refresh_required:
            response = await page.context.request.get(self.origin + "/api/galaxy/user/info", timeout=0)
            payload = await response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            uid = data.get("userId") if isinstance(data, dict) else None
            if (response.status != 200 or not isinstance(payload, dict) or payload.get("success") is not True
                    or payload.get("code") != 0 or not isinstance(uid, str) or not re.fullmatch(r"[a-f0-9]{24}", uid)):
                raise TaskError("AUTH_REQUIRED", "Fresh account identity is unavailable")
            self.identity, self.identity_digest = uid, await self._cookie_digest()
            self.identity_refresh_required = False
        return await super().identify_account(page)

    async def navigate(self, page: Any, content_type: str, *, readonly: bool) -> None:
        if content_type != "video":
            raise TaskError("UNSUPPORTED", "Only video delivery is available", field="content_type")
        if self.page is not None:
            self.page.remove_listener("response", self._observe)
        self.page = page
        self.identity = self.identity_digest = None
        self.pending.clear()
        self.read_responses.clear()
        self.listed = None
        self.listing_complete = False
        self.details.clear()
        page.on("response", self._observe)
        if readonly:
            async def block_writes(route: Any) -> None:
                parsed = urlsplit(route.request.url)
                if (f"{parsed.scheme}://{parsed.netloc}" in {self.origin,self.api_origin}
                        and route.request.method not in {"GET","HEAD","OPTIONS"}):
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", block_writes)
        await page.goto(self.works_url if readonly else self.edit_url, wait_until="domcontentloaded", timeout=0)

    async def arm_submission(self, page: Any) -> None:
        await self.check_challenge(page)
        await self._settings(page)
        button = page.get_by_role("button", name="发布", exact=True)
        if await button.count() != 1 or not await button.is_enabled():
            raise TaskError("UI_CHANGED", "The final publication control is unavailable", field="submit")
        self.submit_button = button

    async def submit_once(self, page: Any) -> dict:
        if self.submitted or self.submit_button is None:
            raise TaskError("INVALID_STATE", "Final publication was already attempted or was not armed")
        self.submitted = True
        await self.submit_button.click(timeout=0)
        return await self.wait_for_receipt(page)

    async def wait_for_receipt(self, page: Any) -> dict:
        if not self.submitted:
            raise TaskError("INVALID_STATE", "No publication is awaiting a receipt")
        while True:
            await self.check_challenge(page)
            if self.receipt_failed:
                raise TaskError("SUBMISSION_UNCERTAIN", "No unique positive publication receipt was observed")
            if self.observed_receipt is not None:
                return self.observed_receipt
            await asyncio.sleep(.1)

    async def list_works(self, page: Any, content_type: str) -> list[dict]:
        target = self.reconciliation_target
        while self.listed is None:
            await self.check_challenge(page)
            await asyncio.sleep(.1)
        if target is not None:
            rows = [row for row in self.listed if isinstance(row, dict) and row.get("id") == target and row.get("type") == "video"]
            if len(rows) != 1:
                return []
        else:
            if not self.listing_complete:
                return []
            rows = [row for row in self.listed if row.get("type") == "video"]
            if len(rows) > 10:
                raise TaskError("RECONCILIATION_UNAVAILABLE", "Complete listing exceeds the bounded detail scope")
        observations = []
        for row in rows:
            content_id = row.get("id")
            if not isinstance(content_id, str) or not re.fullmatch(r"[a-f0-9]{24}", content_id):
                raise TaskError("RECONCILIATION_UNAVAILABLE", "Work listing contains an invalid identity")
            await page.goto(self.origin + "/publish/update?id=" + content_id + "&noteType=video", wait_until="domcontentloaded", timeout=0)
            # The real application issues its own authenticated GET detail.
            # Recovery blocks write endpoints and never fills or clicks controls.
            while content_id not in self.details:
                await self.check_challenge(page)
                await asyncio.sleep(.1)
            observation = self.work_observation(row, self.details[content_id])
            if observation is None:
                # Without a receipt, every candidate must be inspectable; an
                # unreadable work could otherwise hide a second matching post.
                if target is None:
                    return []
            else:
                observation["listing_complete"] = self.listing_complete
                observations.append(observation)
        return observations

    @staticmethod
    def work_observation(row: dict, detail: dict) -> dict | None:
        user, privacy, video = detail.get("user"), detail.get("privacy"), detail.get("video_info")
        if not isinstance(user, dict) or not isinstance(privacy, dict) or not isinstance(video, dict):
            return None
        created, title, body = detail.get("post_time"), detail.get("title"), detail.get("desc")
        uid, content_id, file_id = user.get("userid"), detail.get("id"), video.get("file_id")
        media = video.get("media_v2")
        remote = media.get("video") if isinstance(media, dict) else None
        md5 = remote.get("md5") if isinstance(remote, dict) else None
        if (row.get("id") != content_id or row.get("type") != "video" or detail.get("type") != "video"
                or not isinstance(content_id, str) or not re.fullmatch(r"[a-f0-9]{24}", content_id)
                or not isinstance(uid, str) or not re.fullmatch(r"[a-f0-9]{24}", uid)
                or type(created) is not int or not isinstance(title, str) or not isinstance(body, str)
                or detail.get("hash_tag") != [] or detail.get("ats") != [] or detail.get("post_timing") is not False
                or type(privacy.get("type")) is not int or privacy["type"] != 0
                or type(row.get("schedule_post_time")) is not int or row["schedule_post_time"] != 0
                or type(row.get("permission_code")) is not int or row["permission_code"] != 0
                or not isinstance(file_id, str) or not re.fullmatch(r"spectrum/[a-zA-Z0-9_-]{1,128}", file_id)
                or not isinstance(md5, str) or not re.fullmatch(r"[a-f0-9]{32}", md5)
                or type(row.get("tab_status")) is not int):
            return None
        result = {1: "PUBLISHED", 2: "IN_REVIEW", 3: "REJECTED"}.get(row["tab_status"])
        if result is None or (result != "REJECTED" and detail.get("enabled") is not True):
            return None
        try:
            created_at = datetime.fromtimestamp(created, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
        return {"platform": "xiaohongshu", "content_type": "video", "content_id": content_id,
                "account_id": uid, "created_at": created_at, "platform_result": result,
                "source": "work_detail", "evidence_scope": "xiaohongshu_video_v1",
                "metadata": {"title": title, "body": body, "tags": []}, "remote_video_id": file_id,
                "remote_media_md5": md5}

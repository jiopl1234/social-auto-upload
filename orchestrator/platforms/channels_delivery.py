"""Single-request Channels publication and independent read-only work lookup."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import re
import time
from typing import Any
from urllib.parse import urlsplit

from orchestrator.errors import TaskError
from orchestrator.platforms.channels import (
    ACCOUNT_PATTERN, API, CREATE_PATH, DETAIL_PATH, IDENTITY_PATH, LIST_PATH,
    MICRO_API, ORIGIN, ChannelsVideoPreparationPort, media_identity, positive_data,
)
from orchestrator.platforms.dom import _consume_observation


CONTENT_PATTERN = r"[A-Za-z0-9_+=/-]{1,256}"


class ChannelsVideoPort(ChannelsVideoPreparationPort):
    verification_scope = "channels_video_v1"
    capability_version = "1.2.0"

    def __init__(self):
        super().__init__()
        self.observed_receipt: dict | None = None
        self.receipt_failed = False
        self.receipt_auth_required = False
        self.acknowledged = False
        self.receipt_observation: dict | None = None
        self.read_responses: deque[Any] = deque(maxlen=32)
        self.listed: list | None = None
        self.reconciliation_target: str | None = None
        self.allow_receiptless_lookup = False
        self.listing_complete = False
        self.list_owner_verified = False
        self.list_request: dict | None = None
        self.list_data: dict | None = None

    async def ensure_reconciliation_evidence(self, context: Any) -> None:
        job = context.store.get(context.job_id)
        replies = [item for item in job['evidence'] if item.get('kind') == 'submission_response'
                   and item.get('attempt_id') == context.attempt_id]
        self.allow_receiptless_lookup = len(replies) == 1 and self.positive_acknowledgement(replies[0])

    @staticmethod
    def positive_acknowledgement(observation: Any) -> bool:
        if (not isinstance(observation, dict) or type(observation.get('http_status')) is not int
                or observation['http_status'] not in {200, 201}):
            return False
        fields = observation.get('receipt_fields')
        return (isinstance(fields, dict) and type(fields.get('errCode')) is int and fields['errCode'] == 0
                and type(fields.get('data.baseResp.errcode')) is int and fields['data.baseResp.errcode'] == 0)

    def set_reconciliation_target(self, content_id: str | None) -> None:
        self.reconciliation_target = content_id

    @staticmethod
    def receipt_content_id(payload: Any) -> str | None:
        data = positive_data(payload)
        if data is None:
            return None
        value = data.get("objectId")
        return value if isinstance(value, str) and re.fullmatch(CONTENT_PATTERN, value) else None

    def _observe(self, response: Any) -> None:
        super()._observe(response)
        parsed = urlsplit(response.url)
        if f"{parsed.scheme}://{parsed.netloc}" != self.origin:
            return
        if parsed.path == CREATE_PATH and response.request.method == "POST" and self.submitted:
            if self.receipt_task is not None:
                self.receipt_failed = True
                return
            self.receipt_task = asyncio.create_task(self._capture_receipt(response))
            self.receipt_task.add_done_callback(_consume_observation)
        elif parsed.path == LIST_PATH and self.readonly:
            if len(self.read_responses) == self.read_responses.maxlen:
                self.observer_overflow = True
                return
            task = asyncio.create_task(self._cache_response(response))
            task.add_done_callback(_consume_observation)
            if self.budget is not None:
                self.budget.track(task)
            self.read_responses.append(task)

    async def _capture_receipt(self, response: Any) -> None:
        try:
            payload = await response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            base = data.get("baseResp") if isinstance(data, dict) else None
            fields = {}
            for key, value in (("errCode", payload.get("errCode") if isinstance(payload, dict) else None),
                               ("data.baseResp.errcode", base.get("errcode") if isinstance(base, dict) else None),
                               ("data.objectId", data.get("objectId") if isinstance(data, dict) else None)):
                if key.endswith("objectId"):
                    if isinstance(value, str) and re.fullmatch(CONTENT_PATTERN, value):
                        fields[key] = value
                elif type(value) is int and -(2**31) <= value < 2**31:
                    fields[key] = value
            self.receipt_observation = {"http_status": response.status, "receipt_fields": fields}
            if (response.status in {401, 403} or isinstance(base, dict) and base.get("errcode") == -11233):
                self.receipt_auth_required = True
                return
            content_id = self.receipt_content_id(payload) if response.status in {200, 201} else None
            self.acknowledged = self.positive_acknowledgement(self.receipt_observation)
            if content_id is None and not self.acknowledged:
                self.receipt_failed = True
            elif content_id is not None:
                self.observed_receipt = {"content_id": content_id}
        except Exception:
            self.receipt_failed = True

    async def check_challenge(self, page: Any) -> None:
        await super().check_challenge(page)
        if self.receipt_auth_required:
            raise TaskError("AUTH_REQUIRED", "Administrator verification is required", field="verification")
        while self.read_responses:
            response = await self.read_responses.popleft()
            data = positive_data(await response.json()) if response.status in {200, 201} else None
            if data is None:
                raise TaskError("RECONCILIATION_UNAVAILABLE", "Work listing returned no positive evidence")
            rows = data.get("list")
            if not isinstance(rows, list) or len(rows) > 100:
                raise TaskError("RECONCILIATION_UNAVAILABLE", "Work listing exceeds the verified scope")
            self.listed = rows
            request = response.request.post_data_json
            self.list_request = request if isinstance(request, dict) else None
            self.list_data = data
            self._validate_listing()

    def _validate_listing(self) -> None:
        data, request, rows = self.list_data, self.list_request, self.listed
        if data is None or request is None or rows is None:
            self.listing_complete = self.list_owner_verified = False
            return
        permitted = {'pageSize', 'currentPage', 'userpageType', 'stickyOrder', 'timestamp',
                     '_log_finder_uin', '_log_finder_id', 'rawKeyBuff', 'pluginSessionId', 'scene', 'reqScene'}
        ids = [row.get('objectId') for row in rows if isinstance(row, dict)]
        self.list_owner_verified = (request.get('_log_finder_id') == self.identity
            and type(request.get('userpageType')) is int and request['userpageType'] == 11)
        self.listing_complete = (not set(request) - permitted
            and type(request.get('currentPage')) is int and request['currentPage'] == 1
            and type(request.get('userpageType')) is int and request['userpageType'] == 11
            and type(request.get('pageSize')) is int and 1 <= request['pageSize'] <= 100
            and request.get('_log_finder_id') == self.identity
            and type(data.get('totalCount')) is int and data['totalCount'] == len(rows)
            and data.get('continueFlag') is False and len(ids) == len(rows)
            and all(isinstance(value, str) and re.fullmatch(CONTENT_PATTERN, value) for value in ids)
            and len(set(ids)) == len(ids))

    async def navigate(self, page: Any, content_type: str, *, readonly: bool) -> None:
        if content_type != "video":
            raise TaskError("UNSUPPORTED", "Only video delivery is available", field="content_type")
        while self.read_responses:
            task = self.read_responses.popleft()
            if not task.done():
                task.cancel()
        self.listed = None
        self.listing_complete = False
        self.list_owner_verified = False
        self.list_request = self.list_data = None
        await self._navigate(page, readonly=readonly)

    async def arm_submission(self, page: Any) -> None:
        await self.check_challenge(page)
        await self._settings(page)
        button = page.get_by_role("button", name="发表", exact=True)
        if (await button.count() != 1 or not await button.is_enabled()
                or "weui-desktop-btn_disabled" in (await button.get_attribute("class") or "")):
            raise TaskError("UI_CHANGED", "The final publication control is unavailable", field="submit")
        self.submit_button = button

    async def submit_once(self, page: Any) -> dict | None:
        if self.submitted or self.submit_button is None:
            raise TaskError("INVALID_STATE", "Final publication was already attempted or was not armed")
        self.submitted = True
        await self.submit_button.click(timeout=0)
        return await self.wait_for_receipt(page)

    async def wait_for_receipt(self, page: Any) -> dict | None:
        if not self.submitted:
            raise TaskError("INVALID_STATE", "No publication is awaiting a receipt")
        while True:
            await self.check_challenge(page)
            if self.receipt_failed:
                raise TaskError("SUBMISSION_UNCERTAIN", "A unique positive publication receipt was not observed")
            if self.observed_receipt is not None:
                return self.observed_receipt
            if self.acknowledged:
                # The observed live reply positively acknowledges creation but
                # contains no work ID. Do not invent one from a later listing.
                return None
            await asyncio.sleep(.1)

    async def list_works(self, page: Any, content_type: str) -> list[dict]:
        target = self.reconciliation_target
        if target is not None and not re.fullmatch(CONTENT_PATTERN, target):
            return []
        if target is None and not self.allow_receiptless_lookup:
            return []
        while self.listed is None:
            await self.check_challenge(page)
            await asyncio.sleep(.1)
        # The list can arrive before auth_data. Bind the retained read envelope
        # only after the adapter's independent stable-identity guard succeeds.
        self._validate_listing()
        if target is None and (not self.listing_complete or len(self.listed) > 10):
            return []
        rows = [row for row in self.listed if isinstance(row, dict) and (target is None or row.get('objectId') == target)]
        if (target is not None and len(rows) != 1) or self.identity_request is None or not self.list_owner_verified:
            return []
        observations = []
        for row in rows:
            observation = await self._work_detail(page, row)
            # With no server ID, every row must have readable independent
            # details before uniqueness can be established.
            if observation is None:
                if target is None:
                    raise TaskError('RECONCILIATION_UNAVAILABLE', 'Not every listed work has verifiable details')
                return []
            observation['listing_complete'] = self.listing_complete
            observations.append(observation)
        return observations

    async def _work_detail(self, page: Any, row: dict) -> dict | None:
        target = row.get('objectId')
        if not isinstance(target, str) or not re.fullmatch(CONTENT_PATTERN, target) or self.identity_request is None:
            return None
        # Replay only the observed authenticated read envelope. Values remain
        # in memory; cookies, session parameters and signed URLs are not saved.
        payload = {key: value for key, value in self.identity_request.items()
                   if key in {"_log_finder_uin", "_log_finder_id", "rawKeyBuff", "pluginSessionId", "scene", "reqScene"}}
        payload.update({"timestamp": str(int(time.time() * 1000)), "objectId": target})
        response = await page.evaluate("""async ({url,payload})=>{
            const r=await fetch(url,{method:'POST',credentials:'same-origin',
                headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
            const text=await r.text();if(text.length>1048576)return {status:r.status,payload:null};
            return {status:r.status,payload:JSON.parse(text)};
        }""", {"url": self.origin + DETAIL_PATH, "payload": payload})
        data = positive_data(response.get("payload")) if isinstance(response, dict) and response.get("status") in {200, 201} else None
        if data is None or not isinstance(data.get("object"), dict):
            raise TaskError("RECONCILIATION_UNAVAILABLE", "Independent work detail returned no positive evidence")
        return self.work_observation(row, data['object'], account_id=self.identity)

    @staticmethod
    def work_observation(row: dict, detail: dict, *, account_id: str | None = None) -> dict | None:
        desc = detail.get("desc")
        if not isinstance(desc, dict):
            return None
        media = desc.get("media")
        title = desc.get("shortTitle")
        if (not isinstance(media, list) or len(media) != 1 or not isinstance(media[0], dict)
                or not isinstance(title, list) or len(title) != 1 or not isinstance(title[0], dict)):
            return None
        content_id = detail.get("objectId")
        uid = detail.get('finderUsername', account_id)
        created = detail.get("createTime")
        video_id = media_identity(media[0].get("url"))
        md5 = media[0].get("md5sum")
        if (not isinstance(content_id, str) or not re.fullmatch(CONTENT_PATTERN, content_id)
                or row.get("objectId") != content_id
                or not isinstance(uid, str) or not re.fullmatch(ACCOUNT_PATTERN, uid)
                or type(created) is not int or type(desc.get("mediaType")) is not int or desc["mediaType"] != 4
                or not isinstance(title[0].get("shortTitle"), str) or not isinstance(desc.get("description"), str)
                or (account_id is not None and uid != account_id)
                or desc.get('mentionedUser', []) != [] or desc.get('mentionedMusics', []) != []
                or desc.get('location') != {} or desc.get('feedLocation', {}) != {}
                or desc.get('extReading', {}) != {} or desc.get('topic', {}) != {} or desc.get('member', {}) != {}
                or desc.get('event') not in ({}, {'eventTopicId':'','eventName':'','eventCreatorNickname':'','eventAttendCount':0})
                or detail.get('topics', []) != [] or detail.get("effectiveTime", 0) not in (0, None)
                or video_id is None or not isinstance(md5, str) or not re.fullmatch(r"[a-f0-9]{32}", md5)):
            return None
        # The observed frontend exports public=1, normal=1 and handle states
        # running=1/success=2/fail=3. A normal row alone does not mean published.
        status = row.get("status")
        handling = row.get("handleStatus")
        if (type(status) is not int or status != 1 or type(row.get("visibleType")) is not int or row["visibleType"] != 1
                or type(detail.get('status')) is not int or detail['status'] != status
                or type(detail.get('visibleType')) is not int or detail['visibleType'] != row['visibleType']):
            return None
        result = {0: 'ACCEPTED', 1: 'ACCEPTED', 2: 'ACCEPTED', 3: 'REJECTED', 4: 'REJECTED', 5: 'REJECTED'}.get(handling) if type(handling) is int else 'ACCEPTED' if 'handleStatus' not in row else None
        disabled = row.get('disableInfo', {})
        if isinstance(disabled, dict) and (disabled.get('disableAddWording') == '审核中'
                or disabled.get('disableShowWording') == '视频审核中'):
            result = 'IN_REVIEW'
        if result is None:
            return None
        try:
            created_at = datetime.fromtimestamp(created, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
        return {"platform": "tencent", "content_type": "video", "content_id": content_id,
                "account_id": uid, "created_at": created_at, "platform_result": result,
                "source": "work_detail", "evidence_scope": "channels_video_v1",
                "account_evidence": 'work_detail' if 'finderUsername' in detail else 'authenticated_creator_list',
                "metadata": {"title": title[0]["shortTitle"], "body": desc["description"], "tags": []},
                "remote_video_id": video_id, "remote_media_md5": md5}

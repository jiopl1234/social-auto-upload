"""Fail-closed editing with an explicit, independently implemented evidence reader."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode

from orchestrator.errors import TaskError


class PageEvidence(Protocol):
    verification_scope: str
    async def identify_account(self, page: Any) -> str: ...
    async def media(self, page: Any) -> list[dict]: ...
    async def cover(self, page: Any) -> dict | None: ...
    async def declarations(self, page: Any) -> dict: ...
    async def receipt(self, page: Any) -> dict | None: ...
    async def list_works(self, page: Any, content_type: str) -> list[dict]: ...


@dataclass(frozen=True)
class EditorSelectors:
    upload: str
    title: str
    body: str
    submit: str
    tag_search: str | None = None
    tag_candidates: str | None = None
    selected_tags: str | None = None
    cover_input: str | None = None
    collection: str | None = None
    declarations: Mapping[str, str] = field(default_factory=dict)
    challenge: str = 'input[placeholder*="验证码"]:visible, [data-challenge]:visible'
    unknown_dialog: str = '[role="dialog"]:visible'


class DomPagePort:
    capability_version = "1.0.0"
    def __init__(self, platform: str, selectors: EditorSelectors, *, edit_url: str, works_url: str,
                 evidence: PageEvidence | None = None, note_selectors: EditorSelectors | None = None,
                 note_edit_url: str | None = None):
        self.platform, self.selectors = platform, selectors
        self.video_selectors, self.note_selectors = selectors, note_selectors
        self.content_types = frozenset({"video", "note"} if note_selectors is not None else {"video"})
        self.note_edit_url = note_edit_url
        self.edit_url, self.works_url, self.evidence = edit_url, works_url, evidence
        self.verification_scope = evidence.verification_scope if evidence is not None else "unverified"
        self.submitted = False
        self.submit_button: Any = None

    def _evidence(self) -> PageEvidence:
        if self.evidence is None:
            raise TaskError("UNSUPPORTED", "Platform evidence is not verified", field="platform")
        return self.evidence

    async def navigate(self, page: Any, content_type: str, *, readonly: bool) -> None:
        if content_type not in self.content_types:
            raise TaskError("UNSUPPORTED", "Content path is not supported", field="content_type")
        if content_type == "note":
            assert self.note_selectors is not None
            self.selectors = self.note_selectors
        else:
            self.selectors = self.video_selectors
        editor_url = (self.note_edit_url or self.edit_url) if content_type == "note" else self.edit_url
        url = self.works_url if readonly else editor_url
        if self.verification_scope == "local_fixture":
            url += ("&" if "?" in url else "?") + urlencode({"kind": content_type, "view": "works" if readonly else "editor"})
        await page.goto(url, wait_until="domcontentloaded", timeout=0)

    async def identify_account(self, page: Any) -> str:
        return await self._evidence().identify_account(page)

    async def check_challenge(self, page: Any) -> None:
        if page.is_closed():
            raise TaskError("PAGE_CLOSED", "Browser page was closed")
        if await page.locator(self.selectors.challenge).count():
            raise TaskError("AUTH_REQUIRED", "Account verification or login is required")
        for text in ("实名验证", "短信验证", "扫码登录", "安全验证"):
            candidate = page.get_by_text(text, exact=True)
            if await candidate.count() and await candidate.first.is_visible():
                raise TaskError("AUTH_REQUIRED", "Account verification or login is required")
        if await page.locator(self.selectors.unknown_dialog).count():
            raise TaskError("UI_CHANGED", "An unrecognized dialog blocks editing")

    async def _control(self, page: Any, selector: str | None, name: str, *, visible: bool = True) -> Any:
        await self.check_challenge(page)
        if selector is None:
            raise TaskError("UNSUPPORTED", "Required page control is not mapped", field=name)
        control = page.locator(selector)
        if await control.count() != 1 or (visible and not await control.is_visible()):
            raise TaskError("UI_CHANGED", "Required page control is missing or ambiguous", field=name)
        return control

    async def upload(self, page: Any, manifest: Mapping[str, Any]) -> None:
        control = await self._control(page, self.selectors.upload, "media", visible=False)
        await control.set_input_files([item["path"] for item in manifest["media"]], timeout=0)

    async def wait_media(self, page: Any, manifest: Mapping[str, Any]) -> None:
        while True:
            await self.check_challenge(page)
            items = await self._evidence().media(page)
            if any(item.get("status") == "failed" for item in items):
                raise TaskError("UPLOAD_FAILED", "One or more media files failed processing", field="media")
            if len(items) == len(manifest["media"]) and all(item.get("status") == "ready" for item in items):
                for actual, expected in zip(items, manifest["media"]):
                    if (actual.get("name") != Path(expected["path"]).name or actual.get("sha256") != expected["sha256"]
                            or actual.get("kind") != expected["kind"]):
                        raise TaskError("METADATA_MISMATCH", "Processed media differs from the selected files", field="media")
                return
            # Bounded by the caller's cumulative processing budget.
            await asyncio.sleep(0.05)

    async def _fill(self, page: Any, selector: str, name: str, value: str) -> None:
        control = await self._control(page, selector, name)
        maximum = await control.get_attribute("maxlength")
        if maximum is not None and maximum.isdigit() and len(value) > int(maximum):
            raise TaskError("INVALID_INPUT", "Content exceeds the observed page limit", field=name)
        await control.fill(value, timeout=0)

    async def apply_fields(self, page: Any, manifest: Mapping[str, Any]) -> None:
        selectors = self.selectors
        await self._fill(page, selectors.title, "title", manifest["title"])
        await self._fill(page, selectors.body, "body", manifest["body"])
        omitted = set(manifest["field_policy"]["allow_omit"])
        if manifest["tags"]:
            if selectors.tag_search is None and "tags" in omitted:
                pass
            else:
                search = await self._control(page, selectors.tag_search, "tags")
                for tag in manifest["tags"]:
                    await self.check_challenge(page)
                    await search.fill(tag, timeout=0)
                    candidates = page.locator(selectors.tag_candidates or "[data-unmapped-tag]")
                    exact = [item for item in await candidates.all() if await item.inner_text() == tag and await item.is_visible()]
                    if len(exact) != 1:
                        await search.fill("", timeout=0)
                        raise TaskError("METADATA_MISMATCH", "An exact topic candidate was not found", field="tags")
                    await exact[0].click(timeout=0)
        if manifest.get("cover") is not None and not (selectors.cover_input is None and "cover" in omitted):
            cover = await self._control(page, selectors.cover_input, "cover", visible=False)
            await cover.set_input_files(manifest["cover"]["path"], timeout=0)
            while await self._evidence().cover(page) is None:
                await self.check_challenge(page)
                await asyncio.sleep(0.05)
        if manifest.get("collection") is not None and not (selectors.collection is None and "collection" in omitted):
            collection = await self._control(page, selectors.collection, "collection")
            await collection.select_option(label=manifest["collection"], timeout=0)
        for name, selector in selectors.declarations.items():
            control = page.locator(selector)
            if await control.count() == 1 and await control.get_attribute("required") is not None and name not in manifest["declarations"]:
                raise TaskError("INVALID_INPUT", "A required declaration is missing", field=f"declarations.{name}")
        for name, value in manifest["declarations"].items():
            control = await self._control(page, selectors.declarations.get(name), f"declarations.{name}")
            encoded = "true" if value is True else "false" if value is False else "null" if value is None else str(value)
            await control.select_option(value=encoded, timeout=0)

    async def readback(self, page: Any) -> dict:
        selectors = self.selectors
        title = await self._control(page, selectors.title, "title")
        body = await self._control(page, selectors.body, "body")
        media = await self._evidence().media(page)
        if any(item.get("status") != "ready" for item in media):
            raise TaskError("UPLOAD_FAILED", "Media is no longer ready", field="media")
        tags = await page.locator(selectors.selected_tags).all_inner_texts() if selectors.selected_tags else []
        collection = (await (await self._control(page, selectors.collection, "collection")).input_value()
                      if selectors.collection else None)
        return {"title": await title.input_value(), "body": await body.inner_text(), "tags": tags,
                "media": [{"kind": item["kind"], "sha256": item["sha256"]} for item in media],
                "cover": await self._evidence().cover(page), "collection": collection or None,
                "declarations": await self._evidence().declarations(page)}

    async def arm_submission(self, page: Any) -> None:
        self.submit_button = await self._control(page, self.selectors.submit, "submit")
        if not await self.submit_button.is_enabled():
            raise TaskError("UI_CHANGED", "Final action is not enabled", field="submit")

    async def submit_once(self, page: Any) -> dict | None:
        if self.submitted or self.submit_button is None:
            raise TaskError("INVALID_STATE", "Final action was already attempted")
        self.submitted = True
        await self.submit_button.click(timeout=0)
        return await self._evidence().receipt(page)

    async def list_works(self, page: Any, content_type: str) -> list[dict]:
        return await self._evidence().list_works(page, content_type)

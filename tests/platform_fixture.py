"""Synthetic creator UI and independent loopback work store; no platform requests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

from orchestrator.browser import BrowserSession
from tests.test_browser_lifecycle import _installed_chromium_executable


class CreatorSite:
    def __init__(self, platform: str, *, mode: str = "normal", result: str = "PUBLISHED"):
        self.platform, self.mode, self.result = platform, mode, result
        self.submissions: list[dict] = []
        self.works: list[dict] = []
        self.queries = 0
        self.query_kinds: list[str] = []
        self.hide_works = False
        self.response_gate = threading.Event()
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def send_json(self, data):
                payload = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_GET(self):
                if urlparse(self.path).path == "/works":
                    site.queries += 1
                    kind = parse_qs(urlparse(self.path).query).get("kind", [""])[0]
                    site.query_kinds.append(kind)
                    return self.send_json([] if site.hide_works else [work for work in site.works if work["content_type"] == kind])
                query = parse_qs(urlparse(self.path).query)
                config = {"platform": site.platform, "mode": site.mode,
                          "kind": query.get("kind", ["video"])[0],
                          "readonly": query.get("view") == ["works"]}
                html = (Path(__file__).parent / "fixtures" / "creator_contract.html").read_text(encoding="utf-8")
                payload = html.replace("__FIXTURE_CONFIG__", json.dumps(config)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                if self.path != "/publish":
                    self.send_error(404)
                    return
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                site.submissions.append(data)
                work = {"content_id": str(len(site.submissions)), "platform": site.platform,
                        "content_type": data["content_type"], "account_id": data["account_id"],
                        "created_at": datetime.now(timezone.utc).isoformat(), "platform_result": site.result,
                        "source": "work_detail", "metadata": data["metadata"],
                        "media_sha256": [item["sha256"] for item in data["metadata"]["media"]]}
                if site.mode != "false-success":
                    site.works.append(work)
                if site.mode == "ambiguous":
                    site.works.append({**work, "content_id": "duplicate"})
                if site.mode in {"lost-response", "ambiguous"}:
                    self.close_connection = True
                    self.connection.close()
                    return
                if site.mode == "delayed-response":
                    site.response_gate.wait(timeout=5)
                self.send_json({"content_id": work["content_id"]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}/editor"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.response_gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class FixtureEvidence:
    """These selectors describe our synthetic UI, never a real creator site."""
    verification_scope = "local_fixture"

    async def identify_account(self, page):
        return await page.locator("#identity").get_attribute("data-account-id")

    async def media(self, page):
        return await page.locator("#previews > article").evaluate_all(
            "els => els.map(e => ({name:e.dataset.name, kind:e.dataset.kind, sha256:e.dataset.sha256, status:e.dataset.status}))")

    async def cover(self, page):
        value = await page.locator("#cover-proof").text_content()
        return json.loads(value) if value else None

    async def declarations(self, page):
        return await page.locator("[data-declaration]").evaluate_all("""els => Object.fromEntries(
            els.filter(e => e.value !== '__unset__').map(e => [e.dataset.declaration,
            e.value === 'true' ? true : e.value === 'false' ? false : e.value === 'null' ? null : e.value]))""")

    async def list_works(self, page, content_type):
        await page.locator("#works-data[data-ready='true']").wait_for(state="attached", timeout=0)
        return json.loads(await page.locator("#works-data").text_content())

    async def receipt(self, page):
        # Observation is bounded by the submit stage; no second click follows.
        await page.locator("#receipt[data-observed='true']").wait_for(state="attached", timeout=0)
        content_id = await page.locator("#receipt").get_attribute("data-content-id")
        return {"content_id": content_id} if content_id else None


class SlowCancelledReceiptEvidence(FixtureEvidence):
    async def receipt(self, page):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            # Fault injection: the external observation ignores the first
            # cancellation long enough to outlive browser cleanup.
            await asyncio.sleep(5)


class FailingCancelledReceiptEvidence(FixtureEvidence):
    async def receipt(self, page):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            raise RuntimeError("private browser diagnostic must stay hidden")


def fixture_adapter(service, site: CreatorSite, *, mutate_after_save: str | None = None,
                    cleanup_error: bool = False):
    import pytest
    from orchestrator.platforms.base import BrowserDeliveryAdapter
    from orchestrator.platforms.profiles import EDITORS, NOTE_EDITORS, create_port

    executable = _installed_chromium_executable()
    if executable is None:
        pytest.skip("No local Chromium browser available")
    class Session(BrowserSession):
        async def save_state(self, path):
            await super().save_state(path)
            if mutate_after_save is not None:
                assert self.page is not None
                await self.page.evaluate(mutate_after_save)

        async def close(self):
            await super().close()
            if cleanup_error:
                from orchestrator.errors import TaskError

                failure = TaskError("CLEANUP_FAILED", "Driver shutdown could not be confirmed")
                self.cleanup_warnings.append(failure.as_dict())
                raise failure
    class ChallengeBeforeReconcileAdapter(BrowserDeliveryAdapter):
        async def submit(self, context):
            receipt = await super().submit(context)
            # The local page can present verification just after a receipt is
            # observed, before the coordinator starts its read-only lookup.
            await self.page.evaluate("""() => {
                const prompt = document.createElement('div');
                prompt.dataset.challenge = 'auth';
                prompt.textContent = '实名验证';
                document.body.append(prompt);
            }""")
            return receipt
    selectors = replace(EDITORS[site.platform],
        tag_search="#tag-search", tag_candidates="#topics button", selected_tags="#tags > span",
        cover_input="#cover", collection="#collection",
        declarations={name: f"#decl-{name}" for name in (
            "ownership", "ai_usage", "repost_source", "originality_rights", "repost_authorized")},
        challenge="[data-challenge]:visible", unknown_dialog="[role='dialog']:visible",
    )
    adapter_type = ChallengeBeforeReconcileAdapter if site.mode == "auth-before-reconcile" else BrowserDeliveryAdapter
    return adapter_type(
        service.runtime_dir,
        lambda: create_port(site.platform, selectors=selectors, edit_url=site.url, works_url=site.url,
                            evidence=(SlowCancelledReceiptEvidence()
                                      if site.mode == "auth-after-submit-no-receipt-slow-cancel"
                                      else FailingCancelledReceiptEvidence()
                                      if site.mode == "auth-after-submit-no-receipt-raises-on-cancel"
                                      else FixtureEvidence()),
                            note_selectors=(replace(selectors, upload=NOTE_EDITORS[site.platform].upload)
                                            if site.platform in NOTE_EDITORS else None)),
        session_factory=lambda context: Session(context, launch_options={
            "headless": True, "executable_path": str(executable),
        }),
    )

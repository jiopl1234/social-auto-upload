"""Synthetic creator UI and independent loopback work store; no platform requests."""
from __future__ import annotations

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
        self.hide_works = False
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
                self.wfile.write(payload)

            def do_GET(self):
                if urlparse(self.path).path == "/works":
                    site.queries += 1
                    return self.send_json([] if site.hide_works else site.works)
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
                self.send_json({"content_id": work["content_id"]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}/editor"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
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


def fixture_adapter(service, site: CreatorSite, *, mutate_after_save: str | None = None):
    import pytest
    from orchestrator.platforms.base import BrowserDeliveryAdapter
    from orchestrator.platforms.profiles import EDITORS, create_port

    executable = _installed_chromium_executable()
    if executable is None:
        pytest.skip("No local Chromium browser available")
    class Session(BrowserSession):
        async def save_state(self, path):
            await super().save_state(path)
            if mutate_after_save is not None:
                assert self.page is not None
                await self.page.evaluate(mutate_after_save)
    selectors = replace(EDITORS[site.platform],
        tag_search="#tag-search", tag_candidates="#topics button", selected_tags="#tags > span",
        cover_input="#cover", collection="#collection",
        declarations={name: f"#decl-{name}" for name in (
            "ownership", "ai_usage", "repost_source", "originality_rights", "repost_authorized")},
        challenge="[data-challenge]:visible", unknown_dialog="[role='dialog']:visible",
    )
    return BrowserDeliveryAdapter(
        service.runtime_dir,
        lambda: create_port(site.platform, selectors=selectors, edit_url=site.url, works_url=site.url,
                            evidence=FixtureEvidence()),
        session_factory=lambda context: Session(context, launch_options={
            "headless": True, "executable_path": str(executable),
        }),
    )

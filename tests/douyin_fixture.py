"""Loopback replay of the observed Douyin UI/protocol, never a real account."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

from orchestrator.browser import BrowserSession
from orchestrator.platforms.base import BrowserDeliveryAdapter
from tests.test_browser_lifecycle import _installed_chromium_executable


HTML = """<!doctype html><meta charset="utf-8"><body>
<input type=file accept="video/mp4" id=upload>
<section id=editor hidden>
<input placeholder="填写作品标题，为作品获得更多流量" maxlength=30>
<div class="zone-container" contenteditable=true></div>
<button id=cover>选择封面</button><span>竖封面3:4</span><span>横封面4:3</span>
<div id=covers></div><div class=semi-select>请选择合集</div>
<div class=semi-select>请选择自主声明</div>
<label data-checked=true><input type=checkbox checked>公开</label>
<label data-checked=true><input type=checkbox checked>立即发布</label>
<input type=checkbox role=switch>
<video></video><button id=publish>发布</button></section>
<script>
const mode = __MODE__;
const api = path => fetch(path).then(r=>r.json());
api('/web/api/media/user/info/');
if(location.pathname.includes('manage')) {
  document.querySelector('#upload').remove();
  api('/janus/douyin/creator/pc/work_list');
} else {
document.querySelector('#upload').onchange=async e=>{
  const file=e.target.files[0];
  const result=await fetch('/?Action=CommitUploadInner', {method:'POST',body:JSON.stringify({size:file.size})}).then(r=>r.json());
  if(mode==='processing') return;
  document.querySelector('#editor').hidden=false;
  const video=document.querySelector('video'); video.src='/tos-cn-v/fixture-video';
  video.preload='auto'; video.load();
};
document.querySelector('#cover').onclick=()=>{
  const modal=document.createElement('div');modal.setAttribute('role','dialog');
  modal.innerHTML='<span>设置竖封面</span><div class="preview-frame-replay">frame</div><button id="landscape">设置横封面</button><button id="finish">完成</button>';
  document.body.append(modal);
  modal.querySelector('#landscape').onclick=()=>{modal.querySelector('span').textContent='设置横封面';};
  modal.querySelector('#finish').onclick=()=>{document.querySelector('#covers').innerHTML='<img alt="竖封面" src="/cover"><img alt="横封面" src="/cover">';modal.remove();if(mode==='replaced-preview')document.querySelector('video').src='/different-video';};
};
document.querySelector('#publish').onclick=async()=>{
  const payload={title:document.querySelector('input[placeholder]').value,body:document.querySelector('[contenteditable]').innerText};
  if(mode==='challenge') {const dialog=document.createElement('article');dialog.className='uc-ui-verify_qr-verify';dialog.textContent='为保障账号安全，请使用抖音 APP扫码验证';document.body.append(dialog);}
  await fetch('/web/api/media/aweme/create_v2/',{method:'POST',body:JSON.stringify(payload)});
};
}
</script>"""


class DouyinSite:
    def __init__(self, mode="normal"):
        self.mode, self.submissions, self.works = mode, [], []
        self.hide_works = False
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def send(self, payload, content_type="application/json"):
                data = (payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                        if content_type == "application/json" else payload.encode())
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/web/api/media/user/info/":
                    return self.send({"status_code": 0, "user": {"uid": "other" if site.mode == "wrong-account" else "douyin:creator"}})
                if path == "/janus/douyin/creator/pc/work_list":
                    return self.send({"status_code": 0, "aweme_list": [] if site.hide_works else site.works})
                if path.startswith("/creator-micro/"):
                    return self.send(HTML.replace("__MODE__", json.dumps(site.mode)), "text/html; charset=utf-8")
                if path == "/tos-cn-v/fixture-video":
                    return self.send((Path(__file__).parent / "fixtures/douyin-preview.webm").read_bytes(), "video/webm")
                self.send_error(404)

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/?Action=CommitUploadInner":
                    return self.send({"Result": {"Results": [{"Vid": "fixture-video", "VideoMeta": {
                        "Uri": "tos-cn-v/fixture-video", "Size": data["size"], "Width": 720, "Height": 1280,
                        "Duration": 9, "FileType": "video"}}]}})
                if self.path != "/web/api/media/aweme/create_v2/":
                    self.send_error(404)
                    return
                site.submissions.append(data)
                site.works.append({"aweme_id": "7690181573743742254", "aweme_type": 4,
                    "author": {"uid": "douyin:creator"}, "create_time": int(datetime.now(timezone.utc).timestamp()),
                    "item_title": data["title"], "desc": data["title"] + " " + data["body"],
                    "text_extra": [], "video": {"vid": "fixture-video", "width": 720, "height": 1280},
                    "status": {"is_delete": False, "is_prohibited": False, "in_reviewing": False,
                               "is_private": False, "private_status": 0, "self_see": False}})
                if site.mode == "wrong-video":
                    site.works[-1]["video"]["vid"] = "another-video"
                if site.mode == "changed-body":
                    site.works[-1]["desc"] = data["title"] + " Different body"
                if site.mode == "unknown-state":
                    site.works[-1]["status"].pop("in_reviewing")
                if site.mode == "duplicate-work":
                    site.works.append(dict(site.works[-1]))
                return self.send({"status_code": 0, **({} if site.mode in {"lost-receipt", "challenge"}
                    else {"item_id": "7690181573743742254"})})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def adapter(self, service):
        from orchestrator.platforms.douyin import DouyinVideoPort

        site = self
        class ReplayPort(DouyinVideoPort):
            verification_scope = "local_fixture"
            origin = site.origin
            upload_origin = site.origin

        return BrowserDeliveryAdapter(service.runtime_dir, ReplayPort,
            session_factory=lambda context: BrowserSession(context, launch_options={
                "headless": True, "executable_path": str(_installed_chromium_executable())}))

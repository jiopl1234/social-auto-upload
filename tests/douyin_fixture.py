"""Loopback replay of the observed Douyin UI/protocol, never a real account."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import struct
import zlib
from typing import Any
from urllib.parse import urlsplit

from orchestrator.browser import BrowserSession
from orchestrator.platforms.base import BrowserDeliveryAdapter
from tests.test_browser_lifecycle import _installed_chromium_executable


def cover_png(width, height):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\x80' * (3 * width)) * height)) + chunk(b'IEND', b''))


VERIFY_SCRIPT = r"""async (refresh = true) => {
  const dialog=document.createElement('article');dialog.className='uc-ui-verify_qr-verify';dialog.textContent='安全验证：请在当前页面完成本人验证';document.body.append(dialog);
  while(!(await fetch('/verification').then(r=>r.json())).approved) await new Promise(r=>setTimeout(r,50));
  dialog.remove();if(refresh) await fetch('/web/api/media/user/info/');
}"""


HTML = r"""<!doctype html><meta charset="utf-8"><body>
<input type=file accept="video/mp4" id=upload>
<section id=editor hidden>
<input placeholder="填写作品标题，为作品获得更多流量" maxlength=30>
<div class="zone-container" contenteditable=true></div>
<button id=cover>选择封面</button>
<div id=portrait><span>竖封面3:4</span></div><div id=landscape><span>横封面4:3</span></div>
<div class=cover-check></div><div class=semi-select>请选择合集</div>
<div class=semi-select>请选择自主声明</div>
<label data-checked=true><input type=checkbox checked>公开</label>
<label data-checked=true><input type=checkbox checked>立即发布</label>
<input type=checkbox role=switch>
<video></video><button id=publish>发布</button></section>
<script>
const mode = __MODE__;
const api = path => fetch(path).then(r=>r.json());
const verifyPerson = __VERIFY__;
api('/web/api/media/user/info/');
if(mode==='rich-editor') {
  const body=document.querySelector('[contenteditable]');
  body.addEventListener('beforeinput', event=>{
    if(event.inputType!=='insertText' || event.data===null) return;
    event.preventDefault(); body.replaceChildren();
    for(const line of event.data.split('\n')) {
      const row=document.createElement('div');row.className='ace-line';row.dataset.node='true';
      const wrapper=document.createElement('div');wrapper.dataset.lineWrapper='true';
      const value=document.createElement('span');value.dataset.string='true';value.textContent=line;
      const sentinel=document.createElement('span');sentinel.dataset.string='true';sentinel.dataset.enter='true';sentinel.textContent='\u200b';
      wrapper.append(value,sentinel);row.append(wrapper);body.append(row);
    }
  });
}
if(location.pathname.includes('manage')) {
  document.querySelector('#upload').remove();
  if(['collection-tip', 'unknown-tip', 'collection-tip-auth', 'collection-tip-retained-auth'].includes(mode)) {
    const tip=document.createElement('div');tip.setAttribute('role','dialog');
    tip.innerHTML='<span>'+(mode==='unknown-tip'?'未识别的提示':'合集管理移到这里啦')+'</span><button>知道了</button>';
    tip.querySelector('button').onclick=()=>{
      if(mode!=='collection-tip-retained-auth') tip.remove();
      if(mode==='collection-tip-auth' || mode==='collection-tip-retained-auth') {
        const challenge=document.createElement('div');challenge.setAttribute('role','dialog');
        challenge.textContent='安全验证';document.body.append(challenge);
      }
    }; document.body.append(tip);
  }
  if(mode==='manual-worklist' || mode==='manual-repeat') verifyPerson().then(()=>api('/janus/douyin/creator/pc/work_list'));
  else api('/janus/douyin/creator/pc/work_list');
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
  modal.innerHTML='<span>设置竖封面</span><div style="position:relative;width:40px;height:32px"><div class="preview-frame-replay" style="width:40px;height:32px">frame</div><div class="slider-preview-replay" style="position:absolute;inset:0"></div></div><button id="landscape">设置横封面</button><button id="finish">完成</button>';
  document.body.append(modal);
  const selected = new Set();
  modal.querySelector('.slider-preview-replay').onclick=()=>selected.add(modal.querySelector('span').textContent);
  modal.querySelector('#landscape').onclick=()=>{modal.querySelector('span').textContent='设置横封面';};
  modal.querySelector('#finish').onclick=async()=>{
    if(mode!=='missing-covers' && selected.size===2) {
      document.querySelector('#portrait').insertAdjacentHTML('beforeend','<img src="/portrait-cover">');
      document.querySelector('#landscape').insertAdjacentHTML('beforeend','<img src="/landscape-cover">');
      if(mode==='pending-covers') {
        const blob=URL.createObjectURL(await fetch('/cover').then(r=>r.blob()));
        for(const img of document.querySelectorAll('#portrait img, #landscape img')) img.src=blob;
      }
      if(mode==='uncropped-covers') {
        for(const img of document.querySelectorAll('#portrait img, #landscape img')) img.src='/uncropped-cover';
      }
    }
    document.querySelector('.cover-check').textContent='封面检测通过';
    modal.remove();
    if(mode==='replaced-preview')document.querySelector('video').src='/different-video';
    if(mode==='preview-tabs') {
      document.querySelector('video').remove();
      const tab=document.createElement('div');tab.textContent='预览视频';
      tab.onclick=()=>{const video=document.createElement('video');video.src='/tos-cn-v/fixture-video';video.preload='auto';document.querySelector('#editor').append(video);video.load();};
      document.querySelector('#editor').append(tab);
    }
  };
};
document.querySelector('#publish').onclick=async()=>{
  const payload={title:document.querySelector('input[placeholder]').value,body:document.querySelector('[contenteditable]').innerText};
  if(mode.startsWith('manual-') && mode!=='manual-worklist') await verifyPerson(mode!=='manual-stale-identity');
  if(mode==='challenge') {const dialog=document.createElement('article');dialog.className='uc-ui-verify_qr-verify';dialog.textContent='为保障账号安全，请使用抖音 APP扫码验证';document.body.append(dialog);}
  await fetch('/web/api/media/aweme/create_v2/',{method:'POST',body:JSON.stringify(payload)});
};
}
</script>"""


class DouyinSite:
    def __init__(self, mode="normal"):
        self.mode, self.submissions, self.works = mode, [], []
        self.hide_works = False
        self.verification_released = threading.Event()
        self.page: Any = None
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
                if path == '/verification':
                    return self.send({'approved': site.verification_released.is_set()})
                if path == "/web/api/media/user/info/":
                    wrong = site.mode == "wrong-account" or (site.mode == "manual-wrong-account" and site.verification_released.is_set())
                    return self.send({"status_code": 0, "user": {"uid": "other" if wrong else "douyin:creator"}})
                if path == "/janus/douyin/creator/pc/work_list":
                    if site.mode == "worklist-auth":
                        return self.send({"status_code": 8, "status_msg": "用户未登录"})
                    return self.send({"status_code": 0, "aweme_list": [] if site.hide_works else site.works})
                if path.startswith("/creator-micro/"):
                    if site.mode == 'manual-repeat' and path.endswith('/manage'):
                        site.verification_released.clear()
                    return self.send(HTML.replace("__MODE__", json.dumps(site.mode)).replace('__VERIFY__', VERIFY_SCRIPT), "text/html; charset=utf-8")
                if path == "/tos-cn-v/fixture-video":
                    return self.send((Path(__file__).parent / "fixtures/douyin-preview.webm").read_bytes(), "video/webm")
                if path in {"/portrait-cover", "/landscape-cover", "/changed-cover", "/uncropped-cover"}:
                    size = (3, 4) if path == '/portrait-cover' else (9, 16) if path == '/uncropped-cover' else (4, 3)
                    return self.send(cover_png(*size), "image/png")
                if path == "/cover":
                    from tests.task_fixtures import IMAGE_BYTES
                    return self.send(IMAGE_BYTES, "image/png")
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
                if site.mode == "submit-auth":
                    return self.send({"status_code": 8, "status_msg": "用户未登录"})
                return self.send({"status_code": 0, **({} if site.mode in {"lost-receipt", "challenge", "manual-lost-receipt"}
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

    async def close_window(self):
        assert self.page is not None
        await self.page.close()

    def port(self):
        from orchestrator.platforms.douyin import DouyinVideoPort

        site = self
        class ReplayPort(DouyinVideoPort):
            verification_scope = "local_fixture"
            origin = site.origin
            upload_origin = site.origin

            async def submit_once(self, page):
                receipt = await super().submit_once(page)
                if site.mode == 'receipt-verification':
                    await page.evaluate(f'void ({VERIFY_SCRIPT})()')
                return receipt

        return ReplayPort()

    def adapter(self, service, **options):
        site = self

        class ReplayAdapter(BrowserDeliveryAdapter):
            async def prepare(self, context):
                result = await super().prepare(context)
                site.page = self.page
                # A local page change after successful preparation must be
                # caught by the public service's final readiness check.
                if site.mode == "removed-cover-after-ready":
                    await self.page.locator('#landscape img').evaluate("img=>img.remove()")
                elif site.mode == "changed-cover-after-ready":
                    await self.page.locator('#landscape img').evaluate("img=>img.src='/changed-cover'")
                elif site.mode == "failed-cover-check-after-ready":
                    await self.page.locator('.cover-check').evaluate("el=>el.textContent='封面检测未通过'")
                return result

        return ReplayAdapter(service.runtime_dir, self.port, **options,
            session_factory=lambda context: BrowserSession(context, launch_options={
                "headless": True, "executable_path": str(_installed_chromium_executable())}))

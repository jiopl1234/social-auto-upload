"""Loopback replay of the observed preparation protocol, with synthetic data."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit

from orchestrator.browser import BrowserSession
from orchestrator.platforms.base import BrowserDeliveryAdapter, PreparationOnlyAdapter
from orchestrator.platforms.xiaohongshu import XiaohongshuVideoPreparationPort
from orchestrator.platforms.xiaohongshu_delivery import XiaohongshuVideoPort, DETAIL_PATH, LIST_PATH, PUBLISH_PATH
from tests.test_browser_lifecycle import _installed_chromium_executable


UID = "0123456789abcdef01234567"
NOTE_ID = "123456789abcdef012345678"
HTML = r"""<!doctype html><meta charset=utf-8>
<input type=file class=upload-input accept=video/mp4>
<section id=editor hidden>
<input placeholder="填写标题会有更多赞哦">
<div class=tiptap contenteditable=true><p><br></p></div>
<video class=exact-video></video>
<div class=d-select-description>公开可见</div>
<div class=d-select-content><div class=d-select-description></div><div>添加内容类型声明</div></div>
<div class=collection-plugin-create>创建合集</div>
<div class=cover-plugin-preview><input type=checkbox>
<span>默认截取第一帧作为封面，优质的封面会吸引更多人浏览笔记</span></div>
<div class=custom-switch-card><span>定时发布</span><input type=checkbox></div>
<div class=custom-switch-card><span>原创声明</span><input type=checkbox></div>
<button id=publish>发布</button></section>
<script>
const mode=__MODE__, video=document.querySelector('video'), editor=document.querySelector('#editor');
fetch('/api/galaxy/user/info');
const body=document.querySelector('.tiptap');
body.addEventListener('beforeinput',event=>{
  if(event.inputType!=='insertText' || event.data===null) return;
  event.preventDefault(); const text=event.data; body.replaceChildren();
  for(const line of text.split('\n')) {const p=document.createElement('p');
    if(mode==='rich-markup') {const span=document.createElement('span');span.textContent=line;p.append(span);}
    else if(line) p.textContent=line; else p.append(document.createElement('br'));
    body.append(p);
  }
});
document.querySelector('.upload-input').onchange=async e=>{
  const file=e.target.files[0], blob=URL.createObjectURL(file);video.src=blob;
  Object.defineProperties(video,{readyState:{get:()=>4},videoWidth:{get:()=>720},videoHeight:{get:()=>1280},
    duration:{get:()=>10},currentSrc:{get:()=>blob}});
  editor.hidden=false;
  await fetch('/api/media/v1/upload/creator/permit');
  if(mode==='challenge') {const node=document.createElement('div');node.className='red-captcha';node.textContent='安全验证';document.body.append(node);return;}
  if(mode==='unknown-dialog') {const node=document.createElement('div');node.setAttribute('role','dialog');node.textContent='未知提示';document.body.append(node);return;}
  const chunk=mode==='large-parts'?5242880:mode==='adaptive-parts'?3145728:2097152;
  const count=Math.ceil(file.size/chunk), completedParts=[];
  const numbers=Array.from({length:count},(_,i)=>i+1);
  if(mode==='large-parts'||mode==='adaptive-parts') numbers.reverse();
  for(const number of numbers) {
    if(mode==='missing-part' && number===count) continue;
    let data=file.slice((number-1)*chunk,number*chunk);
    if(mode==='wrong-file') data=new Blob([new Uint8Array(await data.arrayBuffer()).map(b=>b^1)]);
    const response=await fetch('/spectrum/video001?partNumber='+number+'&uploadId=fixture', {method:'PUT',body:data});
    completedParts.push('<Part><PartNumber>'+number+'</PartNumber><ETag>'+response.headers.get('etag')+'</ETag></Part>');
  }
  if(mode!=='missing-complete') await fetch('/spectrum/video001?uploadId=fixture',{method:'POST',body:'<CompleteMultipartUpload>'+completedParts.join('')+'</CompleteMultipartUpload>'});
  if(mode==='scheduled') document.querySelector('.custom-switch-card input').checked=true;
};
if(mode==='delayed-control') {
  const upload=document.querySelector('.upload-input');upload.remove();
  setTimeout(()=>document.body.prepend(upload),300);
}
document.querySelector('#publish').onclick=()=>{
  const body=Array.from(document.querySelector('.tiptap').children).map(p=>p.innerText==='\n'?'':p.textContent).join('\n');
  if(mode==='post-challenge') {const node=document.createElement('div');node.className='red-captcha';node.textContent='安全验证';document.body.append(node);}
  return fetch('/web_api/sns/v2/note',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:document.querySelector('input[placeholder]').value,body})});
};
</script>"""


class XiaohongshuSite:
    def __init__(self, mode="success"):
        self.mode = mode
        self.parts = {}
        self.data_parts = {}
        self.submissions = 0
        self.readonly_writes = 0
        self.title = self.body = ""
        self.created = int(time.time())
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def send(self, value, kind="application/json"):
                payload = (json.dumps(value).encode() if kind == "application/json" else value.encode())
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/api/galaxy/user/info":
                    self.send({"code": 0, "success": True, "data": {"userId": "f" * 24 if site.mode == "wrong-account" else UID}})
                elif path == "/api/media/v1/upload/creator/permit":
                    self.send({"code": 0, "success": True, "data": {"uploadTempPermits": [{"fileIds": ["spectrum/video001"]}]}})
                elif path == LIST_PATH:
                    rows = [{"id":NOTE_ID,"type":"video","tab_status":1,"schedule_post_time":0,"permission_code":0}] if site.submissions else []
                    if site.mode == 'ambiguous-no-receipt':
                        rows.append({**rows[0], 'id':'a'*24})
                    self.send({"success":True,"code":0,"data":{"notes":rows,
                        "page":1 if site.mode=='incomplete-no-receipt' else -1,
                        "tags":[{"id":"special.note_time_desc","checked":True,"notes_count":len(rows)}]}})
                elif path == DETAIL_PATH:
                    note_id = parse_qs(urlsplit(self.path).query)['note_id'][0]
                    md5 = hashlib.md5(b''.join(site.data_parts[n] for n in sorted(site.data_parts))).hexdigest()
                    self.send({"success":True,"code":0,"data":{
                        "id":note_id,"type":"video","user":{"userid":'f'*24 if site.mode=='detail-wrong-author' else UID},
                        "title":site.title,"desc":'changed' if site.mode=='detail-wrong-body' else site.body,
                        "post_time":site.created-3600 if site.mode=='detail-old-time' else site.created,
                        "privacy":{"type":0},"post_timing":False,"enabled":True,"hash_tag":[],"ats":[],
                        "video_info":{"file_id":"spectrum/canonical_video001",
                                      "media_v2":{"video":{"md5":'0'*32 if site.mode=='detail-wrong-video' else md5}}},
                    }})
                elif path in {'/new/note-manager','/publish/update'}:
                    note_id = parse_qs(urlsplit(self.path).query).get('id', [NOTE_ID])[0]
                    mutation = 'fetch("/web_api/sns/capa/postgw/note/update",{method:"POST"});' if site.mode=='readonly-write-attempt' else ''
                    self.send('<script>'+mutation+'fetch("/api/galaxy/user/info");fetch('+json.dumps(LIST_PATH+'?tab=0&page=0' if path=='/new/note-manager' else DETAIL_PATH+'?note_id='+note_id)+');</script>', 'text/html; charset=utf-8')
                else:
                    self.send(HTML.replace("__MODE__", json.dumps(site.mode)), "text/html; charset=utf-8")

            def do_PUT(self):
                data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                number = int(parse_qs(urlsplit(self.path).query)["partNumber"][0])
                site.parts[number] = hashlib.md5(data).digest()
                site.data_parts[number] = data
                self.send_response(200)
                self.send_header("ETag", '"' + ("0" * 32 if site.mode == "bad-etag" else site.parts[number].hex()) + '"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if urlsplit(self.path).path == '/web_api/sns/capa/postgw/note/update':
                    site.readonly_writes += 1
                    self.send({'success':True,'code':0})
                    return
                if urlsplit(self.path).path == PUBLISH_PATH:
                    site.submissions += 1
                    fields = json.loads(data)
                    site.title, site.body = fields['title'], fields['body']
                    site.created = int(time.time())
                    missing = site.mode in {'missing-receipt','ambiguous-no-receipt','incomplete-no-receipt'}
                    if site.mode == 'live-result-receipt':
                        self.send({'success':True,'result':0,'data':{'id':NOTE_ID}})
                        return
                    self.send({"success":True,"code":0,"data":{} if missing else {'note_id':NOTE_ID}})
                    return
                # The observed service ETag is opaque, not the S3 formula.
                etag = 'a' * 32 + f"-{len(site.parts)}"
                self.send(f'<CompleteMultipartUploadResult><Key>spectrum/video001</Key><ETag>"{etag}"</ETag></CompleteMultipartUploadResult>', "application/xml")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def fixture_adapter(service, site, *, delivery=False, verification_wait=0, on_verification=None):
    class LocalPort(XiaohongshuVideoPreparationPort):
        origin = site.url
        api_origin = site.url
        upload_hosts = frozenset({urlsplit(site.url).netloc})
        verification_scope = "local_fixture"

        async def _preview(self, page):
            # The tiny intake fixture has an MP4 signature, not a real codec.
            # Simulate decoder readiness in the driver's isolated world while
            # retaining the real blob and production byte/hash verification.
            await page.locator('video.exact-video').evaluate("""e => Object.defineProperties(e, {
              readyState:{configurable:true,get:()=>4}, videoWidth:{configurable:true,get:()=>720},
              videoHeight:{configurable:true,get:()=>1280}, duration:{configurable:true,get:()=>10},
              currentSrc:{configurable:true,get:()=>e.src}
            })""")
            return await super()._preview(page)

    class LocalDeliveryPort(LocalPort, XiaohongshuVideoPort):
        pass

    executable = _installed_chromium_executable()
    adapter = BrowserDeliveryAdapter(service.runtime_dir, LocalDeliveryPort if delivery else LocalPort,
        verification_wait=verification_wait, on_verification=on_verification,
        session_factory=lambda context: BrowserSession(context, launch_options={"headless": True, "executable_path": str(executable)}))
    return adapter if delivery else PreparationOnlyAdapter(adapter)

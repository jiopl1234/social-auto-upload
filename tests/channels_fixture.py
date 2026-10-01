"""Isolated synthetic Channels protocol; all network is fulfilled locally."""
import hashlib
import json
import time
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from orchestrator.browser import BrowserSession
from orchestrator.platforms.base import BrowserDeliveryAdapter, PreparationOnlyAdapter
from orchestrator.platforms.channels import IDENTITY_PATH, ORIGIN, CREATE_PATH, LIST_PATH, DETAIL_PATH
from orchestrator.platforms.channels import ChannelsVideoPreparationPort
from orchestrator.platforms.channels_delivery import ChannelsVideoPort
from tests.test_browser_lifecycle import _installed_chromium_executable

UID = 'v2_' + 'a'*64 + '@finder'
CONTENT_ID = 'synthetic_export_id_001'
KEY = 'synthetic_media_key_' + 'x'*64
VIDEO_URL = 'https://finder.video.qq.com/251/20302/stodownload?encfilekey='+KEY+'&token=private'

HTML = r'''<!doctype html><meta charset=utf-8><div id=app></div><script>
const mode=__MODE__,root=document.querySelector('#app').attachShadow({mode:'open'});
root.innerHTML=`<input type=file accept=video/mp4><section class=form>
<input placeholder="填写短标题有机会获得更多流量"><div class=input-editor contenteditable=true></div>
<video id=local></video><video id=remote></video>
<div class=position-display>不显示位置</div><div class=post-album-display>选择合集</div>
<div class=link-display-wrap>选择链接</div><div class=activity-display>不参与活动</div>
<div class=mark-tag-select><div class=select-display>选择视频标注</div></div>
<input type=radio value=0 checked><input type=radio value=1><button id=publish>发表</button></section>`;
const get=s=>root.querySelector(s);
fetch('__IDENTITY__',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene:7,reqScene:7})});
if(location.pathname.endsWith('/list')){
 fetch('__LIST__',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pageSize:20,currentPage:1,userpageType:11,stickyOrder:true,_log_finder_id:'__UID__',...(mode==='filtered-list'?{postType:4}:{})})});
 if(mode==='readonly-write')fetch('__CREATE__',{method:'POST',body:'{}'});
}
get('input[type=file]').onchange=async e=>{
 const file=e.target.files[0], blob=URL.createObjectURL(file),video=get('#local');
 video.dataset.src=blob;get('#remote').dataset.src='__VIDEO__';
 for(const v of [video,get('#remote')])Object.defineProperties(v,{readyState:{get:()=>4},videoWidth:{get:()=>720},videoHeight:{get:()=>1280},duration:{get:()=>10}});
 Object.defineProperty(video,'currentSrc',{get:()=>blob});
 Object.defineProperty(get('#remote'),'currentSrc',{get:()=> '__VIDEO__'});
 if(mode==='challenge'){root.insertAdjacentHTML?.('beforeend','');const d=document.createElement('div');d.className='captcha';d.textContent='安全验证';root.append(d);return;}
 if(mode==='dialog'){const d=document.createElement('div');d.setAttribute('role','dialog');d.textContent='unknown';root.append(d);return;}
 if(mode==='scheduled'){get('input[value="0"]').checked=false;get('input[value="1"]').checked=true;}
 if(mode==='location')get('.position-display').textContent='Unrequested location';
 if(localStorage.getItem('AssistantUploadedInfoStorageKey_123'))return;
 let data=file;
 if(mode==='wrong-file')data=new Blob([new Uint8Array(await file.arrayBuffer()).map(x=>x^1)]);
 const r=await fetch('https://channels.weixin.qq.com/uploadpartdfs?PartNumber=1&UploadID=fixture',{method:'PUT',body:data});
 const p=await r.json();
 if(mode!=='missing-complete')await fetch('https://channels.weixin.qq.com/completepartuploaddfs?UploadID=fixture',{method:'POST',body:JSON.stringify({PartInfo:[{PartNumber:1,ETag:p.ETag}]})});
 if(mode==='upload-error'){const d=document.createElement('div');d.textContent='网络出错，请重新上传。';root.append(d);}
 if(mode==='processing-stuck')get('#publish').className='weui-desktop-btn_disabled';
};
get('#publish').onclick=()=>{
 const caption={title:get('input[placeholder]').value,body:get('.input-editor').innerText};
 if(mode==='post-challenge'){const d=document.createElement('div');d.className='captcha';d.textContent='安全验证';root.append(d);}
 fetch('__CREATE__',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(caption)});
 if(mode==='frontend-retry')setTimeout(()=>fetch('__CREATE__',{method:'POST',body:JSON.stringify(caption)}),10);
};
</script>'''


class ChannelsSite:
    def __init__(self, mode='success'):
        self.mode=mode
        self.submissions=0
        self.readonly_writes=0
        self.source_md5=''
        self.caption={}
        self.responses={}
        self.created=int(time.time())
        site=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,format,*args):pass
            def handle_request(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                method=self.command
                request=SimpleNamespace(url=site.origin+self.path,method=method,post_data_buffer=body,
                    post_data_json=json.loads(body) if body and body[:1]==b'{' else None,
                    is_navigation_request=lambda:method=='GET')
                handler=self
                class Route:
                    def __init__(self):self.request=request
                    async def fulfill(self,status=200,content_type=None,headers=None,body=''):
                        data=body.encode() if isinstance(body,str) else body
                        handler.send_response(status)
                        handler.send_header('Content-Type',content_type or 'text/plain')
                        handler.send_header('Content-Length',str(len(data)))
                        for key,value in (headers or {}).items():handler.send_header(key,value)
                        handler.end_headers();handler.wfile.write(data)
                asyncio.run(site.route(Route()))
            do_GET=do_POST=do_PUT=do_OPTIONS=handle_request
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.origin='http://127.0.0.1:'+str(self.server.server_address[1])
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    def __enter__(self):self.thread.start();return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close();self.thread.join(3)

    async def route(self, route):
        req=route.request
        path=urlsplit(req.url).path
        payload={}
        status=200
        if req.is_navigation_request():
            html=HTML.replace('https://channels.weixin.qq.com/uploadpartdfs',self.origin+'/uploadpartdfs').replace('https://channels.weixin.qq.com/completepartuploaddfs',self.origin+'/completepartuploaddfs').replace('__MODE__',json.dumps(self.mode)).replace('__IDENTITY__',IDENTITY_PATH).replace('__LIST__',LIST_PATH).replace('__CREATE__',CREATE_PATH).replace('__VIDEO__',VIDEO_URL).replace('__UID__',UID)
            await route.fulfill(status=200,content_type='text/html',body=html)
            return
        if req.method=='OPTIONS':
            await route.fulfill(status=204,headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'PUT,POST','Access-Control-Allow-Headers':'*'})
            return
        if path==IDENTITY_PATH:
            if self.mode=='identity-after-list':await asyncio.sleep(.2)
            status=201
            payload={'errCode':0,'data':{'finderUser':{'finderUsername':UID if self.mode!='wrong-account' else 'v2_'+'b'*64+'@finder'}}}
        elif path=='/uploadpartdfs':
            body=req.post_data_buffer
            self.source_md5=hashlib.md5(body).hexdigest()
            payload={'ETag':'"'+self.source_md5+'"','X-Errno':0}
        elif path=='/completepartuploaddfs':
            payload={'DownloadURL':VIDEO_URL}
        elif path==CREATE_PATH:
            self.submissions+=1
            self.created=int(time.time())
            self.caption=req.post_data_json
            payload={'errCode':0,'data':{'baseResp':{'errcode':0},'objectId':CONTENT_ID}}
            if self.mode=='negative-receipt':payload['errCode']=1
            if self.mode in {'no-id-receipt','filtered-list','incomplete-list','duplicate-match','missing-author','in-review','bad-detail-settings'}:
                del payload['data']['objectId']
        elif path==LIST_PATH:
            rows=[{'objectId':CONTENT_ID,'status':1,'visibleType':1,'handleStatus':2}] if self.submissions else []
            if self.mode=='duplicate-match' and rows:rows.append({**rows[0],'objectId':'synthetic_duplicate_002'})
            if self.mode=='in-review' and rows:
                rows[0].pop('handleStatus')
                rows[0]['disableInfo']={'isDisabled':True,'disableAddWording':'审核中'}
            payload={'errCode':0,'data':{'list':rows,'totalCount':len(rows)+(1 if self.mode=='incomplete-list' else 0),'continueFlag':False}}
        elif path==DETAIL_PATH:
            detail={'objectId':req.post_data_json['objectId'],'finderUsername':UID,'createTime':self.created,'topics':[],
                    'status':1,'visibleType':1,
                    'desc':{'mediaType':4,'shortTitle':[{'shortTitle':self.caption.get('title','')}],
                            'description':self.caption.get('body',''),'location':{},'event':{},'mentionedUser':[],
                            'media':[{'url':VIDEO_URL.replace(KEY,'canonical_media_key_'+'y'*64) if self.mode in {'no-id-receipt','missing-author'} else VIDEO_URL,'md5sum':self.source_md5}]}}
            if self.mode=='wrong-detail-body':detail['desc']['description']='other'
            if self.mode=='wrong-detail-author':detail['finderUsername']='v2_'+'b'*64+'@finder'
            if self.mode=='wrong-detail-media':detail['desc']['media'][0]['md5sum']='0'*32
            if self.mode=='missing-author':del detail['finderUsername']
            if self.mode=='bad-detail-settings':detail['desc']['extReading']={'link':'https://example.test'}
            payload={'errCode':0,'data':{'baseResp':{'errcode':0},'object':detail}}
        self.responses[req.url]=payload
        await route.fulfill(status=status,content_type='application/json',headers={'Access-Control-Allow-Origin':'*'},body=json.dumps(payload))


def fixture_adapter(service, site, *, delivery=False):
    class FixturePort(ChannelsVideoPreparationPort):
        def __init__(self):
            super().__init__()
            self.verification_scope='local_fixture'
            self.origin=site.origin
            self.edit_url=site.origin+'/platform/post/create'
            self.works_url=site.origin+'/platform/post/list'
            self.upload_hosts=frozenset({'127.0.0.1'})
        async def _preview(self,page):
            # Simulate codec readiness in the driver's isolated world, while
            # retaining actual blob bytes and the production hash comparison.
            await page.locator('video').evaluate_all("""es=>es.forEach(e=>Object.defineProperties(e,{
                readyState:{configurable:true,get:()=>4},videoWidth:{configurable:true,get:()=>720},
                videoHeight:{configurable:true,get:()=>1280},duration:{configurable:true,get:()=>10},
                currentSrc:{configurable:true,get:()=>e.dataset.src||''}
            }))""")
            return await super()._preview(page)
        async def _cache_response(self,response):
            if urlsplit(response.url).path in {'/uploadpartdfs','/completepartuploaddfs'}:
                # The local-only network fence intercepts binary requests. The
                # pinned driver's inspector cannot read their response bodies;
                # use the independent fixture server's exact sent JSON here.
                payload=site.responses[response.url]
                async def decoded():return payload
                return SimpleNamespace(url=response.url,status=response.status,request=response.request,json=decoded)
            return await super()._cache_response(response)
    class DeliveryFixturePort(FixturePort,ChannelsVideoPort):
        pass
    adapter=BrowserDeliveryAdapter(service.runtime_dir,DeliveryFixturePort if delivery else FixturePort,
        session_factory=lambda context:BrowserSession(context,launch_options={'headless':True,'executable_path':str(_installed_chromium_executable())}))
    return adapter if delivery else PreparationOnlyAdapter(adapter)

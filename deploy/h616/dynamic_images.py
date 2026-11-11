"""Bounded sequential image download/upload and Feishu card delivery."""
import hashlib,json,logging,sqlite3,tempfile,time
from urllib.parse import urlparse,urljoin
from pathlib import Path
import requests

class ImagePreparationError(RuntimeError):
    media_failure=True

def image_url(value):
    if not isinstance(value,str):return None
    value=value.strip()
    if value.startswith('//'):value='https:'+value
    elif value.startswith('http://'):value='https://'+value[7:]
    p=urlparse(value)
    allowed=('hdslb.com','biliimg.com','bilivideo.com','bilibili.com')
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):return None
    if not any(p.hostname==d or p.hostname.endswith('.'+d) for d in allowed):return None
    return value

def collect_image_urls(dynamic):
    result=[]
    def add(value):
        try:url=image_url(value)
        except ValueError:return
        if url and url not in result:result.append(url)
    def visit(node,depth=0):
        if not isinstance(node,dict) or depth>5:return
        modules=node.get('modules',{})
        if isinstance(modules,dict):mods=[modules.get('module_dynamic',{})]
        elif isinstance(modules,list):mods=[m for m in modules if isinstance(m,dict)]
        else:mods=[]
        for m in mods:
            major=m.get('major') or {}
            for item in (major.get('draw') or {}).get('items') or []:
                if isinstance(item,dict):add(item.get('src') or item.get('url'))
            for item in (major.get('opus') or {}).get('pics') or []:
                if isinstance(item,dict):add(item.get('url') or item.get('src'))
            content=m.get('module_content') or {}
            for para in content.get('paragraphs') or []:
                if not isinstance(para,dict):continue
                for item in (para.get('pic') or {}).get('pics') or []:
                    if isinstance(item,dict):add(item.get('url') or item.get('src'))
        for item in (node.get('item') or {}).get('pictures') or []:
            if isinstance(item,dict):add(item.get('img_src') or item.get('url'))
        for key in ('orig','origin'):
            visit(node.get(key),depth+1)
        card=node.get('card')
        if isinstance(card,str):
            try:card=json.loads(card)
            except (ValueError,TypeError):card=None
        if isinstance(card,dict):visit(card,depth+1)
    visit(dynamic)
    return result

def dynamic_media(w,dynamic,item,uid,name,cookie):
    urls=collect_image_urls(dynamic)
    likely='DRAW' in str(item.get('type','')) or dynamic.get('type')=='DYNAMIC_TYPE_DRAW' or bool(item.get('access_hint'))
    if not urls and likely:
        try:urls=collect_image_urls(w.request_dynamic_detail(item['id'],uid,cookie) or {})
        except Exception:pass
        if not urls:
            try:
                state=w.request_opus_initial_state(item['id'],uid,cookie)
                urls=collect_image_urls((state or {}).get('detail') or {})
            except Exception:pass
    return urls, bool(likely and not urls)

class ImageClient:
    def __init__(self,config,db,ensure_time,signature):
        self.config=config;self.db=db;self.ensure_time=ensure_time;self.signature=signature
        self.token=None;self.expires=0
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS image_uploads(cache_key TEXT PRIMARY KEY,image_key TEXT,uploaded_at REAL)')
        self.scratch=Path(config.get('_image_temp_dir',tempfile.gettempdir()))
        self.scratch.mkdir(parents=True,exist_ok=True)

    def access_token(self):
        self.ensure_time()
        if self.token and time.monotonic()<self.expires:return self.token
        app=self.config.get('feishu_app_id');secret=self.config.get('feishu_app_secret')
        if not app or not secret:raise ImagePreparationError('Image app credentials are missing')
        try:
            r=requests.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',json={'app_id':app,'app_secret':secret},timeout=(8,20))
            r.raise_for_status();payload=r.json()
            if payload.get('code')!=0 or not payload.get('tenant_access_token'):raise ValueError()
            self.token=payload['tenant_access_token'];self.expires=time.monotonic()+max(1,int(payload.get('expire',7200))-120)
            return self.token
        except Exception:raise ImagePreparationError('Feishu app authentication failed') from None

    def download(self,url):
        limit=9*1024*1024;started=time.monotonic()
        for _ in range(4):
            try:checked=image_url(url)
            except ValueError:checked=None
            if not checked:raise ImagePreparationError('Image address is outside approved Bilibili CDN hosts')
            r=requests.get(checked,headers={'Referer':'https://www.bilibili.com/','User-Agent':'Mozilla/5.0'},stream=True,allow_redirects=False,timeout=(8,20))
            if r.status_code in (301,302,303,307,308):
                url=urljoin(checked,r.headers.get('Location',''));r.close();continue
            try:
                r.raise_for_status()
                mime=r.headers.get('Content-Type','').split(';')[0].lower()
                if not mime.startswith('image/'):raise ImagePreparationError('CDN response was not an image')
                if int(r.headers.get('Content-Length','0'))>limit:raise ImagePreparationError('Image exceeds 9 MiB limit')
                f=tempfile.TemporaryFile(dir=self.scratch)
                try:
                    size=0
                    for chunk in r.iter_content(65536):
                        size+=len(chunk)
                        if size>limit or time.monotonic()-started>60:raise ImagePreparationError('Image download exceeded limits')
                        f.write(chunk)
                    if not size:raise ImagePreparationError('Empty image')
                    f.seek(0);return f,mime
                except Exception:f.close();raise
            finally:r.close()
        raise ImagePreparationError('Too many image redirects')

    def upload(self,url):
        cache=hashlib.sha256((str(self.config.get('feishu_app_id'))+'\n'+url).encode()).hexdigest()
        row=self.db.execute('SELECT image_key,uploaded_at FROM image_uploads WHERE cache_key=?',(cache,)).fetchone()
        if row and 0<=time.time()-row[1]<7*86400:return row[0]
        self.ensure_time()
        f,mime=self.download(url)
        try:
            for attempt in range(2):
                token=self.access_token();self.ensure_time();f.seek(0)
                r=requests.post('https://open.feishu.cn/open-apis/im/v1/images',headers={'Authorization':'Bearer '+token},data={'image_type':'message'},files={'image':('dynamic-image',f,mime)},timeout=(8,40))
                payload=r.json()
                if payload.get('code') in (99991663,99991664,99991668) and not attempt:
                    self.token=None;continue
                r.raise_for_status()
                key=(payload.get('data') or {}).get('image_key')
                if payload.get('code')!=0 or not key:raise ImagePreparationError('Feishu image upload rejected')
                with self.db:self.db.execute('INSERT OR REPLACE INTO image_uploads VALUES(?,?,?)',(cache,key,time.time()))
                return key
            raise ImagePreparationError('Feishu image authentication failed')
        except ImagePreparationError:raise
        except Exception:raise ImagePreparationError('Image upload failed') from None
        finally:f.close()

    def send(self,title,body,urls,allow_fallback=False):
        max_images=max(1,min(30,int(self.config.get('max_dynamic_images',18))))
        keys=[];failed=[]
        for url in urls[:max_images]:
            try:keys.append(self.upload(url))
            except Exception:
                failed.append(url)
        omitted=urls[max_images:]
        if failed and not allow_fallback:raise ImagePreparationError('Some dynamic images are not uploaded yet')
        elements=[{'tag':'markdown','content':body}]
        for key in keys:
            elements.append({'tag':'img','img_key':key,'alt':{'tag':'plain_text','content':'动态配图'},'mode':'fit_horizontal','preview':True})
        if failed or omitted:
            text=f'配图共 {len(urls)} 张，已附 {len(keys)} 张。其余图片上传失败或超过展示上限，请打开原动态查看。'
            elements.append({'tag':'note','elements':[{'tag':'plain_text','content':text}]})
        payload={'msg_type':'interactive','card':{'config':{'wide_screen_mode':True},'header':{'title':{'tag':'plain_text','content':title},'template':'blue'},'elements':elements}}
        self.ensure_time()
        secret=self.config.get('feishu_secret')
        if secret:
            stamp=int(time.time());payload['timestamp']=str(stamp);payload['sign']=self.signature(secret,stamp)
        r=requests.post(self.config['feishu_webhook'],json=payload,timeout=(8,20))
        r.raise_for_status();result=r.json()
        if result.get('code') not in (0,None):
            # A cached upload key can be rejected later; next retry must re-upload.
            with self.db:self.db.execute('DELETE FROM image_uploads')
            raise RuntimeError('Feishu card rejected, code='+str(result.get('code')))
        if failed or omitted:logging.warning('Image card delivered with fallback: attached=%s total=%s',len(keys),len(urls))

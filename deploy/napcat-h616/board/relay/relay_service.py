"""Authenticated loopback event receiver, durable outbox, and bounded catch-up."""
import base64,datetime as dt,hashlib,hmac,json,os,shutil,sys,threading,time
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from urllib.parse import urlparse,urljoin,unquote
import requests
from queue_store import Outbox,deliver_one,selected_message

BASE=Path('/overlay/napcat-lab');ROOT=BASE/'rootfs';DB=BASE/'forward-outbox.sqlite3'
sys.path.insert(0,'/overlay/b-up/app')

def ensure_time():
    from timeguard import require_trusted_time
    require_trusted_time()

def config():
    c=json.loads((BASE/'forward-config.json').read_text())
    if not c.get('enabled'):raise RuntimeError('Not enabled')
    p=urlparse(c['feishu_webhook'])
    if p.scheme!='https' or p.hostname!='open.feishu.cn' or p.username or p.password or not p.path.startswith('/open-apis/bot/v2/hook/'):raise ValueError('Invalid webhook')
    if not c.get('event_token') or not c.get('api_token') or not c.get('start_epoch'):raise ValueError('Missing private settings')
    return c

def api(cfg,action,args):
    s=requests.Session();s.trust_env=False
    try:
        r=s.post('http://127.0.0.1:6101/'+action,json=args,headers={'Authorization':'Bearer '+cfg['api_token']},timeout=(3,25))
        r.raise_for_status();v=r.json()
        if v.get('status')!='ok' or v.get('retcode')!=0:raise RuntimeError('NapCat API rejected')
        return v['data']
    finally:s.close()

def signed(secret,stamp):
    return base64.b64encode(hmac.new((str(stamp)+'\n'+secret).encode(),b'',hashlib.sha256).digest()).decode()

def store_image(source,target):
    limit=9*1024**2;target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():return str(target)
    p=urlparse(source);temp=target.with_suffix('.partial');started=time.monotonic()
    try:
        if p.scheme in ('file','') and (p.scheme=='file' or source.startswith('/')):
            native=Path(unquote(p.path))
            resolved=(ROOT/str(native).lstrip('/')).resolve()
            allowed=[ROOT/'home/napcat/.config/QQ',ROOT/'opt/QQ/resources/app/napcat/temp']
            if not any(resolved.is_relative_to(x.resolve()) for x in allowed):raise ValueError('Image outside QQ media roots')
            if not resolved.is_file() or resolved.stat().st_size>limit:raise ValueError('Invalid local image')
            with resolved.open('rb') as f,temp.open('wb') as out:
                while True:
                    block=f.read(65536)
                    if not block:break
                    if out.tell()+len(block)>limit:raise ValueError('Image too large')
                    out.write(block)
                out.flush();os.fsync(out.fileno())
        else:
            session=requests.Session();session.trust_env=False
            try:
                for _ in range(4):
                    p=urlparse(source)
                    if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not p.hostname or not any(p.hostname==d or p.hostname.endswith('.'+d) for d in ('qq.com','qpic.cn','gtimg.cn','gtimg.com')):raise ValueError('Unapproved QQ image host')
                    with session.get(source,stream=True,allow_redirects=False,timeout=(5,15)) as r:
                        if r.status_code in (301,302,303,307,308):source=urljoin(source,r.headers.get('Location',''));continue
                        r.raise_for_status()
                        if int(r.headers.get('Content-Length','0'))>limit:raise ValueError('Image too large')
                        with temp.open('wb') as out:
                            for block in r.iter_content(65536):
                                if out.tell()+len(block)>limit or time.monotonic()-started>45:raise ValueError('Image limit')
                                out.write(block)
                            out.flush();os.fsync(out.fileno())
                        break
                else:raise ValueError('Redirect limit')
            finally:session.close()
        header=temp.read_bytes()[:16] if temp.stat().st_size<32 else b''
        if not header:
            with temp.open('rb') as f:header=f.read(16)
        if not (header.startswith(b'\x89PNG\r\n\x1a\n') or header.startswith(b'\xff\xd8\xff') or header[:6] in (b'GIF87a',b'GIF89a') or (header[:4]==b'RIFF' and header[8:12]==b'WEBP')):raise ValueError('Unsupported image bytes')
        temp.chmod(0o600);temp.replace(target);return str(target)
    finally:temp.unlink(missing_ok=True)

def make_plan(key,msg,cfg=None):
    texts=[];images=[]
    for i,s in enumerate(msg['segments']):
        data=s['data'];kind=s['type']
        if kind=='text':texts.append(str(data.get('text','')))
        elif kind=='at':texts.append('@'+str(data.get('qq','')))
        elif kind=='image':
            target=BASE/'forward-cache/pending'/key/(str(i)+'.image')
            sources=[str(data.get(k,'')) for k in ('url','file') if data.get(k)]
            if cfg and data.get('file'):
                try:
                    info=api(cfg,'get_image',{'file':str(data['file'])})
                    sources.extend(str(info[k]) for k in ('file','url') if info.get(k))
                except Exception:pass
            for source in sources:
                try:images.append(store_image(source,target));break
                except Exception:continue
            else:raise RuntimeError('Image not captured; retain pending message')
        elif kind in ('json','xml'):texts.append('\n[消息卡片]\n'+str(data.get('data','')))
        elif kind=='face':texts.append('[QQ表情 '+str(data.get('id',''))+']')
        elif kind=='reply':texts.append('[引用消息 '+str(data.get('id',''))+']\n')
        else:texts.append('\n['+kind+'，附件暂未转存] '+str(data.get('name','')))
    stamp=dt.datetime.fromtimestamp(msg['time'],dt.timezone(dt.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    heading=f"群 {msg['group_id']}｜{msg['name']}（{msg['user_id']}）\n{stamp}\n消息编号：{msg['message_id']}\n"
    if msg.get('merged_count',1)>1:
        heading=f"群 {msg['group_id']}｜{msg['name']}（{msg['user_id']}）\n{stamp} 起，合并 {msg['merged_count']} 条连续发言\n消息编号：{', '.join(msg['message_ids'])}\n"
    body=heading+''.join(texts)
    # Small parts are durable independently; no silent truncation of long messages.
    parts=[{'text':body[i:i+3500]} for i in range(0,len(body),3500)]
    # Most messages fit one post: text and up to nine ordinary images travel together.
    # Preserve the existing bounded text chunks for exceptionally long messages.
    for start in range(0,len(images),9):
        if start==0:parts[-1]['images']=images[start:start+9]
        else:parts.append({'images':images[start:start+9]})
    return parts

class Sender:
    def __init__(self,cfg,box):
        self.cfg=cfg;self.box=box;self.images=None
    def image_key(self,path):
        if self.images is None:
            from dynamic_images import ImageClient
            parent=(BASE/'forward-cache/pending').resolve()
            class SavedImageClient(ImageClient):
                def download(self,uri):
                    p=Path(uri).resolve()
                    if not p.is_relative_to(parent) or not p.is_file():raise ValueError('Invalid saved image')
                    return p.open('rb'),'application/octet-stream'
            private=json.loads(Path('/overlay/b-up/data/config.json').read_text())
            self.images=SavedImageClient(private,self.box.db,ensure_time,signed)
        return self.images.upload(path)
    def __call__(self,part):
        ensure_time();cfg=self.cfg
        content=[]
        if 'text' in part:content.append([{'tag':'text','text':part['text']}])
        if 'image' in part:content.append([{'tag':'img','image_key':self.image_key(part['image'])}])
        for path in part.get('images',[]):content.append([{'tag':'img','image_key':self.image_key(path)}])
        if not content:raise ValueError('Empty message part')
        title=part.get('title','QQ群管理员消息')
        payload={'msg_type':'post','content':{'post':{'zh_cn':{'title':(cfg.get('feishu_keyword','')+' '+title).strip(),'content':content}}}}
        if cfg.get('feishu_secret'):
            stamp=int(time.time());payload.update(timestamp=str(stamp),sign=signed(cfg['feishu_secret'],stamp))
        r=requests.post(cfg['feishu_webhook'],json=payload,timeout=(5,20));r.raise_for_status();v=r.json()
        if v.get('code',v.get('StatusCode'))!=0:raise RuntimeError('Feishu rejected message')
        time.sleep(1)

def catch_up(box,cfg):
    """Only target groups; non-selected history remains in memory, never on disk."""
    ensure_time();roles={}
    for group in cfg['allowed_group_ids']:
        statekey='history:'+str(group);cutoff=box.get(statekey,cfg['start_epoch']);started=int(time.time())
        progress=box.get(statekey+':progress',{})
        cursor=progress.get('cursor');started=progress.get('started',started)
        visited=set();complete=False
        for page in range(20):
            args={'group_id':str(group),'count':50,'reverse_order':False,'disable_get_url':True,'parse_mult_msg':False}
            if cursor is not None:args['message_seq']=str(cursor)
            messages=api(cfg,'get_group_msg_history',args).get('messages',[])
            if not messages:complete=True;break
            older=False
            for raw in sorted(messages,key=lambda m:int(m.get('time',0))):
                if int(raw.get('time',0))<cutoff:older=True;continue
                event=dict(raw,post_type='message',message_type='group',group_id=str(group))
                sender=dict(event.get('sender') or {});uid=sender.get('user_id',event.get('user_id'))
                if not uid:continue
                if sender.get('role') not in ('admin','owner','member'):
                    k=(group,str(uid))
                    if k not in roles:roles[k]=api(cfg,'get_group_member_info',{'group_id':str(group),'user_id':str(uid),'no_cache':False}).get('role','member')
                    sender['role']=roles[k]
                sender['user_id']=uid;event['sender']=sender
                event.setdefault('self_id',cfg['self_id']);box.accept(event,cfg)
            # QQ may return fewer than requested while older pages still exist.
            if older:complete=True;break
            oldest=min(messages,key=lambda m:int(m.get('time',0)))
            cursor=oldest.get('message_id')
            if cursor is None or str(cursor) in visited:break
            visited.add(str(cursor))
        if complete:
            box.set(statekey,max(cfg['start_epoch'],started-5));box.set(statekey+':progress',{})
        elif cursor is not None:box.set(statekey+':progress',{'cursor':cursor,'started':started})
        box.set('history_status:'+str(group),{'epoch':int(time.time()),'complete':complete})

def worker():
    box=Outbox(DB);last_history=0;last_cleanup=0
    while True:
        try:
            cfg=config();ensure_time();sender=Sender(cfg,box)
            sent=deliver_one(box,cfg,lambda key,msg:make_plan(key,msg,cfg),sender,ensure_time)
            if time.monotonic()-last_history>60:
                try:catch_up(box,cfg);box.set('history_error',None)
                except Exception as e:box.set('history_error',type(e).__name__)
                last_history=time.monotonic()
            if time.monotonic()-last_cleanup>3600:
                box.expire()
                for p in (BASE/'forward-cache/pending').glob('*'):
                    row=box.db.execute('SELECT sent FROM messages WHERE key=?',(p.name,)).fetchone()
                    if row and row['sent'] and time.time()-row['sent']>86400 and p.is_dir() and not p.is_symlink():shutil.rmtree(p)
                last_cleanup=time.monotonic()
            health={'epoch':int(time.time()),'outbox':box.status(),'history_error':box.get('history_error'),
                    'history':{str(g):box.get('history_status:'+str(g)) for g in cfg['allowed_group_ids']}}
            temp=BASE/'relay-health.new';temp.write_text(json.dumps(health));temp.replace(BASE/'relay-health.json')
            if not sent:time.sleep(2)
        except Exception:time.sleep(5)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        self.connection.settimeout(5)
        try:
            cfg=config()
            if self.path!='/events':self.send_error(404);return
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=1024*1024:self.send_error(413);return
            body=self.rfile.read(size)
            signature='sha1='+hmac.new(cfg['event_token'].encode(),body,hashlib.sha1).hexdigest()
            if not hmac.compare_digest(signature,self.headers.get('x-signature','')):self.send_error(403);return
            event=json.loads(body)
            if str(event.get('self_id'))!=str(cfg['self_id']):self.send_error(403);return
            if selected_message(event,cfg) is None:
                self.send_response(200);self.end_headers();self.wfile.write(b'{}');return
            box=Outbox(DB)
            try:box.accept(event,cfg)
            finally:box.db.close()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{}')
        except (ValueError,TypeError,KeyError):self.send_error(400)
        except Exception:self.send_error(503)

def main():
    import fcntl
    os.umask(0o077);lock=(BASE/'relay.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    threading.Thread(target=worker,daemon=True).start()
    HTTPServer(('127.0.0.1',6102),Handler).serve_forever()

if __name__=='__main__':main()

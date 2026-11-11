import json, os, ssl, signal, subprocess, sys
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
BASE=Path('/overlay/b-up')

def apply(raw):
    config=json.loads(raw.decode('utf-8-sig'))
    import bilibili_feishu_watcher as w
    w.normalize_up_users(config.get('up_users',[]))
    if not isinstance(config.get('feishu_webhook'),str) or not config['feishu_webhook'].startswith('https://'):
        raise ValueError('HTTPS webhook required')
    interval=int(config.get('interval_seconds',300))
    if not 30<=interval<=86400: raise ValueError('interval_seconds must be 30..86400')
    if not isinstance(config.get('bilibili_cookie',''),str): raise ValueError('Cookie must be a string')
    if not isinstance(config.get('enable_dynamic_images',False),bool):raise ValueError('enable_dynamic_images must be boolean')
    if config.get('enable_dynamic_images'):
        if not all(isinstance(config.get(k),str) and config[k].strip() for k in ('feishu_app_id','feishu_app_secret')):raise ValueError('Image app credentials required')
    if not 1<=int(config.get('max_dynamic_images',18))<=30:raise ValueError('max_dynamic_images must be 1..30')
    if not 0<=int(config.get('daily_report_hour',21))<=23:raise ValueError('daily_report_hour must be 0..23')
    if not isinstance(config.get('daily_report_enabled',True),bool):raise ValueError('daily_report_enabled must be boolean')
    config['poll_interval_seconds']=interval
    # Poll collection at the configured interval, retry the outbox every 30 seconds.
    config['interval_seconds']=30
    target=BASE/'data/config.json'
    old=target.read_bytes()
    backup=BASE/'data/config.previous.json'
    atomic(backup,old)
    atomic(target,json.dumps(config,ensure_ascii=False,indent=2).encode())
    pid=Path('/var/run/b-up-watcher.pid')
    if pid.exists():
        p=int(pid.read_text())
        try:
            if b'/overlay/b-up/app/runner.py' in Path(f'/proc/{p}/cmdline').read_bytes(): os.kill(p,signal.SIGTERM)
        except FileNotFoundError: pass

def atomic(path,raw):
    temp=path.with_suffix('.new')
    with open(temp,'wb') as f:
        os.chmod(temp,0o600); f.write(raw); f.flush(); os.fsync(f.fileno())
    os.replace(temp,path)
    fd=os.open(path.parent,os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        if self.path!='/':self.send_error(404);return
        page=(BASE/'app/config_page.html').read_bytes()
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Length',str(len(page)));self.end_headers();self.wfile.write(page)
    def do_POST(self):
        try:
            origin=self.headers.get('Origin')
            if origin and origin!='https://'+self.headers.get('Host',''):raise ValueError('Invalid origin')
            if self.path!='/config': raise ValueError('Unknown route')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=262144: raise ValueError('Invalid length')
            self.connection.settimeout(15)
            raw=self.rfile.read(length)
            apply(raw)
        except Exception:
            self.send_response(400); self.end_headers(); self.wfile.write(b'Configuration rejected; check JSON and required fields')
        else:
            self.send_response(200); self.end_headers(); self.wfile.write(b'Configuration saved. Watcher will reload; progress and queue retained.')

if __name__=='__main__':
    os.umask(0o077)
    if len(sys.argv)>1: apply(Path(sys.argv[1]).read_bytes())
    else:
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(BASE/'private/server.pem'),str(BASE/'private/server.key'))
        context.load_verify_locations(str(BASE/'private/ca.pem')); context.verify_mode=ssl.CERT_REQUIRED
        server=HTTPServer(('0.0.0.0',8443),Handler)
        server.socket=context.wrap_socket(server.socket,server_side=True)
        server.serve_forever()

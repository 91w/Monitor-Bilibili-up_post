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
    def do_POST(self):
        try:
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

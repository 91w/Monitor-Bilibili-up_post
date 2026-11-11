"""Loopback-only browser bridge to a paired board. Remote TLS remains authenticated."""
import os, argparse, http.client, ssl, secrets, webbrowser, threading, json, hashlib, ipaddress
from pathlib import Path
from http.server import HTTPServer,BaseHTTPRequestHandler

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host')
    parser.add_argument('--pairing',default=os.environ.get('B_UP_PAIRING_DIR',str(Path.home()/'.b-up/pairing')))
    args=parser.parse_args()
    host=(args.host or input('Board IP / 开发板当前 IP: ').strip())
    address=ipaddress.ip_address(host)
    if address.version!=4 or not address.is_private or address.is_loopback:raise SystemExit('请填写开发板的局域网 IPv4 地址')
    host=str(address);pair=Path(args.pairing)
    ctx=ssl.create_default_context(cafile=str(pair/'ca.pem'))
    # Dedicated paired CA + exact server certificate pin authenticate this board
    # even when its DHCP IP changes. Do not use CERT_NONE.
    ctx.check_hostname=False
    ctx.load_cert_chain(str(pair/'client.pem'),str(pair/'client.key'))
    pin=hashlib.sha256(ssl.PEM_cert_to_DER_cert((pair/'server.pem').read_text())).digest()
    token=secrets.token_urlsafe(24)
    route='/'+token
    def remote(method,path,body=None,timeout=25):
        conn=http.client.HTTPSConnection(host,8443,context=ctx,timeout=timeout)
        try:
            conn.connect()
            if not secrets.compare_digest(hashlib.sha256(conn.sock.getpeercert(binary_form=True)).digest(),pin):
                raise ssl.SSLError('Board certificate differs from paired certificate')
            conn.request(method,path,body=body,headers={'Content-Type':'application/json'})
            r=conn.getresponse();return r.status,r.read(524288)
        finally:conn.close()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def response(self,status,data,content='text/plain; charset=utf-8'):
            self.send_response(status);self.send_header('Content-Type',content);self.send_header('Cache-Control','no-store');self.send_header('X-Frame-Options','DENY');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def valid_host(self):
            return self.headers.get('Host')=='127.0.0.1:'+str(self.server.server_port)
        def do_GET(self):
            if not self.valid_host() or self.path not in (route+'/',route+'/status-api',route+'/config-page'):self.response(404,b'Not found');return
            try:
                if self.path==route+'/config-page':
                    code,data=remote('GET','/')
                    data=data.replace(b"fetch('/config'",("fetch('"+route+"/config'").encode())
                    self.response(code,data,'text/html; charset=utf-8')
                else:
                    conn=http.client.HTTPConnection(host,8080,timeout=15)
                    try:
                        conn.request('GET','/api/status' if self.path.endswith('/status-api') else '/')
                        r=conn.getresponse();code,data=r.status,r.read(524288)
                    finally:conn.close()
                    if self.path==route+'/':
                        data=data.replace(b"fetch('/api/status'",("fetch('"+route+"/status-api'").encode())
                        data=data.replace(b"'https://'+location.hostname+':8443/'",json.dumps(route+'/config-page').encode())
                    self.response(code,data,'application/json; charset=utf-8' if self.path.endswith('/status-api') else 'text/html; charset=utf-8')
            except Exception:self.response(502,'无法连接开发板，请检查 IP、联网、配对文件或校时。关闭窗口后可填写新 IP 重试。'.encode())
        def do_POST(self):
            origin=self.headers.get('Origin')
            expected='http://127.0.0.1:'+str(self.server.server_port)
            if not self.valid_host() or self.path!=route+'/config' or origin!=expected:
                self.response(403,b'Forbidden');return
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=262144:raise ValueError()
                self.connection.settimeout(15)
                raw=self.rfile.read(n);json.loads(raw.decode('utf-8-sig'))
                code,data=remote('POST','/config',raw)
                self.response(code,data)
            except Exception:self.response(502,'上传未能确认，请检查配置格式与开发板连接。原文件可能已应用，恢复后可重复上传。'.encode())
    # Verify pairing and device before opening the browser.
    code,_=remote('GET','/',timeout=8)
    if code!=200:raise SystemExit('开发板配置服务暂不可用')
    server=HTTPServer(('127.0.0.1',0),Handler)
    url='http://127.0.0.1:'+str(server.server_port)+route+'/'
    print('网页只在本机开放。保持本窗口打开；关闭后网页入口停止。')
    webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()

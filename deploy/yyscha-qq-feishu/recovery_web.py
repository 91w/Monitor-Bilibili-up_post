"""Loopback-only recovery UI; on H616 use an SSH tunnel, never expose it to LAN/WAN."""
import argparse
import base64
import json
import secrets
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import relay as r
from auth_recovery import LoginFlow, import_cookie
from cookie_import import parse_cookie_text
from site_session import get_session

HTML = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>��ҳ������¼�ָ�</title>
<style>body{font:16px system-ui;max-width:700px;margin:40px auto;padding:0 20px;background:#f5f7fa;color:#203040}section{background:white;padding:24px;margin:20px 0;border-radius:12px}input,textarea,button{font:inherit;box-sizing:border-box;padding:10px;margin:6px 0;max-width:100%}input,textarea{width:100%}button{cursor:pointer}#captcha{display:block;margin:12px 0;max-width:250px}#result{white-space:pre-wrap}small{color:#546575}</style>
<h1>��ҳ������¼�ָ�</h1><p>����ɹ��������е��°�������Զ����¶�ȡ���ã����ߺͶ��б�����</p>
<section><h2>��ʽһ����¼��ȡ Cookie</h2><p>�����ȡ��֤�룬������д���˺ź����벻���档</p>
<button id="begin">��ȡ��ˢ����֤��</button><img id="captcha" alt="���Ȼ�ȡ��֤��" hidden>
<form id="login"><label>�û���������<input id="username" autocomplete="username" required></label><label>����<input id="password" type="password" autocomplete="current-password" required></label><label>ͼ����֤��<input id="code" autocomplete="off" required></label><button id="submit">��¼����֤��Ϣ��ȡ</button></form></section>
<section><h2>��ʽ���������ѵ�¼�� Cookie</h2><label>ѡ�� cookies.txt<input id="file" type="file" accept=".txt"></label><small>��������ύ yyscha �����ļ�¼�����ύ������վ Cookie��</small><p>Ҳ��ֱ��ճ������ͷ Cookie ��ֵ��</p><textarea id="cookie" rows="3" spellcheck="false"></textarea><button id="import">��֤������ Cookie</button></section>
<section><h2>���</h2><div id="result" role="status">��δ��������Ҫ�ڷ���Ⱥ��ճ�� Cookie �����롣</div></section>
<script>
const result=document.getElementById('result');let busy=false;
async function call(action,body){if(busy)throw Error('��ȴ���ǰ�������');busy=true;result.textContent='�����С�';try{const r=await fetch(location.pathname+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.message||'����ʧ��');result.textContent=d.message;return d;}catch(e){result.textContent=e.message;throw e;}finally{busy=false;}}
document.getElementById('begin').onclick=async()=>{try{const d=await call('begin',{});const img=document.getElementById('captcha');img.src=d.image;img.hidden=false;document.getElementById('code').value='';}catch(e){}};
document.getElementById('login').onsubmit=async e=>{e.preventDefault();try{await call('login',{username:document.getElementById('username').value,password:document.getElementById('password').value,captcha:document.getElementById('code').value});}catch(e){}finally{document.getElementById('password').value='';document.getElementById('code').value='';}};
document.getElementById('import').onclick=async()=>{try{const file=document.getElementById('file').files[0];const cookie=document.getElementById('cookie').value.trim();if(cookie){await call('import',{cookie});}else if(file){if(file.size>5242880)throw Error('�ļ�����');const rows=(await file.text()).split(/\\r?\\n/).filter(line=>{const domain=line.replace(/^#HttpOnly_/,'').split('\\t')[0].replace(/^\\./,'').toLowerCase();return domain==='www.yyscha.cn'||domain==='yyscha.cn';});await call('import',{text:rows.join('\\n')});}else throw Error('��ѡ���ļ���ճ��Cookie');document.getElementById('cookie').value='';document.getElementById('file').value='';}catch(e){result.textContent=e.message;}};
</script></html>'''


def make_handler(config_path, token):
    route = '/' + token + '/'
    class Handler(BaseHTTPRequestHandler):
        flow = None

        def log_message(self, *args):
            pass

        def respond(self, status, payload, content='application/json; charset=utf-8'):
            data = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, dict) else payload
            self.send_response(status)
            for key, value in {'Content-Type': content, 'Content-Length': str(len(data)), 'Cache-Control': 'no-store',
                               'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                               'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'"}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            return self.headers.get('Host') == '127.0.0.1:' + str(self.server.server_port)

        def do_GET(self):
            if not self.valid_host() or self.path != route:
                self.respond(404, {'message': 'Not found'})
                return
            self.respond(200, HTML.encode(), 'text/html; charset=utf-8')

        def do_POST(self):
            expected = 'http://127.0.0.1:' + str(self.server.server_port)
            action = self.path[len(route):] if self.path.startswith(route) else ''
            if not self.valid_host() or self.headers.get('Origin') != expected or action not in ('begin', 'login', 'import'):
                self.respond(403, {'message': 'Forbidden'})
                return
            try:
                self.connection.settimeout(20)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise r.Failure('�����С��Ч')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise r.Failure('�����ʽ��Ч')
                if action == 'begin':
                    if Handler.flow:
                        Handler.flow.close()
                    Handler.flow = LoginFlow(config_path)
                    data, mime = Handler.flow.begin()
                    self.respond(200, {'message': '��֤���ѻ�ȡ��5��������д���ύ��', 'image': 'data:' + mime + ';base64,' + base64.b64encode(data).decode()})
                elif action == 'login':
                    if not Handler.flow:
                        raise r.Failure('���Ȼ�ȡ��֤��')
                    try:
                        Handler.flow.submit(str(payload.get('username', '')), str(payload.get('password', '')), str(payload.get('captcha', '')))
                    finally:
                        Handler.flow.close()
                        Handler.flow = None
                    self.respond(200, {'message': '��¼����Ϣ��ȡ��֤�ɹ����Ự�Ѹ��£��°���������Զ�ʹ�ã����������л��ߡ�'})
                else:
                    raw = payload.get('cookie', '')
                    if not raw:
                        raw, _ = parse_cookie_text(str(payload.get('text', '')))
                    import_cookie(config_path, str(raw))
                    self.respond(200, {'message': '��Ϣ��ȡ��֤�ɹ���Cookie�Ѹ��£��°���������Զ�ʹ�á�'})
            except (r.Failure, ValueError) as exc:
                # ValueErrors from imported data are fixed parser messages; JSON
                # decoder errors must not echo a document or credentials.
                message = str(exc) if isinstance(exc, r.Failure) else 'Cookie�ı��������ʽ��Ч�������ļ�����Ч�ڡ�'
                self.respond(400, {'message': message})
            except Exception:
                self.respond(500, {'message': '���ػָ�����ʧ�ܣ�δ���ƾ�ݣ��������á��������̡�'})
    return Handler, route


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=r.PRIVATE / 'config.json')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--open-browser', action='store_true')
    args = parser.parse_args()
    r.load_config(args.config, allow_missing_cookie=True)
    token = secrets.token_urlsafe(24)
    handler, route = make_handler(args.config, token)
    server = HTTPServer(('127.0.0.1', args.port), handler)
    url = 'http://127.0.0.1:' + str(server.server_port) + route
    print('�ָ���ҳ���ڱ����ػ����ţ��رմ��ں�ֹͣ��������ʹ��ͬ�˿�SSHת����')
    print(url)
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if handler.flow:
            handler.flow.close()
        server.server_close()


if __name__ == '__main__':
    try:
        main()
    except r.Failure as exc:
        print(str(exc))
        raise SystemExit(1)
    except Exception:
        print('�ָ��������ʧ�ܣ��������û�˿�ռ�á�')
        raise SystemExit(1)

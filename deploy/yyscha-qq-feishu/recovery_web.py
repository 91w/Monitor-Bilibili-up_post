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

HTML = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>网页监听登录恢复</title>
<style>body{font:16px system-ui;max-width:700px;margin:40px auto;padding:0 20px;background:#f5f7fa;color:#203040}section{background:white;padding:24px;margin:20px 0;border-radius:12px}input,textarea,button{font:inherit;box-sizing:border-box;padding:10px;margin:6px 0;max-width:100%}input,textarea{width:100%}button{cursor:pointer}#captcha{display:block;margin:12px 0;max-width:250px}#result{white-space:pre-wrap}small{color:#546575}</style>
<h1>网页监听登录恢复</h1><p>保存成功后，运行中的新版监听会自动重新读取配置，基线和队列保留。</p>
<section><h2>方式一：登录获取 Cookie</h2><p>程序获取验证码，请你填写；账号和密码不保存。</p>
<button id="begin">获取／刷新验证码</button><img id="captcha" alt="请先获取验证码" hidden>
<form id="login"><label>用户名／邮箱<input id="username" autocomplete="username" required></label><label>密码<input id="password" type="password" autocomplete="current-password" required></label><label>图形验证码<input id="code" autocomplete="off" required></label><button id="submit">登录并验证消息读取</button></form></section>
<section><h2>方式二：导入已登录的 Cookie</h2><label>选择 cookies.txt<input id="file" type="file" accept=".txt"></label><small>浏览器仅提交 yyscha 域名的记录，不提交其他网站 Cookie。</small><p>也可直接粘贴请求头 Cookie 的值：</p><textarea id="cookie" rows="3" spellcheck="false"></textarea><button id="import">验证并更新 Cookie</button></section>
<section><h2>结果</h2><div id="result" role="status">尚未操作。不要在飞书群里粘贴 Cookie 或密码。</div></section>
<script>
const result=document.getElementById('result');let busy=false;
async function call(action,body){if(busy)throw Error('请等待当前操作完成');busy=true;result.textContent='处理中…';try{const r=await fetch(location.pathname+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.message||'操作失败');result.textContent=d.message;return d;}catch(e){result.textContent=e.message;throw e;}finally{busy=false;}}
document.getElementById('begin').onclick=async()=>{try{const d=await call('begin',{});const img=document.getElementById('captcha');img.src=d.image;img.hidden=false;document.getElementById('code').value='';}catch(e){}};
document.getElementById('login').onsubmit=async e=>{e.preventDefault();try{await call('login',{username:document.getElementById('username').value,password:document.getElementById('password').value,captcha:document.getElementById('code').value});}catch(e){}finally{document.getElementById('password').value='';document.getElementById('code').value='';}};
document.getElementById('import').onclick=async()=>{try{const file=document.getElementById('file').files[0];const cookie=document.getElementById('cookie').value.trim();if(cookie){await call('import',{cookie});}else if(file){if(file.size>5242880)throw Error('文件过大');const rows=(await file.text()).split(/\\r?\\n/).filter(line=>{const domain=line.replace(/^#HttpOnly_/,'').split('\\t')[0].replace(/^\\./,'').toLowerCase();return domain==='www.yyscha.cn'||domain==='yyscha.cn';});await call('import',{text:rows.join('\\n')});}else throw Error('请选择文件或粘贴Cookie');document.getElementById('cookie').value='';document.getElementById('file').value='';}catch(e){result.textContent=e.message;}};
</script></html>'''

RELAY_HTML = '''<section><h2>同步到家中开发板</h2><p>先完成上方登录或导入，再点击加密上传。上传完成后等待开发板验证；两种状态分别显示。</p><button id="relay-upload">验证并加密上传</button><button id="relay-status">查询／重试同步</button><div id="relay-result" role="status">尚未查询。</div></section>
<script>
async function relayCall(action){const box=document.getElementById('relay-result');box.textContent='处理中…';try{const d=await call(action,{});box.textContent=d.message;}catch(e){box.textContent=e.message;}}
document.getElementById('relay-upload').onclick=()=>relayCall('relay-upload');
document.getElementById('relay-status').onclick=()=>relayCall('relay-status');
</script>'''


def desktop_paired(config_path):
    try:
        root = Path(config_path).parent
        return (json.loads((root / 'relay-identity.json').read_text())['role'] == 'desktop'
                and (root / 'relay-peer.json').is_file() and (root / 'cos-uploader.json').is_file())
    except Exception:
        return False


def relay_result(config_path, upload=False):
    from relay_updates import DesktopUpdates, enqueue_current
    from cos_transport import CloudError
    if not desktop_paired(config_path):
        raise r.Failure('本机尚未完成云端配置与开发板配对')
    if upload:
        state = enqueue_current(config_path)
    else:
        updates = DesktopUpdates(Path(config_path).parent)
        try:
            state = updates.synchronize()
        except CloudError:
            state = updates.status()
            return '云端状态查询暂未完成；本机记录：' + str(state.get('state', 'unknown')) + '。请稍后重试。'
        finally:
            updates.close()
    labels = {'empty': '尚无更新包', 'queued': '已加密保存在本机，等待重试上传',
              'uploaded': '密文已上传，等待开发板验证与应用',
              'applied': '开发板已确认验证并应用，会话已同步',
              'rejected': '开发板拒绝此更新，旧会话和队列保留；请重新登录或导入后生成新包',
              'expired': '未取得应用回执，传输有效期已过；实际板端状态需复核，请重新验证并上传'}
    return labels.get(state['state'], '状态暂不可用') + (f"（版本 {state['version']}）" if 'version' in state else '')


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
            page = HTML.replace('</html>', RELAY_HTML + '</html>') if desktop_paired(config_path) else HTML
            self.respond(200, page.encode(), 'text/html; charset=utf-8')

        def do_POST(self):
            expected = 'http://127.0.0.1:' + str(self.server.server_port)
            action = self.path[len(route):] if self.path.startswith(route) else ''
            if not self.valid_host() or self.headers.get('Origin') != expected or action not in ('begin', 'login', 'import', 'relay-upload', 'relay-status'):
                self.respond(403, {'message': 'Forbidden'})
                return
            try:
                self.connection.settimeout(20)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise r.Failure('请求大小无效')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise r.Failure('请求格式无效')
                if action in ('relay-upload', 'relay-status'):
                    self.respond(200, {'message': relay_result(config_path, upload=action == 'relay-upload')})
                elif action == 'begin':
                    if Handler.flow:
                        Handler.flow.close()
                    Handler.flow = LoginFlow(config_path)
                    data, mime = Handler.flow.begin()
                    self.respond(200, {'message': '验证码已获取，5分钟内填写并提交。', 'image': 'data:' + mime + ';base64,' + base64.b64encode(data).decode()})
                elif action == 'login':
                    if not Handler.flow:
                        raise r.Failure('请先获取验证码')
                    try:
                        Handler.flow.submit(str(payload.get('username', '')), str(payload.get('password', '')), str(payload.get('captcha', '')))
                    finally:
                        Handler.flow.close()
                        Handler.flow = None
                    self.respond(200, {'message': '登录及消息读取验证成功，会话已更新；新版监听下轮自动使用，不补发已有基线。'})
                else:
                    raw = payload.get('cookie', '')
                    if not raw:
                        raw, _ = parse_cookie_text(str(payload.get('text', '')))
                    import_cookie(config_path, str(raw))
                    self.respond(200, {'message': '消息读取验证成功，Cookie已更新；新版监听下轮自动使用。'})
            except (r.Failure, ValueError) as exc:
                # ValueErrors from imported data are fixed parser messages; JSON
                # decoder errors must not echo a document or credentials.
                message = str(exc) if isinstance(exc, r.Failure) else 'Cookie文本或请求格式无效，请检查文件及有效期。'
                self.respond(400, {'message': message})
            except Exception:
                self.respond(500, {'message': '本地恢复操作失败，未输出凭据；请检查配置、网络或磁盘。'})
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
    print('恢复网页仅在本机回环开放，关闭窗口后停止。开发板使用同端口SSH转发。')
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
        print('恢复入口启动失败，请检查配置或端口占用。')
        raise SystemExit(1)

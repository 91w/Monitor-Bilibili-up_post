"""Human-assisted login and verified cookie import for desktop or headless H616."""
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request
from urllib.error import HTTPError, URLError

import relay as r
from site_session import SiteSession, encode_jar, fingerprint


def isolated_config(config, session, raw_cookie):
    candidate = {k: v for k, v in config.items() if not k.startswith('_')}
    candidate.update(site_cookie=raw_cookie, _site_session=session)
    return candidate


def verify_session(config, session, raw_cookie):
    candidate = isolated_config(config, session, raw_cookie)
    r.ensure_time(candidate)
    day = r.datetime.now(r.TZ).date().isoformat()
    # A successful, source-validated message response is required, rather than
    # treating login JSON or an HTTP 200 page as sufficient evidence.
    for uid in config['uids']:
        r.Source(candidate).page(day, uid, 0)


def promote_session(config_path, candidate, relay_marker=None):
    path = Path(config_path)
    raw = candidate.cookie_header()
    if not raw:
        raise r.Failure('验证后的会话没有可用于消息接口的Cookie，未替换配置')
    path.parent.mkdir(parents=True, exist_ok=True)
    destination = SiteSession(raw, path.parent / 'site-session.sqlite3')
    temp = path.with_suffix('.auth-new')
    try:
        destination.db.execute('BEGIN IMMEDIATE')
        current = json.loads(path.read_text(encoding='utf-8-sig'))
        current['site_cookie'] = raw
        if relay_marker is not None:
            current['relay_update'] = relay_marker
        # Hold the session DB lock through the atomic config replacement; a
        # concurrently polling process cannot reset the jar to the old seed.
        destination.db.execute('INSERT OR REPLACE INTO session VALUES(1,?,?,?)',
                               (fingerprint(raw), json.dumps(encode_jar(candidate.jar)), time.time()))
        with temp.open('w', encoding='utf-8') as handle:
            json.dump(current, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != 'nt':
            os.chmod(temp, 0o600)
        temp.replace(path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        destination.db.commit()
    except Exception:
        destination.db.rollback()
        temp.unlink(missing_ok=True)
        raise
    finally:
        destination.close()


def import_cookie(config_path, raw_cookie):
    config = r.load_config(config_path, allow_missing_cookie=True)
    raw_cookie = raw_cookie.strip()
    if raw_cookie.lower().startswith('cookie:'):
        raw_cookie = raw_cookie[7:].strip()
    if '=' not in raw_cookie or '\r' in raw_cookie or '\n' in raw_cookie:
        raise r.Failure('Cookie格式无效，原配置未修改')
    session = SiteSession(raw_cookie)  # Verify in isolation; failed imports do not touch the live jar.
    try:
        verify_session(config, session, raw_cookie)
        promote_session(config_path, session)
    finally:
        session.close()


class LoginFlow:
    def __init__(self, config_path):
        self.config_path = Path(config_path)
        self.config = r.load_config(config_path, allow_missing_cookie=True)
        self.session = SiteSession('')  # New anonymous challenge session, isolated from current login.
        self.csrf = None
        self.deadline = 0
        self.attempted = False

    def close(self):
        self.session.close()

    def begin(self):
        r.ensure_time(self.config)
        try:
            with self.session.open(r.SITE + '/ailogin/modal.php') as response:
                raw = response.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise r.Failure('登录表单过大')
            dom = r.DOM(raw.decode('utf-8')).root
            field = dom.find(lambda n: n.tag == 'input' and n.attrs.get('name') == 'csrf_token')
            self.csrf = field.attrs.get('value') if field else None
            if not self.csrf:
                raise r.Failure('登录表单没有CSRF字段，网站流程可能已变化')
            with self.session.open(r.SITE + '/ailogin/captcha.php?t=' + str(int(time.time()))) as response:
                data = response.read(512 * 1024 + 1)
            from media_feishu import image_mime
            mime = image_mime(data)
            if not mime or len(data) > 512 * 1024:
                raise r.Failure('验证码接口未返回支持的图片')
            self.deadline = time.monotonic() + 300
            return data, mime
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError):
            raise r.Failure('获取登录表单或验证码失败，请稍后重试') from None

    def submit(self, username, password, captcha):
        if self.attempted or not self.csrf or time.monotonic() > self.deadline:
            raise r.Failure('请先重新获取验证码，再提交登录')
        if not username.strip() or not password or not captcha.strip():
            raise r.Failure('请填写账号、密码和图形验证码')
        self.attempted = True
        r.ensure_time(self.config)
        form = urlencode({'action': 'login', 'username': username.strip(), 'password': password,
                          'captcha': captcha.strip(), 'remember_me': '1', 'csrf_token': self.csrf}).encode()
        req = Request(r.SITE + '/ailogin/api.php', data=form, headers={
            'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF-Token': self.csrf,
            'Origin': r.SITE, 'Referer': r.SITE + '/application/QQ/'})
        try:
            with self.session.open(req) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise r.Failure('登录响应过大')
            result = json.loads(raw)
            if not isinstance(result, dict) or type(result.get('code')) is not int or result['code'] != 1:
                raise r.Failure('登录未通过，请检查账号密码并重新获取验证码；原配置未修改')
            verify_session(self.config, self.session, '')
            promote_session(self.config_path, self.session)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            raise r.Failure('登录请求或响应失败，请重新获取验证码；原配置未修改') from None

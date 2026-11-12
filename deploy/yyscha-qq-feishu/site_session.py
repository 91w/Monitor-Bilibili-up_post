"""Persistent, source-scoped cookie jar. Values never belong in logs or outputs."""
import hashlib
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from http.cookies import SimpleCookie
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlparse
from urllib.request import Request, build_opener, ProxyHandler

FIELDS = ('version', 'name', 'value', 'port', 'port_specified', 'domain', 'domain_specified', 'domain_initial_dot', 'path', 'path_specified', 'secure', 'expires', 'discard', 'comment', 'comment_url')


def fingerprint(cookie):
    return hashlib.sha256(cookie.encode()).hexdigest()


def encode_jar(jar):
    return [{**{k: getattr(c, k) for k in FIELDS}, 'rest': c._rest} for c in jar]


def decode_jar(records):
    jar = CookieJar(policy=DefaultCookiePolicy(strict_domain=True))
    for record in records:
        if record.get('domain', '').lstrip('.') not in ('www.yyscha.cn', 'yyscha.cn'):
            continue
        jar.set_cookie(Cookie(**record))
    return jar


def seed_jar(header):
    parsed = SimpleCookie()
    parsed.load(header)
    jar = decode_jar([])
    for name, record in parsed.items():
        jar.set_cookie(Cookie(0, name, record.value, None, False, 'www.yyscha.cn', False, False,
                             '/', True, True, None, True, None, None, {'imported_header': True}))
    return jar


class SiteSession:
    def __init__(self, raw_cookie, state_path=None, config_path=None):
        self.source_hash = fingerprint(raw_cookie)
        self.raw_cookie = raw_cookie
        self.state_path = Path(state_path) if state_path else None
        self.config_path = Path(config_path) if config_path else None
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.state_path) if self.state_path else ':memory:', timeout=30)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS session(id INTEGER PRIMARY KEY,source_hash TEXT,cookies TEXT,updated REAL)')
        self.db.commit()
        self.jar = seed_jar(raw_cookie)

    def close(self):
        self.db.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _load(self):
        row = self.db.execute('SELECT source_hash,cookies FROM session WHERE id=1').fetchone()
        if self.config_path:
            latest = json.loads(self.config_path.read_text(encoding='utf-8-sig')).get('site_cookie', '')
            if fingerprint(latest) != self.source_hash:
                self.raw_cookie, self.source_hash = latest, fingerprint(latest)
        self.jar = decode_jar(json.loads(row[1])) if row and row[0] == self.source_hash else seed_jar(self.raw_cookie)
        self.jar.clear_expired_cookies()

    def _save(self):
        self.db.execute('INSERT OR REPLACE INTO session VALUES(1,?,?,?)',
                        (self.source_hash, json.dumps(encode_jar(self.jar)), time.time()))

    def open(self, request, timeout=20):
        import relay as r
        req = Request(request) if isinstance(request, str) else request
        target = urlparse(req.full_url)
        if target.scheme != 'https' or target.netloc != 'www.yyscha.cn':
            raise r.Failure('会话请求必须指向已授权的yyscha HTTPS站点')
        # Serialize the load/request/save sequence so concurrent reader/recovery
        # processes cannot overwrite a newly rotated session with an older copy.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._load()
            req.remove_header('Cookie')
            self.jar.add_cookie_header(req)
            response = build_opener(ProxyHandler({}), r.NoRedirect).open(req, timeout=timeout)
            self.jar.extract_cookies(response, req)
            # A response-scoped cookie supersedes a same-name seed whose original
            # browser domain/path metadata was unavailable in the imported header.
            replacements = {c.name for c in self.jar if not c._rest.get('imported_header')}
            for header in response.headers.get_all('Set-Cookie', []):
                received = SimpleCookie()
                received.load(header)
                replacements.update(received.keys())
            for cookie in list(self.jar):
                if cookie._rest.get('imported_header') and cookie.name in replacements:
                    self.jar.clear(cookie.domain, cookie.path, cookie.name)
            self._save()
            self.db.commit()
            return response
        except Exception:
            self.db.rollback()
            raise

    def cookie_header(self):
        self._load()
        req = Request('https://www.yyscha.cn/application/QQ/api.php')
        self.jar.add_cookie_header(req)
        return req.get_header('Cookie', '')

    def status(self):
        self._load()
        cookies = list(self.jar)
        return {'cookie_count': len(cookies), 'persistent': self.state_path is not None,
                'expiry_timestamps': sorted({c.expires for c in cookies if c.expires is not None}),
                'remember_present': any('remember' in c.name.lower() for c in cookies)}


def get_session(config):
    current = config.get('_site_session')
    raw = config.get('site_cookie', '')
    config_path = config.get('_config_path')
    path = Path(config_path).parent / 'site-session.sqlite3' if config_path else None
    if current is None or current.source_hash != fingerprint(raw) or current.state_path != path:
        if current:
            current.close()
        current = config['_site_session'] = SiteSession(raw, path, config_path)
    return current

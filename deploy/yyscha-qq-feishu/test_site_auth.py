import io
import json
from email.message import Message
from pathlib import Path
import shutil
import time
import unittest
import uuid
from unittest.mock import Mock, patch
from urllib.parse import parse_qs
from urllib.request import Request

import relay as r
from site_session import SiteSession, get_session
from auth_recovery import LoginFlow, promote_session, import_cookie
from recovery_web import make_handler, HTML


class Response(io.BytesIO):
    def __init__(self, data=b'{}', cookies=()):
        super().__init__(data)
        self.headers = Message()
        for cookie in cookies:
            self.headers.add_header('Set-Cookie', cookie)

    def info(self):
        return self.headers


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / '.test-work' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.path = self.root / 'config.json'
        self.base = {'site_cookie': 'PHPSESSID=old; remember_me=remember', 'uids': [r.UIDS[0]], 'feishu_webhook': '', 'feishu_app_secret': 'preserve-app-secret'}
        self.path.write_text(json.dumps(self.base), encoding='utf-8')
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()
        assert self.root.resolve().is_relative_to((Path(__file__).parent / '.test-work').resolve())
        shutil.rmtree(self.root)

    def session(self, cookie=None, persistent=True):
        session = SiteSession(cookie or self.base['site_cookie'], self.root / 'site-session.sqlite3' if persistent else None,
                              self.path if persistent else None)
        self.sessions.append(session)
        return session

    def open(self, session, cookies=(), data=b'{}'):
        opener = Mock()
        opener.open.return_value = Response(data, cookies)
        with patch('site_session.build_opener', return_value=opener):
            with session.open(Request('https://www.yyscha.cn/application/QQ/api.php')) as response:
                response.read()
        return opener.open.call_args.args[0]

    def test_set_cookie_expiry_and_restart_are_preserved(self):
        session = self.session()
        self.open(session, ['PHPSESSID=renewed; Path=/; Max-Age=604800; Secure; HttpOnly'])
        another = self.session()
        header = another.cookie_header()
        self.assertIn('PHPSESSID=renewed', header)
        self.assertNotIn('PHPSESSID=old', header)
        self.assertIn('remember_me=remember', header)
        cookie = next(c for c in another.jar if c.name == 'PHPSESSID')
        self.assertAlmostEqual(cookie.expires - time.time(), 604800, delta=3)
        self.assertIn('HttpOnly', cookie._rest)

    def test_seed_is_replaced_for_domain_cookie(self):
        session = self.session()
        self.open(session, ['PHPSESSID=new; Domain=.yyscha.cn; Path=/; Secure'])
        self.assertEqual(session.cookie_header().count('PHPSESSID='), 1)
        self.assertIn('PHPSESSID=new', session.cookie_header())

    def test_deleted_cookie_does_not_reappear_from_header(self):
        session = self.session()
        self.open(session, ['PHPSESSID=; Domain=.yyscha.cn; Path=/; Max-Age=0; Secure'])
        self.assertNotIn('PHPSESSID=', session.cookie_header())
        self.assertIn('remember_me=remember', session.cookie_header())

    def test_cookie_jar_cannot_be_sent_to_foreign_host(self):
        session = self.session()
        with patch('site_session.build_opener') as opener, self.assertRaises(r.Failure):
            session.open('https://evil.test/')
        opener.assert_not_called()

    def test_static_request_cookie_is_replaced_by_renewed_jar(self):
        session = self.session()
        self.open(session, ['PHPSESSID=new; Path=/; Secure'])
        opener = Mock()
        opener.open.return_value = Response()
        with patch('site_session.build_opener', return_value=opener):
            with session.open(Request('https://www.yyscha.cn/application/QQ/api.php', headers={'Cookie': 'PHPSESSID=stale'})):
                pass
        self.assertIn('PHPSESSID=new', opener.open.call_args.args[0].get_header('Cookie'))

    def test_config_hot_reload_replaces_import_seed(self):
        session = self.session()
        self.open(session)
        new = dict(self.base, site_cookie='PHPSESSID=manual-new')
        self.path.write_text(json.dumps(new))
        req = self.open(session)
        self.assertEqual(req.get_header('Cookie'), 'PHPSESSID=manual-new')

    def test_promote_keeps_other_settings_and_stale_worker_uses_new_jar(self):
        worker = self.session()
        self.open(worker)
        candidate = self.session('PHPSESSID=verified', persistent=False)
        self.open(candidate, ['PHPSESSID=rotated-verified; Path=/; Secure; Max-Age=604800'])
        promote_session(self.path, candidate)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['feishu_app_secret'], 'preserve-app-secret')
        self.assertIn('rotated-verified', saved['site_cookie'])
        req = self.open(worker)
        self.assertIn('PHPSESSID=rotated-verified', req.get_header('Cookie'))

    def test_failed_candidate_verification_keeps_original_config(self):
        before = self.path.read_bytes()
        with patch('auth_recovery.verify_session', side_effect=r.AuthRequired('invalid')), self.assertRaises(r.AuthRequired):
            import_cookie(self.path, 'PHPSESSID=bad')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / 'site-session.sqlite3').exists())

    def test_login_begin_uses_same_anonymous_cookie_session(self):
        flow = LoginFlow(self.path)
        self.sessions.append(flow.session)
        modal = Response(b'<input name="csrf_token" value="local-csrf">')
        captcha = Response(b'\x89PNG\r\n\x1a\npayload')
        with patch.object(flow.session, 'open', side_effect=[modal, captcha]) as request:
            image, mime = flow.begin()
        self.assertEqual(mime, 'image/png')
        self.assertEqual(request.call_count, 2)
        self.assertEqual(flow.csrf, 'local-csrf')
        self.assertGreater(flow.deadline, time.monotonic())
        self.assertNotIn('password', json.loads(self.path.read_text()))

    def test_login_success_requires_business_and_message_verification(self):
        flow = LoginFlow(self.path)
        self.sessions.append(flow.session)
        flow.csrf, flow.deadline = 'local-csrf', time.monotonic()+60
        with patch.object(flow.session, 'open', return_value=Response(b'{"code":1}')) as request, patch('auth_recovery.verify_session') as verify, patch('auth_recovery.promote_session') as promote:
            flow.submit('local-user', 'local-password', 'ABCD')
        req = request.call_args.args[0]
        form = parse_qs(req.data.decode())
        self.assertEqual(form['action'], ['login'])
        self.assertEqual(form['remember_me'], ['1'])
        self.assertEqual(req.get_header('X-csrf-token'), 'local-csrf')
        verify.assert_called_once()
        promote.assert_called_once()
        self.assertNotIn('local-password', self.path.read_text())

    def test_bad_or_used_captcha_never_replaces_current_login(self):
        flow = LoginFlow(self.path)
        self.sessions.append(flow.session)
        flow.csrf, flow.deadline = 'local-csrf', time.monotonic()+60
        with patch.object(flow.session, 'open', return_value=Response(b'{"code":0}')), patch('auth_recovery.promote_session') as promote:
            with self.assertRaises(r.Failure):
                flow.submit('u', 'p', 'wrong')
            with self.assertRaises(r.Failure):
                flow.submit('u', 'p', 'again')
        promote.assert_not_called()

    def test_expired_challenge_never_submits_password(self):
        flow = LoginFlow(self.path)
        self.sessions.append(flow.session)
        flow.csrf, flow.deadline = 'local-csrf', time.monotonic()-1
        with patch.object(flow.session, 'open') as request, self.assertRaises(r.Failure):
            flow.submit('u', 'p', 'ABCD')
        request.assert_not_called()

    def handler(self, path, origin, body=b'{}', host='127.0.0.1:8766'):
        cls, route = make_handler(self.path, 'local-token')
        handler = cls.__new__(cls)
        handler.path = path
        handler.headers = {'Host': host, 'Origin': origin, 'Content-Length': str(len(body))}
        handler.server = Mock(server_port=8766)
        handler.connection = Mock()
        handler.rfile = io.BytesIO(body)
        handler.respond = Mock()
        return handler

    def test_recovery_rejects_foreign_origin_and_wrong_host(self):
        for host, origin in [('127.0.0.1:8766','https://evil.test'), ('evil.test','http://127.0.0.1:8766')]:
            handler = self.handler('/local-token/import', origin, host=host)
            with patch('recovery_web.import_cookie') as update:
                handler.do_POST()
            update.assert_not_called()
            self.assertEqual(handler.respond.call_args.args[0], 403)

    def test_recovery_requires_random_route(self):
        handler = self.handler('/wrong-token/import', 'http://127.0.0.1:8766')
        handler.do_POST()
        self.assertEqual(handler.respond.call_args.args[0], 403)

    def test_web_import_is_verified_and_never_echoed(self):
        handler = self.handler('/local-token/import', 'http://127.0.0.1:8766', json.dumps({'cookie':'PHPSESSID=private'}).encode())
        with patch('recovery_web.import_cookie') as update:
            handler.do_POST()
        update.assert_called_once_with(self.path, 'PHPSESSID=private')
        self.assertNotIn('private', str(handler.respond.call_args))
        self.assertEqual(handler.respond.call_args.args[0], 200)

    def test_auth_failure_is_distinct_from_general_business_error(self):
        source = r.Source(dict(self.base))
        with patch('relay.request_json', return_value={'ok':False,'msg':'请先登录'}), self.assertRaises(r.AuthRequired):
            source.page('2026-11-12', r.UIDS[0], 0)
        with patch('relay.request_json', return_value={'ok':False,'msg':'maintenance'}), self.assertRaises(r.Failure) as error:
            source.page('2026-11-12', r.UIDS[0], 0)
        self.assertNotIsInstance(error.exception, r.AuthRequired)
        get_session(source.config).close()

    def test_recovery_can_start_before_first_cookie_import(self):
        self.path.write_text(json.dumps(dict(self.base, site_cookie='')))
        flow = LoginFlow(self.path)
        self.sessions.append(flow.session)
        self.assertEqual(flow.config['site_cookie'], '')
        with self.assertRaises(r.Failure):
            r.load_config(self.path)


if __name__ == '__main__':
    unittest.main()

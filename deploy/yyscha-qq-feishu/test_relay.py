import copy
import json
import shutil
import uuid
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import relay as r

UID = r.UIDS[0]
DAY = '2026-11-12'


def item(i, **kwargs):
    return dict(id=f'msg-{i}', uid=UID, name='目标', ts=i * 1000, text=f'正文{i}', **kwargs)


class FakeSource:
    def __init__(self):
        self.days, self.calls = {}, []
        self.fail = None

    def page(self, day, uid, offset):
        self.calls.append((day, uid, offset))
        if self.fail == offset:
            raise r.Failure('模拟断网')
        items = self.days.get(day, [])
        return {'ok': True, 'day': {'key': day, 'chat_key': 'group'}, 'offset': offset, 'total': len(items), 'items': copy.deepcopy(items[offset:offset+60])}


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.testdir = Path(__file__).parent / '.test-work' / uuid.uuid4().hex
        self.testdir.mkdir(parents=True)
        self.path = self.testdir / 'state.sqlite3'
        self.db = r.connect(self.path)
        self.source = FakeSource()
        self.config = {'uids': [UID]}
        self.now = datetime(2026, 11, 12, 12, tzinfo=r.TZ)

    def tearDown(self):
        self.db.close()
        assert self.testdir.resolve().is_relative_to((Path(__file__).parent / '.test-work').resolve())
        shutil.rmtree(self.testdir)

    def status(self, status):
        return self.db.execute('SELECT count(*) FROM messages WHERE status=?', (status,)).fetchone()[0]

    def test_baseline_then_burst_all_pages(self):
        self.source.days[DAY] = [item(i) for i in range(155)]
        r.poll(self.db, self.source, self.config, self.now)
        self.assertEqual(self.status('baseline'), 155)
        self.assertEqual(self.status('pending'), 0)
        self.source.days[DAY].extend(item(i) for i in range(155, 400))
        r.poll(self.db, self.source, self.config, self.now)
        self.assertEqual(self.status('pending'), 245)
        self.assertIn((DAY, UID, 335), self.source.calls)

    def test_failure_does_not_advance_or_partially_enqueue(self):
        self.source.days[DAY] = [item(i) for i in range(130)]
        r.scan(self.db, self.source, DAY, UID, True, now=0)
        self.source.days[DAY].extend(item(i) for i in range(130, 300))
        self.source.fail = 130
        with self.assertRaises(r.Failure):
            r.scan(self.db, self.source, DAY, UID, now=1)
        self.assertEqual(self.status('pending'), 0)
        self.assertEqual(self.db.execute('SELECT total FROM streams').fetchone()[0], 130)
        self.source.fail = None
        r.scan(self.db, self.source, DAY, UID, now=2)
        self.assertEqual(self.status('pending'), 170)

    def test_restart_dedup_and_retry(self):
        self.source.days[DAY] = [item(1)]
        r.scan(self.db, self.source, DAY, UID)
        def fail(*_):
            raise r.Failure('模拟飞书失败')
        with patch('relay.time.sleep'):
            r.drain(self.db, {}, fail, now=100)
        self.assertEqual(self.status('retry'), 1)
        self.db.close()
        self.db = r.connect(self.path)
        r.scan(self.db, self.source, DAY, UID)
        sent = []
        with patch('relay.time.sleep'):
            r.drain(self.db, {}, lambda c, b: sent.append(b), now=114)
            self.assertEqual(sent, [])
            r.drain(self.db, {}, lambda c, b: sent.append(b), now=115)
            r.drain(self.db, {}, lambda c, b: sent.append(b), now=200)
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.status('sent'), 1)

    def test_midnight_and_previous_day_grace(self):
        self.source.days[DAY] = [item(1)]
        r.poll(self.db, self.source, self.config, self.now)
        self.source.days[DAY].append(item(2))
        self.source.days['2026-11-13'] = [item(3)]
        r.poll(self.db, self.source, self.config, self.now.replace(day=13, hour=0, minute=1))
        self.assertEqual(self.status('pending'), 2)
        self.source.calls.clear()
        r.poll(self.db, self.source, self.config, self.now.replace(day=13, hour=1))
        self.assertFalse(any(day == DAY for day, _, _ in self.source.calls))

    def test_multiday_offline_recovery(self):
        r.poll(self.db, self.source, self.config, self.now)
        for d, i in [('2026-11-12', 1), ('2026-11-13', 2), ('2026-11-14', 3)]:
            self.source.days[d] = [item(i)]
        r.poll(self.db, self.source, self.config, self.now + timedelta(days=2))
        self.assertEqual(self.status('pending'), 3)

    def test_crossday_duplicate_id(self):
        self.source.days[DAY] = [item(1)]
        self.source.days['2026-11-13'] = [item(1)]
        r.scan(self.db, self.source, DAY, UID)
        r.scan(self.db, self.source, '2026-11-13', UID)
        self.assertEqual(self.status('pending'), 1)

    def test_full_reconciliation_finds_insert_before_overlap(self):
        self.source.days[DAY] = [item(i) for i in range(200)]
        r.scan(self.db, self.source, DAY, UID, True, now=0)
        self.source.days[DAY].insert(0, item(999))
        r.scan(self.db, self.source, DAY, UID, now=3601)
        self.assertEqual(self.status('pending'), 1)

    def test_shrink_rescans(self):
        self.source.days[DAY] = [item(i) for i in range(200)]
        r.scan(self.db, self.source, DAY, UID, True, now=0)
        self.source.days[DAY] = [item(999)]
        r.scan(self.db, self.source, DAY, UID, now=1)
        self.assertEqual(self.status('pending'), 1)

    def test_empty_midpage_fails(self):
        self.source.days[DAY] = [item(i) for i in range(100)]
        original = self.source.page
        def empty(day, uid, offset):
            page = original(day, uid, offset)
            if offset:
                page['items'] = []
            return page
        self.source.page = empty
        with self.assertRaises(r.Failure):
            r.scan(self.db, self.source, DAY, UID)
        self.assertEqual(self.status('pending'), 0)

    def test_partial_initialization_remains_baseline(self):
        self.source.days[DAY] = [item(i) for i in range(100)]
        self.source.fail = 60
        with self.assertRaises(r.Failure):
            r.poll(self.db, self.source, self.config, self.now)
        self.source.fail = None
        r.poll(self.db, self.source, self.config, self.now)
        self.assertEqual(self.status('pending'), 0)
        self.assertEqual(self.status('baseline'), 100)

    def test_reply_body_mentions_and_media(self):
        html = '''<div class="content"><div class="reply-content" data-reply-to="old"><div class="reply-content-header"><strong>非目标</strong></div><span class="reply-content-time">09:22</span><div class="reply-content-text">@目标 问题<img src="x" onerror="bad()"></div></div><span>@非目标</span><span class="text-content"> 是</span><img src="secret" alt="图"><script>bad()</script></div>'''
        result = r.render(item(1, html=html), DAY)
        self.assertIn('引用 非目标', result)
        self.assertIn('回复：@非目标 是[图片：图]', result)
        self.assertEqual(result.count('问题'), 1)
        self.assertNotIn('bad()', result)
        self.assertNotIn('secret', result)

    def test_login_business_rejection_and_uid_mismatch(self):
        source = r.Source({'site_cookie': 'session=local'})
        valid = self.source.page(DAY, UID, 0)
        for response in [{'ok': False, 'items': []}, dict(valid, items=[dict(item(1), uid='other')]), dict(valid, offset=2), dict(valid, day={'key': 'bad', 'chat_key': 'g'})]:
            with patch('relay.request_json', return_value=response), self.assertRaises(r.Failure):
                source.page(DAY, UID, 0)

    def test_no_check_login_and_only_cookie_header(self):
        valid = self.source.page(DAY, UID, 0)
        with patch('relay.request_json', return_value=valid) as request:
            r.Source({'site_cookie': 'session=local'}).page(DAY, UID, 0)
            url, headers = request.call_args.args
            self.assertIn('action=day_messages', url)
            self.assertNotIn('check_login', url)
            self.assertEqual(headers, {'Cookie': 'session=local'})

    def test_feishu_business_success_required_and_signature(self):
        config = {'feishu_webhook': 'local', 'feishu_secret': 'secret', 'feishu_keyword': '提醒'}
        for response in [{}, {'code': 19024}, {'code': '0'}, {'code': False}]:
            with patch('relay.request_json', return_value=response), self.assertRaises(r.Failure):
                r.feishu(config, '正文')
        with patch('relay.request_json', return_value={'code': 0}) as request, patch('relay.time.time', return_value=100):
            r.feishu(config, '正文')
            payload = request.call_args.kwargs['payload']
            self.assertEqual(payload['sign'], r.feishu_signature('secret', 100))
            self.assertIn('提醒', payload['content']['text'])

    def test_config_rejects_foreign_webhook_and_header_injection(self):
        path = self.testdir / 'config.json'
        for config in [{'site_cookie': 'x=y\nInjected: 1', 'uids': [UID]}, {'site_cookie': 'x=y', 'uids': [UID], 'feishu_webhook': 'https://evil.test/hook'}]:
            path.write_text(json.dumps(config))
            with self.assertRaises(r.Failure):
                r.load_config(path)

    def test_timeguard_blocks_read_and_send(self):
        from timeguard import TimeTrustError
        with patch('timeguard.require_trusted_time', side_effect=TimeTrustError('stale')), self.assertRaises(r.Failure):
            r.poll(self.db, self.source, {'require_trusted_time': True})
        with patch('timeguard.require_trusted_time', side_effect=TimeTrustError('stale')), self.assertRaises(r.Failure):
            r.feishu({'require_trusted_time': True}, 'body')

    def test_redirect_never_forwards_cookie(self):
        with self.assertRaises(r.Failure):
            r.NoRedirect().redirect_request(None, None, None, None, None)

    def test_changed_uid_list_stops_without_backfill(self):
        r.poll(self.db, self.source, self.config, self.now)
        with self.assertRaises(r.Failure):
            r.poll(self.db, self.source, {'uids': [UID, r.UIDS[1]]}, self.now)

    def test_non_text_fallback_explains_media(self):
        self.assertIn('媒体或附件', r.render(item(1, type='image'), DAY))

    def test_scanned_total_growth_waits_until_next_poll(self):
        self.source.days[DAY] = [item(i) for i in range(100)]
        original = self.source.page
        def growing(day, uid, offset):
            if offset == 60 and len(self.source.days[day]) == 100:
                self.source.days[day].extend(item(i) for i in range(100, 140))
            return original(day, uid, offset)
        self.source.page = growing
        r.scan(self.db, self.source, DAY, UID)
        self.assertEqual(self.status('pending'), 100)
        r.scan(self.db, self.source, DAY, UID)
        self.assertEqual(self.status('pending'), 140)

    def test_chat_scope_change_stops(self):
        r.scan(self.db, self.source, DAY, UID)
        original = self.source.page
        def changed(day, uid, offset):
            data = original(day, uid, offset)
            data['day']['chat_key'] = 'another-group'
            return data
        self.source.page = changed
        with self.assertRaises(r.Failure):
            r.scan(self.db, self.source, DAY, UID)

    def test_long_feishu_text_is_bounded_without_reappending_last_line(self):
        body = '正文' * 20000 + '\nhttps://www.yyscha.cn/application/QQ/day.php?day=' + DAY
        with patch('relay.request_json', return_value={'code': 0}) as request:
            r.feishu({'feishu_webhook': 'local'}, body)
            text = request.call_args.kwargs['payload']['content']['text']
            self.assertLess(len(text.encode()), 16000)
            self.assertIn('已截断', text)
            self.assertTrue(text.endswith('[内容过长，已截断]'))
            self.assertNotIn('/application/QQ/day.php', text)

    def test_render_does_not_append_source_url(self):
        result = r.render(item(1, html='<div class="content">正常正文</div>'), DAY)
        self.assertIn('正常正文', result)
        self.assertNotIn('/application/QQ/day.php', result)

    def test_plain_message_uses_card_when_production_image_client_present(self):
        from unittest.mock import Mock
        self.source.days[DAY] = [item(1)]
        r.scan(self.db, self.source, DAY, UID)
        client, sender = Mock(), Mock()
        with patch('relay.time.sleep'):
            r.drain(self.db, self.config, sender=sender, now=100, image_client=client)
        sender.assert_not_called()
        client.send.assert_called_once()
        self.assertEqual(client.send.call_args.kwargs['parts']['sender_uid'], UID)
        self.assertEqual(self.status('sent'), 1)

    def test_user_authored_urls_are_preserved(self):
        url = r.SITE + '/application/QQ/day.php?day=' + DAY
        value = item(1, html='<div class="content">分享链接 ' + url + '</div>')
        body = r.render(value, DAY)
        self.assertEqual(body.count(url), 1)
        self.assertEqual(r.delivery_body(body, DAY, r.message_parts(value, DAY)), body)

    def test_legacy_footer_removal_keeps_user_original_url(self):
        url = r.SITE + '/application/QQ/day.php?day=' + DAY
        legacy = '标题\n正文原本分享 ' + url + '\n' + url
        self.assertEqual(r.delivery_body(legacy, DAY, {}), '标题\n正文原本分享 ' + url)

    def test_different_or_nontrailing_url_is_not_removed(self):
        body = '分享 https://example.com/\n后续正文'
        self.assertEqual(r.delivery_body(body, DAY, {}), body)

    def test_structured_quote_is_persisted_with_queue(self):
        self.source.days[DAY] = [item(1, html='<div class="content"><div class="reply-content"><div class="reply-content-header"><strong>其他人</strong></div><div class="reply-content-text">原话</div></div>回复正文</div>')]
        r.scan(self.db, self.source, DAY, UID)
        parts = json.loads(self.db.execute('SELECT parts_json FROM messages').fetchone()[0])
        self.assertEqual(parts['quote']['author'], '其他人')
        self.assertEqual(parts['quote']['text'], '原话')
        self.assertEqual(parts['body'], '回复正文')

    def test_quoted_text_uses_card_sender_without_images(self):
        self.source.days[DAY] = [item(1, html='<div class="content"><div class="reply-content"><div class="reply-content-text">原话</div></div>回复</div>')]
        r.scan(self.db, self.source, DAY, UID)
        from unittest.mock import Mock
        client, sender = Mock(), Mock()
        with patch('relay.time.sleep'):
            r.drain(self.db, {}, sender=sender, image_client=client)
        sender.assert_not_called()
        self.assertEqual(client.send.call_args.kwargs['parts']['quote']['text'], '原话')
        self.assertEqual(self.status('sent'), 1)


if __name__ == '__main__':
    unittest.main()

import io
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch
import relay as r
from media_feishu import ImageClient, extract_images, image_url, image_mime, stable_ref, refresh_if_expired, card_elements

URL = 'https://www.yyscha.cn/application/QQ/serve.php?record=one&path=pic.png&exp=9999999999&sig=temporary'


class Response(io.BytesIO):
    def __init__(self, data, mime='image/png'):
        super().__init__(data)
        self.headers = {'Content-Type': mime, 'Content-Length': str(len(data))}


class MediaTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.config = {'site_cookie': 'local=session', 'feishu_app_id': 'cli_test', 'feishu_app_secret': 'local-secret', 'feishu_webhook': 'local-hook', 'feishu_secret': 'bot-secret'}
        self.client = ImageClient(self.config, self.db)

    def tearDown(self):
        self.db.close()

    def test_source_allowlist(self):
        self.assertEqual(image_url(URL), URL)
        self.assertTrue(image_url('serve.php?record=x&path=a.png'))
        for value in ['https://evil.test/a.png', 'http://www.yyscha.cn/application/QQ/serve.php?record=x&path=a', 'https://www.yyscha.cn/other.php?record=x&path=a', 'https://www.yyscha.cn.evil.test/application/QQ/serve.php?record=x&path=a', 'data:image/png;base64,a', 'https://user@www.yyscha.cn/application/QQ/serve.php?record=x&path=a']:
            self.assertIsNone(image_url(value))

    def test_extract_quote_body_lazy_and_ignore_avatar(self):
        quoted = URL.replace('one', 'quote')
        html = f'<img src="https://q.qlogo.cn/g?a=1"><div class="reply-content"><img src="{quoted}"></div><div class="content"><img src="placeholder" data-src="{URL}"><img src="{URL}"></div>'
        result = extract_images({'html': html})
        self.assertEqual(len(result), 2)
        self.assertEqual([i['context'] for i in result], ['quote', 'body'])

    def test_cache_identity_ignores_signature(self):
        self.assertEqual(stable_ref(URL), stable_ref(URL.replace('9999999999', '1').replace('temporary', 'new')))

    def test_download_cookie_only_to_approved_source(self):
        opener = Mock()
        opener.open.return_value = Response(b'\x89PNG\r\n\x1a\npayload')
        with patch('site_session.get_session', return_value=opener):
            data, mime = self.client.download(URL)
        self.assertEqual(mime, 'image/png')
        req = opener.open.call_args.args[0]
        self.assertIsNone(req.get_header('Cookie'))  # Only the source-scoped jar attaches it.
        self.assertIsNone(req.get_header('Authorization'))
        self.assertTrue(data.startswith(b'\x89PNG'))
        opener.reset_mock()
        with patch('site_session.get_session', return_value=opener), self.assertRaises(r.Failure):
            self.client.download('https://evil.test/a')
        opener.open.assert_not_called()

    def test_non_image_and_size_rejected(self):
        for data, mime in [(b'<html>login</html>', 'text/html'), (b'<html>login</html>', 'image/png')]:
            opener = Mock()
            opener.open.return_value = Response(data, mime)
            with patch('site_session.get_session', return_value=opener), self.assertRaises(r.Failure):
                self.client.download(URL)
        self.assertEqual(image_mime(b'GIF89a'+b'x'), 'image/gif')
        opener = Mock()
        response = Response(b'\x89PNG\r\n\x1a\n')
        response.headers['Content-Length'] = str(10 * 1024 * 1024)
        opener.open.return_value = response
        with patch('site_session.get_session', return_value=opener), self.assertRaises(r.Failure):
            self.client.download(URL)

    def test_upload_multipart_and_cache(self):
        opener = Mock()
        opener.open.return_value = Response(json.dumps({'code': 0, 'data': {'image_key': 'img_test'}}).encode(), 'application/json')
        with patch.object(self.client, 'access_token', return_value='local-token'), patch.object(self.client, 'download', return_value=(b'png-binary', 'image/png')) as download, patch('media_feishu.build_opener', return_value=opener):
            self.assertEqual(self.client.upload(URL), 'img_test')
            self.assertEqual(self.client.upload(URL.replace('temporary', 'changed')), 'img_test')
            self.assertEqual(download.call_count, 1)
        req = opener.open.call_args.args[0]
        self.assertIsNone(req.get_header('Cookie'))
        self.assertEqual(req.get_header('Authorization'), 'Bearer local-token')
        self.assertIn(b'name="image_type"', req.data)
        self.assertIn(b'png-binary', req.data)

    def test_upload_rejection_does_not_cache(self):
        opener = Mock()
        opener.open.return_value = Response(b'{"code":123}', 'application/json')
        with patch.object(self.client, 'access_token', return_value='local-token'), patch.object(self.client, 'download', return_value=(b'png', 'image/png')), patch('media_feishu.build_opener', return_value=opener), self.assertRaises(r.Failure):
            self.client.upload(URL)
        self.assertEqual(self.db.execute('SELECT count(*) FROM image_uploads').fetchone()[0], 0)

    def test_card_contains_text_image_and_signature(self):
        with patch.object(self.client, 'upload', return_value='img_test'), patch('relay.request_json', return_value={'code': 0}) as request:
            self.client.send('����\n�ظ�', [{'url': URL, 'context': 'quote'}])
            payload = request.call_args.kwargs['payload']
            self.assertEqual(payload['msg_type'], 'interactive')
            self.assertEqual(payload['card']['elements'][1]['img_key'], 'img_test')
            self.assertEqual(payload['card']['elements'][1]['alt']['content'], '������ͼ')
            self.assertIn('sign', payload)

    def test_failed_card_is_not_business_success(self):
        with patch.object(self.client, 'upload', return_value='img_test'), patch('relay.request_json', return_value={}), self.assertRaises(r.Failure):
            self.client.send('body', [{'url': URL}])

    def test_refresh_expired_signature(self):
        stale = [{'url': URL.replace('9999999999', '1'), 'context': 'body'}]
        fresh = {'id': 'msg-one', 'html': f'<img src="{URL}">'}
        with patch('history_test.fetch_day', return_value=[fresh]):
            self.assertEqual(refresh_if_expired(self.config, '2026-11-11', r.UIDS[0], 'msg-one', stale)[0]['url'], URL)

    def test_media_failure_keeps_durable_retry(self):
        self.db.executescript('CREATE TABLE messages(scope TEXT,id TEXT,body TEXT,attempts INTEGER,media_json TEXT,day TEXT,uid TEXT,status TEXT,next_try REAL,error TEXT)')
        self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)', ('scope','id','body',0,json.dumps([{'url':URL}]),'2026-11-11','u_one','pending',0,None))
        self.db.execute('ALTER TABLE messages ADD COLUMN ts REAL DEFAULT 0')
        self.db.execute("ALTER TABLE messages ADD COLUMN parts_json TEXT DEFAULT '{}'")
        self.db.commit()
        client = Mock()
        client.send.side_effect = r.Failure('ģ��ͼƬʧ��')
        with patch('relay.time.sleep'):
            r.drain(self.db, self.config, now=100, image_client=client)
        row = self.db.execute('SELECT status,attempts FROM messages').fetchone()
        self.assertEqual(row, ('retry', 1))

    def test_quote_gray_box_and_reply_are_separate(self):
        parts = {'header': 'Ŀ�� �� 11-11 10:00', 'quote': {'author': '��һλ', 'time': '', 'text': '������ԭ��'}, 'body': '��ǰ�ظ�', 'link': 'https://www.yyscha.cn/application/QQ/day.php?day=2026-11-11'}
        elements = card_elements('fallback', parts, [('q-key', 'quote'), ('b-key', 'body')])
        box = next(e for e in elements if e['tag'] == 'column_set')
        self.assertEqual(box['background_style'], 'grey')
        inner = box['columns'][0]['elements']
        self.assertEqual(inner[1]['text']['content'], '������ԭ��')
        self.assertEqual(inner[2]['img_key'], 'q-key')
        self.assertNotIn('��ǰ�ظ�', json.dumps(box, ensure_ascii=False))
        self.assertTrue(any(e.get('text', {}).get('content') == '�ظ�\n��ǰ�ظ�' for e in elements))
        self.assertTrue(any(e.get('img_key') == 'b-key' for e in elements))
        self.assertNotIn('ʱ��δ֪', json.dumps(elements, ensure_ascii=False))
        self.assertEqual(elements[-1]['text']['content'].split(']')[0], '[�鿴ԭ����')

    def test_plain_user_content_is_not_interpreted_as_markdown(self):
        parts = {'header': 'Ŀ��', 'quote': {'author': '��һλ', 'text': '**ԭ��** <at id=all>'}, 'body': '<font>�ظ�</font>'}
        elements = card_elements('', parts, [])
        inner = elements[1]['columns'][0]['elements']
        self.assertEqual(inner[1]['text']['tag'], 'plain_text')
        self.assertEqual(inner[1]['text']['content'], '**ԭ��** <at id=all>')

    def test_quote_only_card_does_not_require_image_credentials(self):
        client = ImageClient({'feishu_webhook': 'local-hook'}, self.db)
        parts = {'header': 'Ŀ��', 'quote': {'author': '��һλ', 'text': 'ԭ��'}, 'body': '�ظ�'}
        with patch.object(client, 'upload') as upload, patch('relay.request_json', return_value={'code': 0}) as request:
            client.send('legacy', [], parts=parts)
        upload.assert_not_called()
        self.assertEqual(request.call_args.kwargs['payload']['card']['elements'][1]['tag'], 'column_set')

    def test_qq_face_is_text_not_attachment(self):
        item = {'id': 'm', 'uid': 'u_one', 'html': '<div class="content">�ظ�<img class="face-emoji face-emoji-image" src="https://koishi.js.org/QFace/assets/qq_emoji/13/apng/13.png" alt="/����"></div>'}
        self.assertEqual(r.message_parts(item, '2026-11-11')['body'], '�ظ�/����')
        self.assertEqual(extract_images(item), [])
        from history_test import category
        self.assertEqual(category(item), '��ͨ��Ϣ������')

    def test_avatar_not_rendered_as_chat_image(self):
        self.assertEqual(r.DOM('<img src="https://q.qlogo.cn/g?b=qq">').root.plain(), '')

    def test_missing_key_never_says_image_below(self):
        parts = {'header': 'Ŀ��', 'quote': {'author': '��һλ', 'text': '[ͼƬ��quote.png]'}, 'body': '[ͼƬ��body.png]'}
        serialized = json.dumps(card_elements('', parts, []), ensure_ascii=False)
        self.assertNotIn('���·�', serialized)
        self.assertIn('ͼƬδ��', serialized)


if __name__ == '__main__':
    unittest.main()

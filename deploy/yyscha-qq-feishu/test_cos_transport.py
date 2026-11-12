from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
import uuid

import cos_transport as c
import cos_settings


CREDS = dict(bucket=c.BUCKET, region=c.REGION, device=c.DEVICE,
             secret_id='syntheticid1234567890', secret_key='synthetickey1234567890')


class Response(io.BytesIO):
    status = 200
    headers = {}


class Opener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class CosTest(unittest.TestCase):
    def setUp(self):
        # Offline transport tests must not depend on a real board NTP marker.
        self.clock = patch('cos_transport.check_time')
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_untrusted_time_blocks_authenticated_request_before_network(self):
        opener = Opener(Response())
        with patch('cos_transport.check_time', side_effect=c.CloudError('untrusted')):
            with self.assertRaises(c.CloudError):
                c.CosClient(CREDS, opener).request('GET', 'a')
        self.assertFalse(opener.calls)

    def test_official_upload_canonical_hash_vector(self):
        # Official public, synthetic example; no real access credential used.
        headers = {'Content-Length': '13', 'Content-MD5': 'mQ/fVh815F3k6TAUm8m0eg==',
                   'Content-Type': 'text/plain', 'Date': 'Thu, 16 May 2019 06:45:51 GMT',
                   'Host': 'examplebucket-1250000000.cos.ap-beijing.myqcloud.com',
                   'x-cos-acl': 'private', 'x-cos-grant-read': 'uin="100000000011"'}
        value = c.http_string('PUT', '/exampleobject(腾讯云)', {}, headers)
        self.assertEqual(hashlib.sha1(value.encode()).hexdigest(), '8b2751e77f43a0995d6e9eb9477f4b685cca4172')

    def test_signing_query_sorting_and_parameter_binding(self):
        params = {'prefix': 'example-folder/', 'delimiter': '/', 'max-keys': 10}
        self.assertEqual(c.encode_fields(params), ('delimiter;max-keys;prefix', 'delimiter=%2F&max-keys=10&prefix=example-folder%2F'))
        args = ('GET', '/', params, {'host': 'example.test'}, CREDS['secret_id'], CREDS['secret_key'])
        original = c.authorization(*args, now=1000)
        changed = c.authorization('GET', '/', {**params, 'prefix': 'other/'}, args[3], *args[4:], now=1000)
        self.assertNotEqual(original, changed)
        self.assertNotIn(CREDS['secret_key'], original)
        self.assertIn('q-sign-time=970;1300', original)

    def test_fixed_endpoint_no_proxy_and_content_md5(self):
        opener = Opener(Response(b''))
        client = c.CosClient(CREDS, opener=opener)
        client.request('PUT', 'devices/h616-home-01/inbox/a.json', data=b'{}')
        req = opener.calls[0]
        self.assertTrue(req.full_url.startswith('https://' + client.host + '/'))
        self.assertEqual(req.get_header('Content-md5'), 'mZFLkyvTelC5g8XnyQrpOw==')
        self.assertIn('content-md5', req.get_header('Authorization'))

    def test_foreign_config_traversal_injection_and_size_rejected(self):
        with self.assertRaises(c.CloudError):
            c.CosClient({**CREDS, 'bucket': 'foreign'})
        opener = Opener(Response())
        client = c.CosClient(CREDS, opener)
        for key in ('https://evil.test', '../config', '/absolute', 'x?acl', 'x\r\nCookie: secret'):
            with self.subTest(key=key), self.assertRaises(c.CloudError):
                client.request('GET', key)
        with self.assertRaises(c.CloudError):
            client.request('PUT', 'a', data=b'x' * (c.MAX_OBJECT + 1))
        self.assertFalse(opener.calls)

    def test_response_limits_and_redirect_rejected(self):
        client = c.CosClient(CREDS, Opener(Response(b'12345')))
        with self.assertRaises(c.CloudError):
            client.request('GET', 'a', max_bytes=4)
        with self.assertRaises(c.CloudError):
            c.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://foreign.test')

    def test_error_body_cannot_echo_secrets_and_codes_stay_distinct(self):
        for code in ('AccessDenied', 'SignatureDoesNotMatch', 'private-secret-not-allowed'):
            body = ('<Error><Code>' + code + '</Code><Message>SECRET_DO_NOT_LOG</Message></Error>').encode()
            error = HTTPError('https://signed-secret-url', 403, 'secret-reason', {}, io.BytesIO(body))
            with self.assertRaises(c.CloudError) as caught:
                c.CosClient(CREDS, Opener(error)).request('GET', 'a')
            self.assertNotIn('SECRET', str(caught.exception))
            self.assertNotIn('signed-secret', str(caught.exception))
            self.assertEqual(caught.exception.code, code if code in ('AccessDenied', 'SignatureDoesNotMatch') else None)

    def xml(self, **changes):
        fields = {'Name': c.BUCKET, 'Prefix': 'devices%2Fh616-home-01%2Finbox%2F',
                  'EncodingType': 'url', 'IsTruncated': 'false'}
        fields.update(changes)
        return ('<ListBucketResult>' + ''.join(f'<{k}>{v}</{k}>' for k,v in fields.items()) +
                '<Contents><Key>devices%2Fh616-home-01%2Finbox%2F' + 'a'*32 + '.json</Key><Size>123</Size></Contents></ListBucketResult>').encode()

    def test_list_request_uses_single_encoded_prefix_and_validates_result(self):
        opener = Opener(Response(self.xml()))
        entries, marker = c.CosClient(CREDS, opener).list_inbox()
        self.assertEqual(len(entries), 1)
        self.assertFalse(marker)
        query = parse_qs(urlparse(opener.calls[0].full_url).query)
        self.assertEqual(query['prefix'], ['devices/h616-home-01/inbox/'])
        self.assertEqual(query['max-keys'], ['100'])

    def test_foreign_list_and_entity_expansion_rejected(self):
        for content in (self.xml(Prefix='other/'), self.xml(IsTruncated='true', NextMarker='other/'),
                        b'<!DOCTYPE x [<!ENTITY x "boom">]><ListBucketResult/>'):
            with self.assertRaises(c.CloudError):
                c.CosClient(CREDS, Opener(Response(content))).list_inbox()

    def test_atomic_private_save_does_not_touch_site_configuration(self):
        root = Path(__file__).parent / '.test-work' / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            path = root / 'config.json'
            path.write_text('{"site_cookie":"existing"}')
            with patch.object(cos_settings, 'PRIVATE', root):
                saved = cos_settings.save_credentials('uploader', CREDS['secret_id'], CREDS['secret_key'])
            self.assertEqual(c.load_credentials(saved), CREDS)
            self.assertEqual(path.read_text(), '{"site_cookie":"existing"}')
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['config.json', 'cos-uploader.json'])
        finally:
            shutil.rmtree(root)


if __name__ == '__main__':
    unittest.main()

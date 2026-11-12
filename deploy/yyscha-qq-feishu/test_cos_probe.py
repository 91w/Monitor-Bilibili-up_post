import json
from pathlib import Path
import shutil
import unittest
import uuid

from cos_transport import CosClient, CloudError
from cos_probe import probe


class FakeCloud:
    def __init__(self, role, store, broad=False, signature_failure=False):
        self.role, self.store = role, store
        self.broad, self.signature_failure = broad, signature_failure
        self.prefix = 'devices/h616-home-01/'

    object_key = CosClient.object_key

    def request(self, method, key='', params=None, data=None, authenticated=True, **kwargs):
        if not authenticated:
            raise CloudError('denied', 403, 'AccessDenied')
        inbox = key.startswith(self.prefix + 'inbox/')
        receipts = key.startswith(self.prefix + 'receipts/')
        allowed = (self.role == 'uploader' and ((inbox and method == 'PUT') or (receipts and method == 'GET')) or
                   self.role == 'receiver' and ((inbox and method in ('GET', 'DELETE')) or (receipts and method == 'PUT')))
        if not allowed and not self.broad:
            raise CloudError('denied', 403, 'SignatureDoesNotMatch' if self.signature_failure else 'AccessDenied')
        if method == 'PUT':
            self.store[key] = data
            return b''
        if method == 'DELETE':
            self.store.pop(key, None)
            return b''
        if params:
            return b''
        if key not in self.store:
            raise CloudError('missing', 404, 'NoSuchKey')
        return self.store[key]

    def list_inbox(self, marker=''):
        return ([{'key': key, 'size': len(value)} for key, value in self.store.items()
                 if key.startswith(self.prefix + 'inbox/')], '')


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / '.test-work' / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_probe(self, **kwargs):
        store = {}
        path = self.root / 'report.json'
        report = probe(FakeCloud('uploader', store, **kwargs), FakeCloud('receiver', store, **kwargs), path)
        self.assertEqual(report, json.loads(path.read_text(encoding='utf-8')))
        self.assertFalse(any('/inbox/' in key for key in store))
        self.assertNotIn('synthetic-permission-check', path.read_text(encoding='utf-8'))
        return report

    def test_scoped_permissions_encrypted_roundtrip_and_receipt(self):
        report = self.run_probe()
        self.assertTrue(report['passed'])
        self.assertEqual(len(report['checks']), 18)
        self.assertEqual(len(report['leftovers']), 1)

    def test_overbroad_permissions_fail_instead_of_passing_on_connectivity(self):
        report = self.run_probe(broad=True)
        self.assertFalse(report['passed'])
        self.assertTrue(any(check.get('reason') == '越权操作意外成功' for check in report['checks']))

    def test_signature_failure_cannot_count_as_correct_denial(self):
        report = self.run_probe(signature_failure=True)
        self.assertFalse(report['passed'])
        self.assertTrue(any(check.get('code') == 'SignatureDoesNotMatch' and not check['passed'] for check in report['checks']))


if __name__ == '__main__':
    unittest.main()

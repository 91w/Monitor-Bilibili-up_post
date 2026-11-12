import json
import unittest

import secure_package as p
libnacl = p.libnacl


class SecurePackageTest(unittest.TestCase):
    def setUp(self):
        self.public, self.secret = libnacl.crypto_box_keypair()
        self.sender, self.signing = libnacl.crypto_sign_keypair()
        self.receipt_public, self.receipt_secret = libnacl.crypto_sign_keypair()
        self.packet = p.seal('session=synthetic-cookie-only', 'h616-test', 2, self.public,
                             self.signing, now=100000)

    def open(self, packet=None, **kwargs):
        options = dict(applied_version=1, now=100001)
        options.update(kwargs)
        return p.open_package(packet or self.packet, 'h616-test', self.public,
                              self.secret, self.sender, **options)

    def test_roundtrip_and_cloud_contains_no_cookie(self):
        header, cookie = self.open()
        self.assertEqual(cookie, 'session=synthetic-cookie-only')
        self.assertNotIn(b'synthetic-cookie', self.packet)
        self.assertEqual(header['version'], 2)

    def test_every_header_field_is_authenticated(self):
        for key, replacement in dict(protocol=2, device='wrong-device', package_id='0'*32,
                                     version=3, created=99999, expires=100005).items():
            with self.subTest(field=key):
                packet = json.loads(self.packet)
                packet['header'][key] = replacement
                with self.assertRaises(p.PackageError):
                    self.open(p.canonical(packet))

    def test_ciphertext_and_signature_tamper_rejected(self):
        for key in ('ciphertext', 'signature'):
            packet = json.loads(self.packet)
            value = p.unb64(packet[key])
            packet[key] = p.b64(bytes([value[0] ^ 1]) + value[1:])
            with self.assertRaises(p.PackageError):
                self.open(p.canonical(packet))

    def test_wrong_sender_rejected(self):
        other, _ = libnacl.crypto_sign_keypair()
        with self.assertRaises(p.PackageError):
            p.authenticate(self.packet, 'h616-test', other)

    def test_wrong_recipient_rejected(self):
        public, secret = libnacl.crypto_box_keypair()
        with self.assertRaises(p.PackageError):
            p.open_package(self.packet, 'h616-test', public, secret, self.sender, now=100001)

    def test_expired_future_and_rollback_rejected(self):
        for options in (dict(now=186400), dict(now=99000), dict(applied_version=2), dict(applied_version=3)):
            with self.subTest(options=options), self.assertRaises(p.PackageError):
                self.open(**options)

    def test_size_duplicate_and_unknown_fields_rejected(self):
        for packet in (b' '*(p.MAX_BYTES+1), b'{"a":1,"a":2}', p.canonical({**json.loads(self.packet), 'extra': True})):
            with self.assertRaises(p.PackageError):
                self.open(packet)

    def test_cookie_injection_and_bool_version_rejected(self):
        for cookie, version in [('session=x\r\nInjected:yes', 1), ('session=x', True)]:
            with self.assertRaises(p.PackageError):
                p.seal(cookie, 'h616-test', version, self.public, self.signing, now=100000)

    def test_receipt_is_bound_to_package_and_pinned_board(self):
        header, _ = self.open()
        data = p.receipt(header, self.packet, 'applied', self.receipt_secret)
        self.assertEqual(p.verify_receipt(data, header, self.packet, self.receipt_public), 'applied')
        with self.assertRaises(p.PackageError):
            p.verify_receipt(data, header, self.packet + b' ', self.receipt_public)
        with self.assertRaises(p.PackageError):
            p.verify_receipt(data, header, self.packet, self.sender)
        self.assertNotIn(b'synthetic-cookie', data)


if __name__ == '__main__':
    unittest.main()

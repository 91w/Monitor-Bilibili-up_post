import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

from device_pairing import initialize, pin_peer, public_identity
from test_cos_probe import FakeCloud
from relay_updates import DesktopUpdates, BoardUpdates
import relay as r
import secure_package as p
from site_session import SiteSession


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / '.test-work' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.desktop, self.board = self.root / 'desktop', self.root / 'board'
        desktop = initialize(self.desktop, 'desktop')
        board = initialize(self.board, 'board')
        pin_peer(self.desktop, 'desktop', public_identity(board))
        pin_peer(self.board, 'board', public_identity(desktop))
        self.config = self.board / 'config.json'
        self.config.write_text(json.dumps({'site_cookie': 'session=old', 'uids': r.UIDS,
                                           'feishu_webhook': '', 'custom': 'preserve'}))
        self.store = {}
        self.up = DesktopUpdates(self.desktop, FakeCloud('uploader', self.store))
        self.down = BoardUpdates(self.board, client=FakeCloud('receiver', self.store))
        self.clock = patch('relay_updates.check_time')
        self.clock.start()
        self.verified = []
        def verify(cookie):
            self.verified.append(cookie)
            return SiteSession(cookie)
        self.verifier = patch.object(self.down, '_verify', side_effect=verify)
        self.verifier.start()

    def tearDown(self):
        self.verifier.stop()
        self.clock.stop()
        self.up.close()
        self.down.close()
        shutil.rmtree(self.root)

    def packet(self, package_id):
        return self.up.db.execute('SELECT packet FROM packages WHERE id=?', (package_id,)).fetchone()[0]

    def test_roundtrip_preserves_configuration_and_persists_encrypted_outbox(self):
        package_id = self.up.enqueue('session=synthetic-new')
        packet = self.packet(package_id)
        self.assertNotIn(b'synthetic-new', packet)
        self.assertEqual(self.up.synchronize()['state'], 'uploaded')
        self.assertEqual(self.down.synchronize(), [{'version': 1, 'state': 'applied'}])
        self.assertEqual(self.up.synchronize()['state'], 'applied')
        config = json.loads(self.config.read_text())
        self.assertEqual(config['site_cookie'], 'session=synthetic-new')
        self.assertEqual(config['uids'], r.UIDS)
        self.assertEqual(config['custom'], 'preserve')
        self.assertEqual(self.down.applied_version(), 1)

    def test_upload_timeout_reuses_identical_package_and_version(self):
        package_id = self.up.enqueue('session=retry')
        packet = self.packet(package_id)
        original = self.up.client.request
        def timeout(method, key, **kwargs):
            original(method, key, **kwargs)
            raise r.Failure('simulated timeout')
        with patch.object(self.up.client, 'request', side_effect=timeout), self.assertRaises(r.Failure):
            self.up.synchronize()
        self.assertEqual(self.up.status()['state'], 'queued')
        self.up.synchronize()
        self.assertEqual(self.packet(package_id), packet)
        self.assertEqual(self.up.status()['version'], 1)

    def test_newest_version_first_and_old_package_never_rolls_back(self):
        old = self.up.enqueue('session=old-candidate')
        new = self.up.enqueue('session=new-candidate')
        self.up.synchronize()
        self.down.synchronize()
        self.assertEqual(self.verified, ['session=new-candidate'])
        self.assertEqual(self.down.applied_version(), 2)
        self.assertEqual(json.loads(self.config.read_text())['site_cookie'], 'session=new-candidate')
        self.assertEqual(p.parse(self.down.handle(self.packet(old)))['body']['status'], 'rejected')
        self.assertEqual(p.parse(self.down.handle(self.packet(new)))['body']['status'], 'applied')

    def test_duplicate_returns_exact_original_receipt_without_revalidating(self):
        packet = self.packet(self.up.enqueue('session=new'))
        signed = self.down.handle(packet)
        self.assertEqual(self.down.handle(packet), signed)
        self.assertEqual(len(self.verified), 1)

    def test_bad_cookie_retains_existing_config_and_version(self):
        packet = self.packet(self.up.enqueue('session=bad'))
        original = self.config.read_bytes()
        with patch.object(self.down, '_verify', side_effect=r.AuthRequired('bad synthetic cookie')):
            signed = self.down.handle(packet)
        self.assertEqual(p.parse(signed)['body']['status'], 'rejected')
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(self.down.applied_version(), 0)

    def test_network_failure_retains_session_and_allows_retry(self):
        packet = self.packet(self.up.enqueue('session=new'))
        with patch.object(self.down, '_verify', side_effect=r.Failure('network unavailable')), self.assertRaises(r.Failure):
            self.down.handle(packet)
        self.assertEqual(self.down.applied_version(), 0)
        self.assertEqual(json.loads(self.config.read_text())['site_cookie'], 'session=old')
        self.assertEqual(p.parse(self.down.handle(packet))['body']['status'], 'applied')

    def test_crash_after_config_replace_recovers_without_second_promotion(self):
        packet = self.packet(self.up.enqueue('session=new'))
        original = self.down._promote
        def crash(session, marker):
            original(session, marker)
            raise RuntimeError('simulated process death before ledger commit')
        with patch.object(self.down, '_promote', side_effect=crash), self.assertRaises(RuntimeError):
            self.down.handle(packet)
        self.assertEqual(self.down.applied_version(), 0)
        self.down.close()
        self.down = BoardUpdates(self.board, client=FakeCloud('receiver', self.store))
        with patch.object(self.down, '_verify', side_effect=AssertionError('must not revalidate')):
            signed = self.down.handle(packet)
        self.assertEqual(p.parse(signed)['body']['status'], 'applied')
        self.assertEqual(self.down.applied_version(), 1)

    def test_receipt_can_be_republished_after_cloud_input_disappears(self):
        package_id = self.up.enqueue('session=new')
        packet = self.packet(package_id)
        self.down.handle(packet)
        self.down.flush_receipts()
        self.assertIn(self.up.client.object_key('receipts', package_id), self.store)
        self.assertEqual(self.up.synchronize()['state'], 'applied')


if __name__ == '__main__':
    unittest.main()

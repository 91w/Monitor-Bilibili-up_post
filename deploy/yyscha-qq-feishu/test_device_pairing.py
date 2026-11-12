from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import device_pairing as d
import cos_board_probe as b
from cos_transport import CloudError
from test_cos_probe import FakeCloud


class PairTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / '.test-work' / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root)

    def pair(self):
        desktop, board = self.root / 'desktop', self.root / 'board'
        sender = d.initialize(desktop, 'desktop')
        receiver = d.initialize(board, 'board')
        d.pin_peer(desktop, 'desktop', d.public_identity(receiver))
        d.pin_peer(board, 'board', d.public_identity(sender))
        return desktop, board

    def test_initialization_preserves_identity_and_exports_no_secrets(self):
        root = self.root / 'board'
        identity = d.initialize(root, 'board')
        self.assertEqual(d.initialize(root, 'board'), identity)
        public = d.public_identity(identity)
        self.assertEqual(set(public), {'protocol', 'device', 'role', 'decrypt_public', 'receipt_public'})
        self.assertNotIn(identity['decrypt_secret'], str(public))
        self.assertNotIn(identity['receipt_secret'], str(public))

    def test_changed_peer_cannot_silently_replace_pinned_sender(self):
        desktop, board = self.pair()
        before = (board / 'relay-peer.json').read_bytes()
        replacement = d.public_identity(d.initialize(self.root / 'other', 'desktop'))
        with self.assertRaises(CloudError):
            d.pin_peer(board, 'board', replacement)
        self.assertEqual((board / 'relay-peer.json').read_bytes(), before)

    def test_wrong_device_and_role_rejected(self):
        public = d.public_identity(d.initialize(self.root / 'desktop', 'desktop'))
        with self.assertRaises(CloudError):
            d.validate_public(public, 'board')
        with self.assertRaises(CloudError):
            d.validate_public({**public, 'device': 'other'}, 'desktop')

    def test_outputs_cannot_receive_private_pairing_keys(self):
        with self.assertRaises(CloudError):
            d.initialize(self.root / 'outputs' / 'identity', 'desktop')

    def test_cloud_board_roundtrip_uses_only_board_private_keys(self):
        desktop, board = self.pair()
        store = {}
        manifest = self.root / 'fixture.json'
        uploader, receiver = FakeCloud('uploader', store), FakeCloud('receiver', store)
        with patch.object(b, 'load_credentials', return_value={}), patch.object(b, 'CosClient', return_value=uploader):
            result = b.prepare(desktop, manifest)
        self.assertTrue(result['uploaded'])
        board_secret = d.load_pair(board, 'board')[0]['decrypt_secret']
        self.assertNotIn(board_secret, manifest.read_text())
        with patch.object(b, 'load_credentials', return_value={}), patch.object(b, 'CosClient', return_value=receiver):
            result = b.board(board, manifest)
        self.assertTrue(result['decrypted'])
        self.assertFalse(result['website_session_applied'])
        with patch.object(b, 'load_credentials', return_value={}), patch.object(b, 'CosClient', return_value=uploader):
            self.assertTrue(b.verify(desktop, manifest)['board_receipt_authenticated'])
        self.assertFalse(any('/inbox/' in key for key in store))


if __name__ == '__main__':
    unittest.main()

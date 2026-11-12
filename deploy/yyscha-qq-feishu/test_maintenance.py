import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import unittest
import uuid
import zipfile

from maintenance import backup_database, freeze, sender_active


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / '.test-work' / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_wal_snapshot_contains_committed_data_and_survives_writer(self):
        path = self.root / 'live.sqlite3'
        live = sqlite3.connect(path)
        try:
            live.execute('PRAGMA journal_mode=WAL')
            live.execute('CREATE TABLE sample(value TEXT)')
            live.execute("INSERT INTO sample VALUES('committed')")
            live.commit()
            live.execute("INSERT INTO sample VALUES('uncommitted')")
            backup_database(path, self.root / 'snapshot.sqlite3')
            with closing(sqlite3.connect(self.root / 'snapshot.sqlite3')) as copy:
                self.assertEqual(copy.execute('SELECT * FROM sample').fetchall(), [('committed',)])
        finally:
            live.close()

    def test_sender_detection_does_not_take_over_active_lock(self):
        path = self.root / 'state.sqlite3'
        self.assertFalse(sender_active(path))
        with closing(sqlite3.connect(str(path) + '.lock')) as lock:
            lock.execute('CREATE TABLE guard(id INTEGER)')
            lock.execute('BEGIN EXCLUSIVE')
            self.assertTrue(sender_active(path))
            lock.rollback()
        self.assertFalse(sender_active(path))

    def test_freeze_private_config_is_separate_from_source_archive(self):
        source, private = self.root / 'source', self.root / 'private'
        source.mkdir()
        private.mkdir()
        (source / 'relay.py').write_text('# source')
        (source / 'config.json').write_text('private-source-secret')
        (private / 'config.json').write_text('{"site_cookie":"private-secret"}')
        report = freeze(source, private, self.root / 'snapshot')
        self.assertFalse(report['sender_lock_active'])
        with zipfile.ZipFile(self.root / 'snapshot/source.zip') as archive:
            self.assertEqual(archive.namelist(), ['relay.py'])
        self.assertEqual(json.loads((self.root / 'snapshot/config.json').read_text())['site_cookie'], 'private-secret')

    def test_private_snapshots_refuse_outputs(self):
        with self.assertRaises(ValueError):
            freeze(self.root, self.root, self.root / 'outputs' / 'snapshot')


if __name__ == '__main__':
    unittest.main()

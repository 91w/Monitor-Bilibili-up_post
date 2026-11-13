import json,sqlite3,shutil,unittest,uuid
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import relay as r
from message_identity import describe_all
from identity_migration import run

class MigrationTest(unittest.TestCase):
    def setUp(self):
        workspace=Path(__file__).parent/'.test-work';workspace.mkdir(exist_ok=True)
        self.root=workspace/uuid.uuid4().hex;self.root.mkdir()
        self.config=self.root/'config.json';self.path=self.root/'state.sqlite3'
        self.config.write_text(json.dumps({'site_cookie':'session=synthetic-migration','uids':['u_test']}))
        db=r.connect(self.path)
        with db:
            for mid,status in [('old','sent'),('copy','retry'),('missing','retry')]:
                db.execute("INSERT INTO messages(scope,id,day,uid,ts,body,status) VALUES('scope',?,'2026-11-13','u_test',1,'test',?)",(mid,status))
        db.close()
        items=[{'id':mid,'uid':'u_test','ts':1,'type':'text','text':'test','record_id':record} for mid,record in [('old','archive-one'),('copy','archive-two')]]
        self.snapshots=[{'scope':'scope','identities':describe_all(items,'2026-11-13')}]
    def tearDown(self):
        assert self.root.resolve().is_relative_to((Path(__file__).parent/'.test-work').resolve())
        shutil.rmtree(self.root)
    def statuses(self):
        with closing(sqlite3.connect(self.path)) as db:return dict(db.execute('SELECT id,status FROM messages'))
    def test_default_dry_run_does_not_change_original_queue(self):
        before=self.statuses()
        with patch('identity_migration.fetch_snapshots',return_value=self.snapshots):report=run(self.config,self.path)
        self.assertFalse(report['applied']);self.assertEqual(report['messages_sent'],0)
        self.assertEqual(self.statuses(),before)
    def test_apply_keeps_sent_and_isolates_duplicates_and_unknown(self):
        with patch('identity_migration.fetch_snapshots',return_value=self.snapshots):report=run(self.config,self.path,True)
        self.assertTrue(report['applied'])
        self.assertEqual(self.statuses(),{'old':'sent','copy':'duplicate','missing':'quarantined'})
    def test_running_service_guard_blocks_network_and_migration(self):
        (self.root/'enabled').touch()
        with patch('identity_migration.fetch_snapshots') as fetch:
            with self.assertRaises(r.Failure):run(self.config,self.path,True)
            fetch.assert_not_called()

if __name__=='__main__':unittest.main()

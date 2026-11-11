# Resolve source modules after the delivery directory organization.
import sys
from pathlib import Path
_project=Path(__file__).resolve().parents[1]
for _source in (_project/'board/relay',_project/'board/maintenance'):
    if _source.is_dir():sys.path.insert(0,str(_source))

import json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import native_history as n
import native_history_service as service

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'nt_db';self.db.mkdir();self.backup=self.root/'backup'
        for name,data in {'nt_msg.db':b'encrypted-message','nt_msg.db-wal':b'wal','nt_msg.db-first.material':b'repair','group_msg_fts.db':b'index','settings.db':b'settings','profile_info.db':b'profile','group_info.db':b'groups','login.data':b'login'}.items():(self.db/name).write_bytes(data)
    def tearDown(self):self.tmp.cleanup()
    def test_only_allowlisted_files_rotate_and_restore(self):
        original={p.name:p.read_bytes() for p in self.db.iterdir()}
        result=n.rotate(self.db,self.backup,set());self.assertEqual(result['stage'],'awaiting_verification')
        for name in ('settings.db','profile_info.db','group_info.db','login.data'):self.assertEqual((self.db/name).read_bytes(),original[name])
        self.assertFalse((self.db/'nt_msg.db').exists())
        (self.db/'nt_msg.db').write_bytes(b'new-db')
        n.restore(self.db,self.backup,set())
        self.assertEqual({p.name:p.read_bytes() for p in self.db.iterdir()},original)
        self.assertEqual((self.backup/'new-before-restore/nt_msg.db').read_bytes(),b'new-db')
    def test_open_database_refuses_all_moves(self):
        st=(self.db/'nt_msg.db').stat()
        with self.assertRaises(RuntimeError):n.rotate(self.db,self.backup,{(st.st_dev,st.st_ino)})
        self.assertFalse(self.backup.exists());self.assertTrue((self.db/'nt_msg.db-wal').exists())
    def test_power_loss_partial_snapshot_can_be_restored(self):
        files=n.candidates(self.db);self.backup.mkdir()
        manifest={'stage':'preparing','files':[{'name':p.name,'sha256':n.digest(p),'bytes':p.stat().st_size} for p in files]}
        n.atomic(self.backup/'manifest.json',manifest)
        files[0].replace(self.backup/files[0].name)
        n.restore(self.db,self.backup,set())
        self.assertTrue(all((self.db/x['name']).exists() for x in manifest['files']))
        self.assertEqual(json.loads((self.backup/'manifest.json').read_text())['stage'],'rolled_back')
    def test_symlink_refused(self):
        (self.db/'buddy_msg_fts.db').symlink_to(self.db/'settings.db')
        with self.assertRaises(RuntimeError):n.rotate(self.db,self.backup,set())
        self.assertFalse(self.backup.exists())
    def test_hardlink_refused(self):
        os.link(self.db/'nt_msg.db',self.root/'alias')
        with self.assertRaises(RuntimeError):n.rotate(self.db,self.backup,set())
    def test_unverified_backup_never_purged(self):
        n.rotate(self.db,self.backup,set())
        self.assertEqual(n.purge_verified_backup(self.backup,time.time()+10*86400),0)
        self.assertTrue((self.backup/'nt_msg.db').exists())
    def test_verified_backup_kept_24h_then_purged(self):
        m=n.rotate(self.db,self.backup,set());now=int(time.time());m.update(stage='verified',verified_epoch=now);n.atomic(self.backup/'manifest.json',m)
        self.assertEqual(n.purge_verified_backup(self.backup,now+86399),0)
        self.assertGreater(n.purge_verified_backup(self.backup,now+86401),0)
        self.assertFalse((self.backup/'nt_msg.db').exists());self.assertTrue((self.db/'settings.db').exists())
    def test_restore_integrity_failure_leaves_new_db_untouched(self):
        n.rotate(self.db,self.backup,set());(self.backup/'nt_msg.db').write_bytes(b'tampered');(self.db/'nt_msg.db').write_bytes(b'fresh')
        with self.assertRaises(RuntimeError):n.restore(self.db,self.backup,set())
        self.assertEqual((self.db/'nt_msg.db').read_bytes(),b'fresh')
    def test_interrupted_backup_purge_resumes(self):
        m=n.rotate(self.db,self.backup,set());now=int(time.time());m.update(stage='purging',verified_epoch=now-86401);n.atomic(self.backup/'manifest.json',m)
        (self.backup/'nt_msg.db').unlink()
        self.assertGreater(n.purge_verified_backup(self.backup,now),0)
        self.assertEqual(json.loads((self.backup/'manifest.json').read_text())['stage'],'backup_deleted')
    def test_purge_rejects_manifest_path_escape(self):
        m=n.rotate(self.db,self.backup,set());m.update(stage='verified',verified_epoch=0);m['files'][0]['name']='../nt_db/settings.db';n.atomic(self.backup/'manifest.json',m)
        with self.assertRaises(RuntimeError):n.purge_verified_backup(self.backup,86401)
        self.assertEqual((self.db/'settings.db').read_bytes(),b'settings')
    def test_partial_rename_failure_restores_original_files(self):
        original=Path.replace;count=0
        def fail_after_one(p,target):
            nonlocal count
            if p.parent==self.db:
                count+=1
                if count==2:raise OSError('simulated rename failure')
            return original(p,target)
        with patch.object(Path,'replace',fail_after_one):
            with self.assertRaises(OSError):n.rotate(self.db,self.backup,set())
        self.assertTrue((self.db/'nt_msg.db').exists());self.assertTrue((self.db/'group_msg_fts.db').exists())

    def test_pending_messages_block_maintenance(self):
        box=Mock();box.status.return_value={'pending':1}
        with patch.object(service,'config',return_value={'allowed_group_ids':['123']}),patch.object(service,'Outbox',return_value=box):
            with self.assertRaises(RuntimeError):service.pending_check()
        box.db.close.assert_called_once()
    def test_stale_catchup_blocks_maintenance(self):
        box=Mock();box.status.return_value={'pending':0};box.get.return_value={'complete':True,'epoch':time.time()-1000}
        with patch.object(service,'config',return_value={'allowed_group_ids':['123']}),patch.object(service,'Outbox',return_value=box):
            with self.assertRaises(RuntimeError):service.pending_check(True)
    def test_untrusted_time_does_not_pause_qq(self):
        with patch.object(service,'ensure_time',side_effect=RuntimeError()),patch.object(service,'enabled') as enabled,patch.object(service,'rotate') as rotate:
            with self.assertRaises(RuntimeError):service.start_rotation(True)
        enabled.assert_not_called();rotate.assert_not_called()

if __name__=='__main__':unittest.main()

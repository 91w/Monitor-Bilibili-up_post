# Resolve source modules after the delivery directory organization.
import sys
from pathlib import Path
_project=Path(__file__).resolve().parents[1]
for _source in (_project/'board/relay',_project/'board/maintenance'):
    if _source.is_dir():sys.path.insert(0,str(_source))

import os,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import storage_maintenance as m
from rotating_sink import RotatingSink

class Tests(unittest.TestCase):
    def file(self,root,name,data=b'test'):
        p=Path(root)/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return p
    def future(self):return time.time()+45*86400
    def test_only_allowlisted_expired_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Path(tmp)
            keep=[self.file(b,'rootfs/home/napcat/.config/QQ/nt_qq_test/nt_db/message.db'),self.file(b,'rootfs/home/napcat/.config/QQ/global/nt_data/Login/login.data'),self.file(b,'rootfs/home/napcat/.config/QQ/nt_qq_test/nt_data/mmkv/state'),self.file(b,'rootfs/home/napcat/.config/QQ/nt_qq_test/nt_data/Pic/photo.png'),self.file(b,'forward-cache/pending/photo.png'),self.file(b,'config.json')]
            log=self.file(b,'qqtest.log');done=self.file(b,'forward-cache/completed/photo.png')
            result=m.cleanup(b,True,self.future(),set())
            self.assertFalse(log.exists());self.assertFalse(done.exists())
            self.assertTrue(all(p.read_bytes()==b'test' for p in keep))
            self.assertEqual(sum(v['removed_files'] for v in result.values()),2)
    def test_preview_does_not_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=self.file(tmp,'qqtest.log')
            r=m.cleanup(Path(tmp),False,self.future(),set())
            self.assertTrue(log.exists());self.assertEqual(r['test_logs']['eligible_files'],1)
    def test_open_and_fresh_files_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=self.file(tmp,'qqtest.log');info=log.stat()
            m.cleanup(Path(tmp),True,self.future(),{(info.st_dev,info.st_ino)})
            self.assertTrue(log.exists())
            m.cleanup(Path(tmp),True,time.time(),set());self.assertTrue(log.exists())
    def test_database_in_cleanup_bucket_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=self.file(tmp,'forward-cache/completed/message.db')
            m.cleanup(Path(tmp),True,self.future(),set());self.assertTrue(db.exists())
    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as outside:
            b=Path(tmp);target=self.file(outside,'important.log')
            (b/'qqescape.log').symlink_to(target)
            m.cleanup(b,True,self.future(),set())
            self.assertEqual(target.read_bytes(),b'test')
            self.assertIsNone(m.remove_checked(target,target.stat(),b))
    def test_hardlink_freed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Path(tmp);a=self.file(b,'qqone.log',b'x'*4096);os.link(a,b/'qqtwo.log')
            allocated=a.stat().st_blocks*512
            r=m.cleanup(b,True,self.future(),set())['test_logs']
            self.assertEqual(r['removed_files'],2);self.assertEqual(r['freed_bytes'],allocated)
    def test_thresholds(self):
        self.assertEqual(m.guard_level(6*m.GIB,1*m.GIB),'ok')
        self.assertEqual(m.guard_level(4*m.GIB,1*m.GIB),'warning')
        self.assertEqual(m.guard_level(2*m.GIB,1*m.GIB),'hold')
        self.assertEqual(m.guard_level(100*m.GIB,4*m.GIB),'hold')
    def test_critical_stops_only_via_scoped_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Path(tmp);db=self.file(b,'rootfs/home/napcat/.config/QQ/nt_qq_test/nt_db/message.db')
            disk=Mock(f_bavail=2*m.GIB,f_frsize=1)
            with patch.object(m.os,'statvfs',return_value=disk),patch.object(m,'clock_ready',return_value=False),patch.object(m,'stop_qq') as stop,patch.object(m,'notify',return_value='waiting_for_qq_feishu_config'):
                s=m.run_once(b,True)
                self.assertTrue(s['hold_active']);stop.assert_called_once_with(b)
            self.assertTrue(db.exists());self.assertTrue((b/'storage-hold.json').exists())
    def test_clock_failure_prevents_age_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Path(tmp);log=self.file(b,'qqtest.log')
            with patch.object(m,'clock_ready',return_value=False),patch.object(m,'notify',return_value='not_needed'):
                m.run_once(b,True,True)
            self.assertTrue(log.exists())
    def test_scan_limit_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Path(tmp)
            with patch.object(m,'profile_bytes',side_effect=RuntimeError('limit')),patch.object(m,'clock_ready',return_value=False),patch.object(m,'stop_qq') as stop,patch.object(m,'notify',return_value='pending'):
                result=m.run_once(b,True)
                self.assertEqual(result['level'],'hold');stop.assert_called_once()
    def test_log_rotation_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'qqtest.log';sink=RotatingSink(p,max_bytes=10,backups=2)
            sink.write(b'x'*95);sink.close()
            self.assertLessEqual(sum(f.stat().st_size for f in Path(tmp).iterdir()),30)
            self.assertEqual(p.stat().st_size,5)
    def test_rotator_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Path(tmp);target=self.file(b,'important');link=b/'qqtest.log';link.symlink_to(target)
            with self.assertRaises(ValueError):RotatingSink(link)
            self.assertEqual(target.read_bytes(),b'test')
if __name__=='__main__':unittest.main()

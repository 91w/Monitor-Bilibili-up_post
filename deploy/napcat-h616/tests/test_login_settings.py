# Resolve source modules after the delivery directory organization.
import sys
from pathlib import Path
_project=Path(__file__).resolve().parents[1]
for _source in (_project/'board/relay',_project/'board/maintenance'):
    if _source.is_dir():sys.path.insert(0,str(_source))

import json,os,tempfile,unittest
from pathlib import Path
import login_settings as m

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        (self.base/'forward-config.json').write_text(json.dumps({'self_id':'12345'}))
        self.data={'account':'12345','password':'unit-test-only'}
        self.cmd=['/opt/QQ/qq','-q','12345']
    def tearDown(self):self.tmp.cleanup()
    def test_save_permission_and_exact_password(self):
        value=dict(self.data,password=' test 密码 & quoted " ');m.save(self.base,value)
        p=self.base/'private/qq-login.json'
        self.assertEqual(p.stat().st_mode&0o777,0o600)
        self.assertEqual(p.parent.stat().st_mode&0o777,0o700)
        self.assertEqual(json.loads(p.read_text())['password'],value['password'])
    def test_reject_other_account(self):
        with self.assertRaises(ValueError):m.save(self.base,dict(self.data,account='67890'))
        self.assertFalse((self.base/'private/qq-login.json').exists())
    def test_only_matching_live_qq_receives_password(self):
        m.save(self.base,self.data)
        result=m.environment_for_login(self.base,'qq-live',self.cmd,{})
        self.assertEqual(result['NAPCAT_QUICK_PASSWORD'],self.data['password'])
        self.assertNotIn(self.data['password'],self.cmd)
        self.assertEqual(m.environment_for_login(self.base,'test',self.cmd,{}),{})
        self.assertNotIn('NAPCAT_QUICK_PASSWORD',m.environment_for_login(self.base,'qq-live',['/opt/QQ/qq','-q','67890'],{}))
    def test_insecure_file_not_loaded(self):
        m.save(self.base,self.data);(self.base/'private/qq-login.json').chmod(0o644)
        self.assertNotIn('NAPCAT_QUICK_PASSWORD',m.environment_for_login(self.base,'qq-live',self.cmd,{}))
    def test_reject_symlink(self):
        m.save(self.base,self.data);p=self.base/'private/qq-login.json';p.unlink();p.symlink_to(self.base/'forward-config.json')
        self.assertNotIn('NAPCAT_QUICK_PASSWORD',m.environment_for_login(self.base,'qq-live',self.cmd,{}))
        with self.assertRaises(ValueError):m.save(self.base,self.data)
    def test_no_password_fallback_inherited_for_live_qq(self):
        env={'NAPCAT_QUICK_PASSWORD':'unrelated','NAPCAT_QUICK_PASSWORD_MD5':'unrelated','PATH':'test'}
        self.assertEqual(m.environment_for_login(self.base,'qq-live',self.cmd,env),{'PATH':'test'})
        self.assertEqual(env['NAPCAT_QUICK_PASSWORD'],'unrelated')
    def test_nul_and_empty_password_rejected(self):
        for password in ('','a\x00b'):
            with self.assertRaises(ValueError):m.save(self.base,dict(self.data,password=password))

if __name__=='__main__':unittest.main()

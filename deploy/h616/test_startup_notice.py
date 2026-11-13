import sqlite3,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from startup_notice import StartupNotifier,install_startup_notice

class StartupTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript('CREATE TABLE queue(sent INTEGER);CREATE TABLE events(kind TEXT,uid TEXT);')
        self.w=SimpleNamespace(post_to_feishu=Mock(),normalize_up_users=lambda users:[SimpleNamespace(uid=str(x)) for x in users])
        self.config={'up_users':['1','2'],'feishu_webhook':'test-hook','feishu_keyword':'测试关键词'}
        self.guard=Mock();self.notifier=StartupNotifier(self.db,self.guard)
    def tearDown(self):self.db.close()
    def test_boot_dedup_and_keyword(self):
        self.notifier.send(self.w,self.config,2,100,'boot-one')
        self.notifier.send(self.w,self.config,2,110,'boot-one')
        self.assertEqual(self.w.post_to_feishu.call_count,1)
        self.assertIn('测试关键词',self.w.post_to_feishu.call_args.args[2])
        self.notifier.send(self.w,self.config,2,120,'boot-two')
        self.assertEqual(self.w.post_to_feishu.call_count,2)
    def test_failure_does_not_mark_sent_and_throttles_attempts(self):
        self.w.post_to_feishu.side_effect=RuntimeError('test failure')
        with self.assertRaises(RuntimeError):self.notifier.send(self.w,self.config,2,100,'boot-one')
        self.assertFalse(self.notifier.send(self.w,self.config,2,110,'boot-one'))
        self.assertIsNone(self.db.execute('SELECT sent_at FROM startup_notice').fetchone()[0])
        self.w.post_to_feishu.side_effect=None
        self.assertTrue(self.notifier.send(self.w,self.config,2,160,'boot-one'))
    def test_time_guard_blocks_notice(self):
        self.guard.side_effect=RuntimeError('test invalid clock')
        with self.assertRaises(RuntimeError):self.notifier.send(self.w,self.config,2,100,'boot-one')
        self.w.post_to_feishu.assert_not_called()
    def test_collection_failure_cannot_report_healthy(self):
        def check(*args,**kwargs):self.db.execute("INSERT INTO events VALUES('collection_error','1')");return 0
        self.w.check_once=check
        install_startup_notice(self.w,self.db,self.guard)
        self.w.check_once(self.config,None)
        self.w.post_to_feishu.assert_not_called()
    def test_all_users_required_then_only_one_notice(self):
        def check(*args,**kwargs):
            self.db.executemany('INSERT INTO events VALUES(?,?)',[('checked','1'),('empty','2')]);return 0
        self.w.check_once=check
        with patch('startup_notice.StartupNotifier.send',return_value=True) as send:
            install_startup_notice(self.w,self.db,self.guard)
            self.w.check_once(self.config,None);self.w.check_once(self.config,None)
            send.assert_called_once()
    def test_partial_check_and_dry_run_do_not_send(self):
        def check(*args,**kwargs):self.db.execute("INSERT INTO events VALUES('checked','1')");return 0
        self.w.check_once=check
        install_startup_notice(self.w,self.db,self.guard)
        self.w.check_once(self.config,None);self.w.check_once(self.config,None,dry_run=True)
        self.w.post_to_feishu.assert_not_called()

if __name__=='__main__':unittest.main()

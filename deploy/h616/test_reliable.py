import tempfile, unittest
from pathlib import Path
from reliable import Outbox, fetch_pages
from types import SimpleNamespace
class Tests(unittest.TestCase):
    def test_durable_retry_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'queue.db'; b=Outbox(p)
            b.record('u','2',[('u','2','t','b')]); b.record('u','2',[('u','2','t','b')])
            b.db.close(); b=Outbox(p)
            self.assertEqual(b.db.execute('select count(*) from queue').fetchone()[0],1)
            def fail(*a): raise OSError('network')
            b.deliver(fail,100)
            calls=[];b.deliver(lambda *a:calls.append(a),129);self.assertEqual(calls,[])
            b.deliver(lambda *a:calls.append(a),130);self.assertEqual(len(calls),1)
            b.deliver(lambda *a:calls.append(a),999);self.assertEqual(len(calls),1)
            b.db.close()
    def test_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            b=Outbox(Path(d)/'q')
            with self.assertRaises(Exception): b.record('u','2',[('u','2',None)])
            self.assertEqual(b.db.execute('select count(*) from cursor').fetchone()[0],0)
            b.db.close()
    def test_pages(self):
        pages=[{'code':0,'data':{'items':[{'id_str':'3'}],'has_more':True,'offset':'x'}}, {'code':0,'data':{'items':[{'id_str':'2'},{'id_str':'1'}],'has_more':False}}]
        w=SimpleNamespace(BILIBILI_DYNAMIC_URL='url',BILIBILI_DYNAMIC_FEATURES='',WatcherError=RuntimeError,strip_bilibili_read_state_cookies=lambda c:c, sorted_feed_dynamics=lambda x:x,is_pinned_dynamic=lambda x:False,request_bilibili_json=lambda *a,**k:pages.pop(0))
        self.assertEqual(len(fetch_pages(w,SimpleNamespace(uid='u'),{},'1')),3)
        self.assertEqual(pages,[])
    def test_incomplete_history_rejected(self):
        w=SimpleNamespace(BILIBILI_DYNAMIC_URL='url',BILIBILI_DYNAMIC_FEATURES='',WatcherError=RuntimeError,strip_bilibili_read_state_cookies=lambda c:c,sorted_feed_dynamics=lambda x:x,is_pinned_dynamic=lambda x:False,request_bilibili_json=lambda *a,**k:{'code':0,'data':{'items':[{'id_str':'3'}],'has_more':True,'offset':'x'}})
        with self.assertRaises(RuntimeError): fetch_pages(w,SimpleNamespace(uid='u'),{'recovery_max_pages':1},'1')
if __name__=='__main__':unittest.main()

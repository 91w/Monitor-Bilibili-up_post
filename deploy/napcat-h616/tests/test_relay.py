# Resolve source modules after the delivery directory organization.
import sys
from pathlib import Path
_project=Path(__file__).resolve().parents[1]
for _source in (_project/'board/relay',_project/'board/maintenance'):
    if _source.is_dir():sys.path.insert(0,str(_source))

import copy,hashlib,hmac,json,tempfile,threading,time,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import HTTPServer
import queue_store as q
import relay_service as r

CFG={'allowed_group_ids':['123'],'include_owner':False,'start_epoch':100,'feishu_webhook':'https://open.feishu.cn/open-apis/bot/v2/hook/test','self_id':'7','event_token':'local-test-only','api_token':'local-test-only','enabled':True}
def event(mid=9,**extra):
    v={'post_type':'message','message_type':'group','group_id':123,'self_id':7,'user_id':8,'message_id':mid,'time':101,
       'sender':{'user_id':8,'role':'admin','nickname':'test'},'message':[{'type':'text','data':{'text':'test text'}}]}
    v.update(extra);return v

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.path=self.base/'box.sqlite';self.box=q.Outbox(self.path)
    def tearDown(self):self.box.db.close();self.tmp.cleanup()
    def test_filter_before_disk(self):
        for e in [event(group_id=456),event(sender={'user_id':8,'role':'member'}),event(sender={'user_id':8,'role':'owner'}),event(message_type='private'),event(time=99),event(user_id=7),event(anonymous={'id':1})]:
            self.assertEqual(self.box.accept(e,CFG),'filtered')
        self.assertEqual(self.box.status()['total'],0)
    def test_durable_dedup_and_restart(self):
        self.assertEqual(self.box.accept(event(),CFG),'queued');self.box.db.close();self.box=q.Outbox(self.path)
        self.assertEqual(self.box.accept(event(),CFG),'duplicate');self.assertIsNotNone(self.box.next())
    def test_retry_does_not_discard(self):
        self.box.accept(event(),CFG)
        def fail(x):raise OSError('secret should not be stored')
        self.assertFalse(q.deliver_one(self.box,CFG,lambda *a:[{'text':'one'}],fail,lambda:None))
        row=self.box.db.execute('SELECT * FROM messages').fetchone()
        self.assertEqual(row['attempts'],1);self.assertIsNone(row['sent']);self.assertEqual(row['last_error'],'OSError')
        self.assertGreater(row['next_at'],time.time());self.assertNotIn('secret',row['last_error'])
    def test_partial_delivery_restart_resumes_next_part(self):
        self.box.accept(event(),CFG);calls=[]
        def send(part):
            if part['text']=='two':raise OSError()
            calls.append(part['text'])
        q.deliver_one(self.box,CFG,lambda *a:[{'text':'one'},{'text':'two'}],send,lambda:None)
        self.box.db.close();self.box=q.Outbox(self.path)
        with self.box.db:self.box.db.execute('UPDATE messages SET next_at=0')
        self.assertTrue(q.deliver_one(self.box,CFG,lambda *a:self.fail('plan must persist'),lambda p:calls.append(p['text']),lambda:None))
        self.assertEqual(calls,['one','two']);self.assertIsNone(self.box.next())
    def test_clock_blocks_before_prepare_or_send(self):
        self.box.accept(event(),CFG)
        def denied():raise RuntimeError()
        self.assertFalse(q.deliver_one(self.box,CFG,lambda *a:self.fail(),lambda *a:self.fail(),denied))
        self.assertIsNone(self.box.db.execute('SELECT sent FROM messages').fetchone()[0])
    def test_recipient_change_blocks(self):
        self.box.accept(event(),CFG);cfg=dict(CFG,feishu_webhook=CFG['feishu_webhook']+'other')
        self.assertFalse(q.deliver_one(self.box,cfg,lambda *a:self.fail(),lambda *a:self.fail(),lambda:None))
    def test_retention_preserves_pending_and_tombstone(self):
        self.box.accept(event(),CFG);self.box.accept(event(10),CFG);key=q.event_key(event())
        self.box.prepare(key,[{'text':'one'}]);self.box.complete(key);self.box.expire(time.time()+8*86400)
        row=self.box.db.execute('SELECT * FROM messages WHERE key=?',(key,)).fetchone()
        self.assertEqual(row['payload'],'{}');self.assertIsNone(row['plan']);self.assertEqual(self.box.accept(event(),CFG),'duplicate')
        self.assertIsNotNone(self.box.next());self.box.expire(time.time()+91*86400);self.assertEqual(self.box.status()['total'],1)
    def test_image_key_cache_expires_without_pending_loss(self):
        self.box.accept(event(),CFG)
        with self.box.db:
            self.box.db.execute('CREATE TABLE image_uploads(cache_key TEXT PRIMARY KEY,image_key TEXT,uploaded_at REAL)')
            self.box.db.execute('INSERT INTO image_uploads VALUES(?,?,?)',('hash','test-image-key',time.time()))
        self.box.expire(time.time()+8*86400)
        self.assertEqual(self.box.db.execute('SELECT COUNT(*) FROM image_uploads').fetchone()[0],0)
        self.assertEqual(self.box.status()['pending'],1)
    def test_keyword_filter(self):
        self.assertEqual(self.box.accept(event(),dict(CFG,include_keywords=['missing'])),'filtered')
        self.assertEqual(self.box.accept(event(),dict(CFG,exclude_keywords=['test'])),'filtered')
        self.assertEqual(self.box.accept(event(),dict(CFG,include_keywords=['text'])),'queued')
    def test_long_message_no_truncation(self):
        msg=q.selected_message(event(message=[{'type':'text','data':{'text':'甲'*9000}}]),CFG)
        plan=r.make_plan('test',msg);self.assertGreater(len(plan),2)
        self.assertEqual(''.join(p['text'] for p in plan).count('甲'),9000)
    def test_unapproved_media_host(self):
        with self.assertRaises(ValueError):r.store_image('https://localhost/private',self.base/'out.image')
        self.assertFalse((self.base/'out.image').exists())
    def test_local_media_saved_independently(self):
        with patch.object(r,'ROOT',self.base/'root'):
            p=self.base/'root/home/napcat/.config/QQ/nt_data/Pic/test.png';p.parent.mkdir(parents=True);data=b'\x89PNG\r\n\x1a\n'+b'0'*100;p.write_bytes(data)
            dest=self.base/'pending/test.image';r.store_image('/home/napcat/.config/QQ/nt_data/Pic/test.png',dest);p.unlink()
            self.assertEqual(dest.read_bytes(),data)
    def test_history_only_target_groups_and_baseline(self):
        calls=[]
        def api(cfg,action,args):
            calls.append((action,args))
            return {'messages':[event(1,time=99),event(2),event(3,sender={'user_id':8,'role':'member'})]}
        with patch.object(r,'api',api),patch.object(r,'ensure_time',lambda:None):r.catch_up(self.box,CFG)
        self.assertEqual(self.box.status()['total'],1);self.assertTrue(self.box.get('history_status:123')['complete'])
        self.assertTrue(all(v['group_id']=='123' for _,v in calls))
    def test_feishu_rejection_remains_pending(self):
        self.box.accept(event(),CFG)
        with patch.object(r,'ensure_time',lambda:None),patch.object(r.requests,'post') as post:
            post.return_value.json.return_value={'code':19021}
            self.assertFalse(q.deliver_one(self.box,CFG,lambda *a:[{'text':'x'}],r.Sender(CFG,self.box),lambda:None))
        self.assertEqual(self.box.status()['pending'],1)
    def test_history_error_does_not_advance_cursor(self):
        with patch.object(r,'ensure_time',lambda:None),patch.object(r,'api',side_effect=OSError):
            with self.assertRaises(OSError):r.catch_up(self.box,CFG)
        self.assertIsNone(self.box.get('history:123'))
    def test_short_history_page_does_not_imply_end(self):
        pages=[{'messages':[event(1),event(2)]},{'messages':[event(3,time=99)]}]
        with patch.object(r,'ensure_time',lambda:None),patch.object(r,'api',side_effect=pages) as api:
            r.catch_up(self.box,CFG)
        self.assertEqual(api.call_count,2);self.assertEqual(self.box.status()['total'],2)
        self.assertTrue(self.box.get('history_status:123')['complete'])
    def test_authenticated_http_commit(self):
        # Real HTTP request; acceptance is committed before success is returned.
        db=self.base/'http.sqlite';body=json.dumps(event()).encode();server=HTTPServer(('127.0.0.1',0),r.Handler)
        with patch.object(r,'config',lambda:CFG),patch.object(r,'DB',db):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url='http://127.0.0.1:'+str(server.server_port)+'/events'
                request=urllib.request.Request(url,body,headers={'x-signature':'bad'})
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request,timeout=3)
                self.assertEqual(error.exception.code,403);self.assertFalse(db.exists())
                signature='sha1='+hmac.new(CFG['event_token'].encode(),body,hashlib.sha1).hexdigest()
                with urllib.request.urlopen(urllib.request.Request(url,body,headers={'x-signature':signature}),timeout=3) as response:self.assertEqual(response.status,200)
                check=q.Outbox(db)
                try:self.assertEqual(check.status()['pending'],1)
                finally:check.db.close()
            finally:server.shutdown();thread.join(3);server.server_close()

if __name__=='__main__':unittest.main()

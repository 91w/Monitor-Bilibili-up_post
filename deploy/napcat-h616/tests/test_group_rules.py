import sys,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
project=Path(__file__).resolve().parents[1]
if (project/'board/relay').is_dir():sys.path.insert(0,str(project/'board/relay'))
import queue_store as q
import relay_service as relay
from message_rules import is_sticker

CFG={'allowed_group_ids':['200000002','200000001'],'start_epoch':100,'include_owner':False,
     'feishu_webhook':'https://open.feishu.cn/open-apis/bot/v2/hook/test',
     'exclude_keywords':['王座专属'],
     'group_rules':{'200000002':{'drop_stickers':True,'merge_text':True,'merge_idle_seconds':5,'merge_max_seconds':20}}}
def event(mid=1,text='第一句',group='200000002',uid=8,stamp=101,segments=None):
    return {'post_type':'message','message_type':'group','group_id':group,'self_id':7,'user_id':uid,
            'message_id':mid,'time':stamp,'sender':{'user_id':uid,'role':'admin','nickname':'test'},
            'message':segments if segments is not None else [{'type':'text','data':{'text':text}}]}
def segment(kind,**data):return {'type':kind,'data':data}

class GroupRulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'queue.sqlite';self.box=q.Outbox(self.path)
    def tearDown(self):self.box.db.close();self.tmp.cleanup()
    def accept(self,e,now=1000):
        with patch.object(q.time,'time',return_value=now):return self.box.accept(e,CFG)
    def payload(self,now):
        row=self.box.next(now,cfg=CFG)
        return json.loads(row['payload']) if row else None
    def test_sticker_metadata(self):
        for s in [segment('face',id=1),segment('mface'),segment('image',sub_type=1),
                  segment('image',sub_type='1'),segment('image',emoji_id='abc'),
                  segment('image',emoji_package_id=44),segment('image',summary='[动画表情]',sub_type=11),
                  segment('image',url='https://gxh.vip.qq.com/club/item/parcel/item/ab/id/raw300.gif')]:
            self.assertTrue(is_sticker(s),s)
            self.assertEqual(self.accept(event(segments=[s])),'filtered')
        self.assertEqual(self.box.status()['total'],0)
    def test_ordinary_images_kept_including_gif(self):
        for s in [segment('image',sub_type=0,file='a.gif'),segment('image',file='small.png'),
                  segment('image',sub_type=2,summary='图表'),segment('image',url='https://gxh.vip.qq.com.evil.test/club/item/parcel/item/a')]:
            self.assertFalse(is_sticker(s));self.assertIsNotNone(q.selected_message(event(segments=[s]),CFG))
    def test_mixed_text_kept_sticker_removed(self):
        msg=q.selected_message(event(segments=[segment('text',text='原文'),segment('image',sub_type=1)]),CFG)
        self.assertEqual(msg['segments'],[segment('text',text='原文')])
    def test_reply_sticker_only_discarded(self):
        self.assertEqual(self.accept(event(segments=[segment('reply',id=3),segment('face',id=2),segment('text',text=' ')])),'filtered')
    def test_other_group_unchanged(self):
        self.accept(event(group='200000001',segments=[segment('face',id=2)]))
        self.assertIsNotNone(self.payload(1000));self.assertEqual(self.box.status()['buffering'],0)
    def test_idle_and_restart(self):
        self.accept(event(),1000);self.accept(event(2,'第二句',stamp=104),1003)
        self.assertIsNone(self.payload(1007));self.assertEqual(self.box.status()['pending'],1)
        self.box.db.close();self.box=q.Outbox(self.path)
        msg=self.payload(1008);self.assertEqual(msg['merged_count'],2)
        self.assertEqual(msg['segments'][0]['data']['text'],'第一句\n第二句')
    def test_duplicate_does_not_extend(self):
        self.accept(event(),1000);self.assertEqual(self.accept(event(),1004),'duplicate')
        self.assertIsNotNone(self.payload(1005))
    def test_maximum_twenty_seconds(self):
        for i in range(5):self.accept(event(i+1,str(i),stamp=101+i*4),1000+i*4)
        self.assertIsNone(self.payload(1019));self.assertEqual(self.payload(1020)['merged_count'],5)
    def test_group_and_sender_isolation(self):
        self.accept(event(),1000);self.accept(event(2,uid=9,stamp=102),1001)
        self.assertEqual(self.payload(1001)['user_id'],'8')
        self.box.complete(q.event_key(event()))
        self.assertEqual(self.payload(1006)['user_id'],'9')
    def test_long_history_gap_not_combined(self):
        self.accept(event(),1000);self.accept(event(2,stamp=200),1001)
        self.assertEqual(self.payload(1001)['message_id'],'1');self.assertNotIn('merged_count',self.payload(1006))
    def test_claim_and_retry_cannot_change(self):
        self.accept(event(),1000);row=self.box.next(1005,cfg=CFG)
        self.box.prepare(row['key'],[{'text':'frozen'}]);self.box.fail(row,'OSError')
        self.accept(event(2,stamp=102),1006)
        stored=self.box.db.execute('SELECT * FROM messages WHERE key=?',(row['key'],)).fetchone()
        self.assertEqual(json.loads(stored['payload'])['message_id'],'1');self.assertEqual(stored['plan'],'[{"text": "frozen"}]')
        self.assertEqual(self.box.status()['pending'],2)
    def test_cross_fragment_keyword(self):
        self.accept(event(text='王座'),1000);self.accept(event(2,'专属',stamp=102),1001)
        # Joining with a newline must not permit split keywords to bypass the exclusion.
        self.assertIsNone(self.payload(1006));self.assertEqual(self.box.status()['discarded'],1)
        self.assertEqual(self.box.status()['pending'],0)
    def test_event_dedup_after_batch_complete(self):
        self.accept(event(),1000);self.accept(event(2,stamp=102),1001)
        row=self.box.next(1006,cfg=CFG);self.box.complete(row['key'])
        self.assertEqual(self.accept(event(2,stamp=102),1010),'duplicate')
    def test_image_closes_batch(self):
        self.accept(event(),1000);self.accept(event(2,segments=[segment('image',sub_type=0,file='real.png')]),1001)
        self.assertEqual(self.payload(1001)['message_id'],'1');self.assertEqual(self.box.status()['total'],2)
    def test_long_batches_split_without_truncation(self):
        self.accept(event(text='甲'*1400),1000);self.accept(event(2,'乙'*1400,stamp=102),1001)
        self.assertEqual(self.box.status()['total'],2)
        self.assertEqual(len(self.payload(1001)['segments'][0]['data']['text']),1400)
    def test_groups_do_not_close_each_other(self):
        self.accept(event(),1000);self.accept(event(2,group='200000001'),1001)
        row=self.box.db.execute('SELECT closed,ready_at FROM messages WHERE key=?',(q.event_key(event()),)).fetchone()
        self.assertEqual(row['closed'],0);self.assertEqual(row['ready_at'],1005)
    def test_duplicate_after_restart_uses_every_source_id(self):
        self.accept(event(),1000);self.accept(event(2,stamp=102),1001)
        self.box.db.close();self.box=q.Outbox(self.path)
        self.assertEqual(self.accept(event(2,stamp=102),1004),'duplicate')
        self.assertEqual(self.payload(1006)['merged_count'],2)
    def test_event_tombstone_retention(self):
        self.accept(event(),1000);self.accept(event(2,stamp=102),1001)
        row=self.box.next(1006,cfg=CFG)
        with patch.object(q.time,'time',return_value=1007):self.box.complete(row['key'])
        self.box.expire(1007+8*86400);self.assertEqual(self.accept(event(2),2000),'duplicate')
        self.box.expire(1007+91*86400);self.assertEqual(self.box.db.execute('SELECT COUNT(*) FROM message_events').fetchone()[0],0)
    def test_legacy_migration(self):
        path=Path(self.tmp.name)/'old.sqlite';db=sqlite3.connect(path)
        db.execute('CREATE TABLE messages(key TEXT PRIMARY KEY,payload TEXT NOT NULL,target TEXT NOT NULL,created REAL NOT NULL,next_at REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,plan TEXT,part INTEGER NOT NULL DEFAULT 0,sent REAL,last_error TEXT)')
        db.commit();db.close();box=q.Outbox(path)
        try:self.assertEqual(box.accept(event(group='200000001'),CFG),'queued');self.assertIsNotNone(box.next())
        finally:box.db.close()
    def test_text_and_ordinary_image_single_post(self):
        msg=q.selected_message(event(segments=[segment('text',text='图表说明'),segment('image',sub_type=0,url='https://gchat.qpic.cn/chart.png')]),CFG)
        with patch.object(relay,'store_image',return_value='/saved/image.png'):
            plan=relay.make_plan('test',msg)
        self.assertEqual(len(plan),1);self.assertIn('图表说明',plan[0]['text']);self.assertEqual(plan[0]['images'],['/saved/image.png'])
        with patch.object(relay,'ensure_time'),patch.object(relay.requests,'post') as post,patch.object(relay.time,'sleep'):
            post.return_value.json.return_value={'code':0}
            sender=relay.Sender(CFG,self.box)
            with patch.object(sender,'image_key',return_value='test-image-key'):sender(plan[0])
            self.assertEqual(post.call_count,1)
            rows=post.call_args.kwargs['json']['content']['post']['zh_cn']['content']
            self.assertEqual([row[0]['tag'] for row in rows],['text','img'])
    def test_oversized_image_set_bounded_posts(self):
        msg=q.selected_message(event(segments=[segment('image',sub_type=0,url='https://gchat.qpic.cn/chart.png')]*10),CFG)
        with patch.object(relay,'store_image',return_value='/saved/image.png'):plan=relay.make_plan('test',msg)
        self.assertEqual(len(plan),2);self.assertEqual([len(p['images']) for p in plan],[9,1])

if __name__=='__main__':unittest.main()

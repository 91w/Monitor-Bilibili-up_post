import copy,json,unittest
from unittest.mock import Mock,patch
import relay as r
from message_identity import describe_all,seed

DAY='2026-11-13';UID=r.UIDS[0];SCOPE=r.SITE+'/group'
def msg(mid,record='archive-one',text='same text',ts=1794530000000,uid=UID):
    return {'id':mid,'record_id':record,'uid':uid,'ts':ts,'type':'text','text':text}
class Source:
    def __init__(self,items):self.items=items;self.offsets=[]
    def page(self,day,uid,offset):
        self.offsets.append(offset)
        return {'day':{'chat_key':'group'},'total':len(self.items),'items':copy.deepcopy(self.items[offset:offset+60])}
class IdentityTest(unittest.TestCase):
    def setUp(self):self.db=r.connect(':memory:')
    def tearDown(self):self.db.close()
    def scan(self,items):return r.scan(self.db,Source(items),DAY,UID,now=100)
    def states(self):return dict(self.db.execute('SELECT id,status FROM messages'))
    def test_reimport_new_id_does_not_resend_completed_message(self):
        old=msg('old');self.scan([old])
        self.db.execute("UPDATE messages SET status='sent'")
        self.scan([old,msg('new','archive-two')])
        self.assertEqual(self.states(),{'old':'sent','new':'duplicate'})
        sender=Mock()
        r.drain(self.db,{},sender=sender,now=100)
        sender.assert_not_called()
    def test_real_same_second_identical_messages_in_one_archive_are_retained(self):
        self.scan([msg('one'),msg('two')])
        self.assertEqual(list(self.states().values()),['pending','pending'])
        self.scan([msg('one'),msg('two'),msg('copy-one','archive-two'),msg('copy-two','archive-two')])
        self.assertEqual(self.states()['copy-one'],'duplicate')
        self.assertEqual(self.states()['copy-two'],'duplicate')
    def test_different_time_sender_or_text_is_not_collapsed(self):
        items=[msg('one'),msg('two','archive-two',ts=1794530001000),msg('three','archive-two',uid=r.UIDS[1]),msg('four','archive-two',text='different')]
        self.scan(items)
        self.assertTrue(all(state=='pending' for state in self.states().values()))
    def test_missing_record_falls_back_to_id_without_false_dedup(self):
        self.scan([msg('one',None),msg('two',None)])
        self.assertTrue(all(state=='pending' for state in self.states().values()))
    def test_signed_resource_query_is_not_identity_but_resource_path_is(self):
        base='https://www.yyscha.cn/application/QQ/serve.php?record=one&path=resources%2Fimages%2Fa.png&exp=1&sig=one'
        one=msg('one');one['html']=f'<div class="content"><img src="{base}"></div>'
        two=msg('two','archive-two');two['html']=one['html'].replace('record=one','record=two').replace('exp=1','exp=999').replace('sig=one','sig=two')
        three=msg('three','archive-two');three['html']=two['html'].replace('a.png','b.png')
        self.scan([one,two,three])
        self.assertEqual(self.states(),{'one':'pending','two':'duplicate','three':'quarantined'})
    def test_user_links_and_quote_body_are_part_of_identity(self):
        one=msg('one');one['html']='<div class="content"><a href="https://example.com/one">link</a></div>'
        two=msg('two','archive-two');two['html']=one['html'].replace('/one','/two')
        self.scan([one,two]);self.assertEqual(self.states()['two'],'pending')
    def test_seed_prefers_completed_copy_and_does_not_enqueue(self):
        items=[msg('old'),msg('copy','archive-two')]
        self.scan(items)
        self.db.execute("UPDATE messages SET status='retry' WHERE id='old'")
        self.db.execute("UPDATE messages SET status='sent' WHERE id='copy'")
        self.db.execute('DELETE FROM message_aliases');self.db.execute('DELETE FROM message_identities')
        seed(self.db,[{'scope':SCOPE,'identities':describe_all(items,DAY)}])
        sender=Mock()
        r.drain(self.db,{},sender=sender,now=100)
        sender.assert_not_called()
        self.assertEqual(self.states()['old'],'duplicate')
    def test_collision_in_overlap_triggers_one_full_snapshot(self):
        original=[msg('id-'+str(i),text='text-'+str(i),ts=1794530000000+i*1000) for i in range(100)]
        self.scan(original)
        self.db.execute("UPDATE messages SET status='sent'")
        source=Source(original+[msg('copy','archive-two',text='text-99',ts=1794530099000)])
        r.scan(self.db,source,DAY,UID,now=101)
        self.assertEqual(self.states()['copy'],'duplicate')
        self.assertIn(60,source.offsets)
    def test_duplicate_does_not_discard_original_attachment_retry(self):
        self.scan([msg('old')]);self.db.execute("UPDATE messages SET status='retry',attempts=2,error='attachment not ready'")
        self.scan([msg('old'),msg('copy','archive-two')])
        self.assertEqual(self.states()['old'],'retry')
        self.assertEqual(self.states()['copy'],'duplicate')
    def test_unpublished_new_day_does_not_create_a_fake_scope(self):
        source=r.Source({'site_cookie':'session=test'})
        empty={'ok':True,'day':{'key':DAY,'chat_key':'','message_count':0},'items':[],'total':0,'offset':0}
        with patch('relay.request_json',return_value=empty):
            self.assertEqual(r.scan(self.db,source,DAY,UID),0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM streams').fetchone()[0],0)
        bad=copy.deepcopy(empty);bad['day']['key']='2026-11-12'
        with patch('relay.request_json',return_value=bad):
            with self.assertRaises(r.Failure):source.page(DAY,UID,0)
        bad=copy.deepcopy(empty);bad.update(items=[msg('bad')],total=1)
        with patch('relay.request_json',return_value=bad):
            with self.assertRaises(r.Failure):source.page(DAY,UID,0)

if __name__=='__main__':unittest.main()

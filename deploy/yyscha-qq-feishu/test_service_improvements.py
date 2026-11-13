import io,json,unittest,wave
from datetime import datetime
from unittest.mock import Mock,patch
import relay as r
from file_feishu import FileClient,extract_files
from notification_polling import NotificationPolling,next_delay_seconds
from service_notifications import notify_ready
from test_notification_polling import SCHEDULE

URL='https://www.yyscha.cn/application/QQ/serve.php?record=example&path=resources%2Faudios%2Fexample.amr&exp=9999999999'

class Improvements(unittest.TestCase):
    def setUp(self):self.db=r.connect(':memory:')
    def tearDown(self):self.db.close()

    def test_voice_extract_and_readable_label(self):
        item={'uid':r.UIDS[2],'type':'voice','html':f'<div class="content"><div class="voice-bubble" data-src="{URL}" data-name="wrong.amr">10"</div></div>'}
        files=extract_files(item)
        self.assertEqual(files[0]['kind'],'audio')
        self.assertEqual(files[0]['name'],'语音.wav')
        self.assertIn('语音：10秒',r.message_parts(item,'2026-11-13')['body'])
        self.assertNotIn('非纯文字',r.message_parts(item,'2026-11-13')['body'])
        self.assertEqual(extract_files({'html':item['html'].replace('www.yyscha.cn','evil.test')}),[])

    def test_wav_actual_format_determines_filename_and_duration(self):
        file=io.BytesIO()
        with wave.open(file,'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000);audio.writeframes(b'\0'*489600)
        file.seek(0)
        client=FileClient({'feishu_chat_id':'oc_test'},self.db,Mock())
        with patch.object(client,'download',return_value=file),patch.object(client,'_upload_stream',return_value='file_example') as uploaded:
            self.assertEqual(client.upload({'url':URL,'kind':'audio','name':'wrong.amr'}),'file_example')
            self.assertEqual(uploaded.call_args.args[1],'语音-10.2秒.wav')

    def test_dense_window_is_not_skipped(self):
        at=lambda h,m,s:datetime(2026,11,13,h,m,s,tzinfo=r.TZ)
        self.assertEqual(next_delay_seconds(SCHEDULE,at(9,0,50)),40)
        self.assertEqual(next_delay_seconds(SCHEDULE,at(9,3,55)),155)
        self.assertEqual(next_delay_seconds(SCHEDULE,at(9,2,0)),25)
        self.assertEqual(next_delay_seconds(SCHEDULE,at(8,58,0)),210)

    def test_time_guard_wait_does_not_accumulate_network_backoff(self):
        gate=NotificationPolling();probe=Mock(side_effect=r.TimeNotReady('test clock waiting'))
        config={'uids':['u_test']}
        for now in (100,115,130):
            with self.assertRaises(r.TimeNotReady):gate.tick(self.db,None,config,now=now,probe_fn=probe)
            self.assertEqual(gate.next_probe,now+15)
            self.assertEqual(gate.failures,0)

    def test_boot_ready_notice_dedup_and_failed_delivery(self):
        sender=Mock();config={'notification_polling':True}
        notify_ready(self.db,config,now=100,boot_id='boot-one',sender=sender)
        self.assertIn('首次启用',sender.call_args.args[1])
        notify_ready(self.db,config,now=110,boot_id='boot-one',sender=sender)
        self.assertEqual(sender.call_count,1)
        notify_ready(self.db,config,now=120,boot_id='boot-two',sender=sender)
        self.assertEqual(sender.call_count,2)
        self.assertIn('重启后已恢复',sender.call_args.args[1])
        sender.side_effect=r.Failure('test notification failure')
        with self.assertRaises(r.Failure):notify_ready(self.db,config,now=130,boot_id='boot-three',sender=sender)
        saved=json.loads(self.db.execute("SELECT value FROM meta WHERE key='service_ready_notice'").fetchone()[0])
        self.assertEqual(saved['boot_id'],'boot-two')

    def test_quote_placeholder_uses_existing_message_cache(self):
        original={'body':'cached original text','message_time':'11-13 11:29:00'}
        scope=r.SITE+'/group'
        self.db.execute("INSERT INTO messages(scope,id,day,uid,ts,body,status,parts_json) VALUES(?,?,?,?,?,?,?,?)",(scope,'original','2026-11-13',r.UIDS[0],1,'old','sent',json.dumps(original)))
        item={'id':'reply','uid':r.UIDS[2],'type':'text','ts':2,'html':'<div class="reply-content" data-reply-to="original"><div class="reply-content-text">原消息</div></div><div class="content">reply</div>'}
        source=Mock();source.page.return_value={'day':{'chat_key':'group'},'items':[item],'total':1}
        r.scan(self.db,source,'2026-11-13',r.UIDS[2],now=100)
        row=self.db.execute("SELECT parts_json,observed_at FROM messages WHERE id='reply'").fetchone()
        self.assertEqual(json.loads(row[0])['quote']['text'],'cached original text')
        self.assertEqual(row[1],100)


if __name__=='__main__':unittest.main()

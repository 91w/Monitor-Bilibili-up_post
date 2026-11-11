import json,sqlite3,tempfile,unittest,logging
from pathlib import Path
from unittest.mock import Mock,patch
from dynamic_images import ImageClient,ImagePreparationError,collect_image_urls,image_url,dynamic_media
from reliable import Outbox
class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.previous_logging=logging.root.manager.disable;logging.disable(logging.CRITICAL)
    @classmethod
    def tearDownClass(cls):logging.disable(cls.previous_logging)
    def test_extract_dedup_and_ignore_avatar(self):
        url='https://i0.hdslb.com/bfs/new_dyn/test.png'
        d={'modules':{'module_author':{'face':url+'avatar'},'module_dynamic':{'major':{'draw':{'items':[{'src':url},{'src':url}]},'opus':{'pics':[{'url':'//i1.hdslb.com/bfs/new_dyn/2.png'}]}}}}}
        self.assertEqual(collect_image_urls(d),[url,'https://i1.hdslb.com/bfs/new_dyn/2.png'])
        legacy={'card':json.dumps({'item':{'pictures':[{'img_src':url}]}})}
        self.assertEqual(collect_image_urls(legacy),[url])
        self.assertEqual(collect_image_urls({'orig':d}),collect_image_urls(d))
    def test_address_restrictions(self):
        for u in ['https://evil.example/test.png','https://hdslb.com.evil.example/x','https://127.0.0.1/x','file:///etc/passwd','https://i0.hdslb.com:8443/x']:
            self.assertIsNone(image_url(u))
    def test_text_only_opus_does_not_trigger_image_requests(self):
        watcher=Mock()
        self.assertEqual(dynamic_media(watcher,{}, {'type':'MAJOR_TYPE_OPUS','id':'1'},'u','name',None),([],False))
        watcher.request_dynamic_detail.assert_not_called()
    def test_schema_migration_and_persisted_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'q';c=sqlite3.connect(p)
            c.execute('CREATE TABLE queue(uid TEXT,id TEXT,title TEXT,body TEXT,attempts INTEGER DEFAULT 0,due REAL DEFAULT 0,sent INTEGER DEFAULT 0,PRIMARY KEY(uid,id))')
            c.execute("INSERT INTO queue(uid,id,title,body) VALUES('u','old','t','b')");c.commit();c.close()
            box=Outbox(p)
            self.assertEqual(box.db.execute("SELECT images_json FROM queue WHERE id='old'").fetchone()[0],'[]')
            box.record('u','new',[('u','new','t','b',['https://i0.hdslb.com/x'])]);box.db.close()
            box=Outbox(p);self.assertIn('https://i0.hdslb.com/x',box.db.execute("SELECT images_json FROM queue WHERE id='new'").fetchone()[0]);box.db.close()
    def test_image_retry_does_not_confirm_then_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            box=Outbox(Path(tmp)/'q');box.record('u','1',[('u','1','t','b',['https://i0.hdslb.com/x'])])
            def send(t,b,urls,fallback):
                if not fallback:raise ImagePreparationError('retry')
            for now in (100,130,190):box.deliver(send,now)
            self.assertEqual(box.db.execute('SELECT sent,media_failures FROM queue').fetchone(),(0,3))
            box.deliver(send,310);self.assertEqual(box.db.execute('SELECT sent FROM queue').fetchone()[0],1);box.db.close()
    def test_no_plain_fallback_on_webhook_network_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            box=Outbox(Path(tmp)/'q');box.record('u','1',[('u','1','t','b',['https://i0.hdslb.com/x'])])
            def send(*a):raise OSError('network')
            box.deliver(send,100)
            self.assertEqual(box.db.execute('SELECT sent,media_failures FROM queue').fetchone(),(0,0));box.db.close()
    def test_upload_cache_survives_client_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=sqlite3.connect(Path(tmp)/'cache');cfg={'feishu_app_id':'cli_test','feishu_app_secret':'secret','_image_temp_dir':tmp}
            client=ImageClient(cfg,db,lambda:None,lambda *a:'sig')
            f=tempfile.TemporaryFile();f.write(b'image');f.seek(0)
            response=Mock();response.json.return_value={'code':0,'data':{'image_key':'img_test'}}
            with patch.object(client,'download',return_value=(f,'image/png')),patch.object(client,'access_token',return_value='token'),patch('dynamic_images.requests.post',return_value=response) as post:
                self.assertEqual(client.upload('https://i0.hdslb.com/x'),'img_test')
                new=ImageClient(cfg,db,lambda:None,lambda *a:'sig')
                self.assertEqual(new.upload('https://i0.hdslb.com/x'),'img_test');self.assertEqual(post.call_count,1)
            db.close()
    def test_card_and_failed_images_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=sqlite3.connect(':memory:');cfg={'feishu_webhook':'https://unused.invalid','feishu_secret':'secret','_image_temp_dir':tmp}
            client=ImageClient(cfg,db,lambda:None,lambda *a:'signature')
            response=Mock();response.json.return_value={'code':0}
            with patch.object(client,'upload',side_effect=['img_one',ImagePreparationError('failure')]),patch('dynamic_images.requests.post',return_value=response) as post:
                client.send('title','body',['https://i0.hdslb.com/a','https://i0.hdslb.com/b'],True)
                p=post.call_args.kwargs['json'];self.assertEqual(p['card']['elements'][1]['img_key'],'img_one');self.assertEqual(p['card']['elements'][-1]['tag'],'note');self.assertEqual(p['sign'],'signature')
            db.close()
    def test_download_size_limit_and_no_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=ImageClient({},sqlite3.connect(':memory:'),lambda:None,None);client.scratch=Path(tmp)
            response=Mock();response.status_code=200;response.headers={'Content-Type':'image/png','Content-Length':str(10*1024*1024)}
            with patch('dynamic_images.requests.get',return_value=response) as get:
                with self.assertRaises(ImagePreparationError):client.download('https://i0.hdslb.com/x')
                self.assertNotIn('Cookie',get.call_args.kwargs['headers']);response.close.assert_called_once()
if __name__=='__main__':unittest.main()

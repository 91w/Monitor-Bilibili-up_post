import io
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request, build_opener, ProxyHandler

import relay as r
from file_feishu import FileClient, MultipartBody, extract_files, clean_name, refresh_files, LIMIT, CHUNK

URL = 'https://www.yyscha.cn/application/QQ/serve.php?record=x&path=resources%2Ffiles%2F'+'a'*32+'_term.md&exp=9999999999&sig=private'
FILE = {'url':URL,'name':'术语表(1).md','context':'body'}


class Response(io.BytesIO):
    def __init__(self,data,mime='text/markdown',length=None):
        super().__init__(data)
        self.headers={'Content-Type':mime}
        if length is not None:
            self.headers['Content-Length']=str(length)


class FileTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.config={'feishu_chat_id':'oc_test','feishu_app_id':'cli_test'}
        self.auth=Mock()
        self.auth.access_token.return_value='test-token'
        self.client=FileClient(self.config,self.db,self.auth)

    def tearDown(self):
        self.db.close()

    def test_extract_only_file_bubbles_and_preserve_filename_context(self):
        html=f'<a href="{URL}">ordinary link</a><div class="reply-content"><a class="message-file file-bubble" href="{URL}"><div class="fmeta"><div class="fname">术语表(1).md</div><div>124.9 KB</div></div></a></div>'
        files=extract_files({'html':html})
        self.assertEqual(files,[dict(FILE,context='quote')])
        self.assertEqual(extract_files({'html':html.replace('www.yyscha.cn','evil.test')}),[])
        self.assertEqual(extract_files({'html':html.replace('resources%2Ffiles','resources%2Fimages')}),[])

    def test_safe_name(self):
        self.assertNotIn('\r',clean_name('x\r\nHeader: yes.md'))
        self.assertNotIn('/',clean_name('../../x.md'))

    def test_download_without_disposition_and_length(self):
        with patch('site_session.get_session') as session:
            session.return_value.open.return_value=Response('术语表'.encode())
            with self.client.download(FILE) as file:
                self.assertEqual(file.read(),'术语表'.encode())
            self.assertTrue(file.closed)
        with patch('site_session.get_session') as session:
            with self.assertRaises(r.Failure):
                with self.client.download(dict(FILE,url='https://evil.test/file')): pass
            session.assert_not_called()

    def test_reject_login_truncation_and_oversize(self):
        cases=[Response(b'<html>login</html>','application/octet-stream'),Response(b'login','text/html'),Response(b'abc',length=10),Response(b'x',length=LIMIT+1),Response(b'')]
        for response in cases:
            with self.subTest(response=response),patch('site_session.get_session') as session:
                session.return_value.open.return_value=response
                with self.assertRaises(r.Failure):
                    with self.client.download(FILE): pass

    def test_upload_cache_and_filename(self):
        opener=Mock()
        captured=[]
        def opened(req,**kwargs):
            captured.append(b''.join(req.data))
            return Response(json.dumps({'code':0,'data':{'file_key':'file_test'}}).encode())
        opener.open.side_effect=opened
        with patch.object(self.client,'download',return_value=io.BytesIO(b'contents')) as download,patch('file_feishu.build_opener',return_value=opener):
            self.assertEqual(self.client.upload(FILE),'file_test')
            self.assertEqual(self.client.upload(dict(FILE,url=URL.replace('private','rotated'))),'file_test')
            download.assert_called_once()
            req=opener.open.call_args.args[0]
            self.assertIn('术语表(1).md'.encode(),captured[0])
            self.assertIn(b'\r\nstream\r\n',captured[0])
            self.assertNotIn(b'sig=private',captured[0])
            self.assertEqual(int(req.get_header('Content-length')),len(captured[0]))

    def test_send_success_is_durable_and_retries_same_uuid(self):
        with patch.object(self.client,'upload',return_value='file_test'),patch('relay.request_json') as request:
            request.return_value={'code':230002}
            with self.assertRaises(r.Failure): self.client.send('scope','mid',FILE)
            first=request.call_args.kwargs['payload']
            request.return_value={'code':0,'data':{'message_id':'om_ok'}}
            self.client.send('scope','mid',FILE)
            self.assertEqual(first,request.call_args.kwargs['payload'])
            self.assertEqual(first['msg_type'],'file')
            request.reset_mock()
            FileClient(self.config,self.db,self.auth).send('scope','mid',FILE)
            request.assert_not_called()

    def test_missing_destination_does_not_download(self):
        self.config.pop('feishu_chat_id')
        with patch.object(self.client,'download') as download:
            with self.assertRaises(r.Failure): self.client.upload(FILE)
            download.assert_not_called()

    def test_refresh_keeps_exact_attachment_identity(self):
        old=dict(FILE,url=URL.replace('9999999999','1'))
        item={'id':'mid','html':f'<a class="message-file" href="{URL}">file</a>'}
        with patch('history_test.fetch_day',return_value=[item]):
            self.assertEqual(refresh_files({},'day','uid','mid',[old])[0]['url'],URL)
        with patch('history_test.fetch_day',return_value=[]):
            with self.assertRaises(r.Failure): refresh_files({},'day','uid','mid',[old])

    def test_card_not_repeated_after_file_send_failure(self):
        db=r.connect(':memory:')
        parts={'header':'sender','files':[FILE]}
        with db:
            db.execute("INSERT INTO messages(scope,id,day,uid,ts,body,status,media_json,parts_json) VALUES('s','m','2026-11-12','u',1,'file','pending','[]',?)",(json.dumps(parts),))
        image=Mock()
        results=[r.Failure('test transient failure'),None]
        with patch('file_feishu.FileClient.send',side_effect=results) as send,patch('relay.time.sleep'):
            r.drain(db,self.config,image_client=image,now=100)
            self.assertEqual(db.execute('SELECT status FROM messages').fetchone()[0],'retry')
            r.drain(db,self.config,image_client=image,now=200)
            self.assertEqual(db.execute('SELECT status FROM messages').fetchone()[0],'sent')
            self.assertEqual(send.call_count,2)
            image.send.assert_called_once()
        db.close()

    def test_30mb_boundary_and_unknown_length_overflow(self):
        class Generated(Response):
            def __init__(self,size):
                super().__init__(b'')
                self.remaining=size
            def read(self,size=-1):
                if not 0<size<=CHUNK:
                    raise AssertionError('unbounded read')
                count=min(size,self.remaining)
                self.remaining-=count
                return b'x'*count
        self.assertEqual(LIMIT,30_000_000)
        for size,success in [(LIMIT,True),(LIMIT+1,False)]:
            with self.subTest(size=size),patch('site_session.get_session') as session:
                session.return_value.open.return_value=Generated(size)
                if success:
                    with self.client.download(FILE) as file:
                        self.assertEqual(file.seek(0,2),LIMIT)
                else:
                    with self.assertRaises(r.Failure):
                        with self.client.download(FILE): pass

    def test_tempfile_cleanup_after_read_and_upload_errors(self):
        for upload_error in (False,True):
            tracked=tempfile.TemporaryFile('w+b')
            response=Response(b'content')
            if not upload_error:
                response.read=Mock(side_effect=OSError('test disk/network error'))
            with patch('site_session.get_session') as session,patch('file_feishu.tempfile.TemporaryFile',return_value=tracked):
                session.return_value.open.return_value=response
                with self.assertRaises(r.Failure):
                    with self.client.download(FILE):
                        raise r.Failure('test upload failure')
                self.assertTrue(tracked.closed)

    def test_upload_retry_rewinds_stream_and_failure_closes_tempfile(self):
        opener=Mock()
        captured=[]
        def opened(req,**kwargs):
            captured.append(b''.join(req.data))
            result={'code':99991663} if len(captured)==1 else {'code':0,'data':{'file_key':'file_ok'}}
            return Response(json.dumps(result).encode())
        opener.open.side_effect=opened
        file=io.BytesIO(b'payload-retry')
        with patch.object(self.client,'download',return_value=file),patch('file_feishu.build_opener',return_value=opener):
            self.assertEqual(self.client.upload(FILE),'file_ok')
        self.assertEqual(captured[0],captured[1])
        self.assertTrue(file.closed)

    def test_multipart_streams_over_real_http_with_content_length(self):
        received={}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                self.connection.settimeout(5)
                received['transfer_encoding']=self.headers.get('Transfer-Encoding')
                length=int(self.headers['Content-Length'])
                received['body']=self.rfile.read(length)
                self.send_response(200); self.end_headers(); self.wfile.write(b'OK')
        server=HTTPServer(('127.0.0.1',0),Handler)
        server.timeout=5
        thread=threading.Thread(target=server.handle_request,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryFile('w+b') as file:
                file.write(b'x'*(CHUNK*3+7))
                body=MultipartBody(file,'术语表.md')
                req=Request('http://127.0.0.1:'+str(server.server_port),data=body,headers={'Content-Type':'multipart/form-data; boundary='+body.boundary,'Content-Length':str(body.length)})
                with build_opener(ProxyHandler({})).open(req,timeout=5) as response:
                    self.assertEqual(response.read(),b'OK')
                self.assertEqual(len(received['body']),body.length)
                self.assertIn(b'x'*(CHUNK*3+7),received['body'])
                self.assertIsNone(received['transfer_encoding'])
        finally:
            thread.join(timeout=6)
            server.server_close()


if __name__=='__main__': unittest.main()

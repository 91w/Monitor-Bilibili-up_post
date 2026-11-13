"""Authenticated attachments, bounded upload and durable per-message delivery progress."""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import re
import tempfile
import time
import uuid
import wave
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError

from media_feishu import image_url, stable_ref

# Decimal MB, kept below either interpretation of the platform's 30 MB cap.
LIMIT = 30_000_000
CHUNK = 64 * 1024
TRANSFER_SECONDS = 300


class MultipartBody:
    """Replayable, fixed-length iterable; never materializes the attachment."""
    def __init__(self, file, name):
        self.file = file
        self.boundary = 'yyscha-file-' + uuid.uuid4().hex
        self.prefix = (f'--{self.boundary}\r\nContent-Disposition: form-data; name="file_type"\r\n\r\nstream\r\n'
                       f'--{self.boundary}\r\nContent-Disposition: form-data; name="file_name"\r\n\r\n{name}\r\n'
                       f'--{self.boundary}\r\nContent-Disposition: form-data; name="file"; filename="attachment.bin"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
        self.suffix = f'\r\n--{self.boundary}--\r\n'.encode()
        file.seek(0, 2)
        self.length = len(self.prefix) + file.tell() + len(self.suffix)
        file.seek(0)

    def __iter__(self):
        started = time.monotonic()
        self.file.seek(0)
        yield self.prefix
        while True:
            if time.monotonic() - started > TRANSFER_SECONDS:
                raise TimeoutError('attachment upload deadline')
            chunk = self.file.read(CHUNK)
            if not chunk:
                break
            yield chunk
        yield self.suffix


def clean_name(value):
    value = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', '_', str(value)).strip(' .')
    return value[:180] or 'attachment.bin'


def extract_files(item):
    import relay as r
    root = r.DOM(str(item.get('html') or '')).root
    files, seen = [], set()
    def visit(node, quoted=False):
        if node.tag in ('script', 'style', 'iframe'):
            return
        quoted = quoted or node.has('reply-content')
        if node.has('voice-bubble'):
            audio = image_url(node.attrs.get('data-src'))
            path = parse_qs(urlparse(audio or '').query).get('path', [''])[0]
            if audio and path.startswith('resources/audios/') and path not in seen:
                seen.add(path)
                duration = re.search(r'(\d+(?:\.\d+)?)\s*(?:"|秒)', node.plain())
                files.append({'url': audio, 'name': '语音.wav', 'kind': 'audio',
                              'duration_seconds': float(duration.group(1)) if duration else None,
                              'context': 'quote' if quoted else 'body'})
        url = image_url(node.attrs.get('href')) if node.tag == 'a' else None
        if url and (node.has('message-file') or node.has('file-bubble')):
            path = parse_qs(urlparse(url).query).get('path', [''])[0]
            if not path.startswith('resources/files/') or path in seen:
                return
            seen.add(path)
            name = node.find(lambda n: n.has('fname') or n.has('file-name'))
            # Some archive templates expose the name as the first fmeta child.
            meta = node.find(lambda n: n.has('fmeta'))
            if name is None and meta:
                name = next((n for n in meta.children if isinstance(n, r.Node)), None)
            fallback = PurePosixPath(path).name
            fallback = re.sub(r'^[0-9a-f]{32}_', '', fallback)
            files.append({'url': url, 'name': clean_name(name.plain().strip() if name else fallback),
                          'context': 'quote' if quoted else 'body'})
        for child in node.children:
            if isinstance(child, r.Node):
                visit(child, quoted)
    visit(root)
    return files


def refresh_files(config, day, uid, mid, files):
    import relay as r
    try:
        stale = any(float(parse_qs(urlparse(f['url']).query).get('exp', ['0'])[0]) <= time.time()+60 for f in files)
    except (ValueError, KeyError, TypeError):
        stale = True
    if not stale:
        return files
    from history_test import fetch_day
    item = next((x for x in fetch_day(r.Source(config), day, uid) if x['id'] == mid), None)
    fresh = extract_files(item) if item else []
    mapped = {stable_ref(x['url']): x for x in fresh}
    if any(stable_ref(x['url']) not in mapped for x in files):
        raise r.Failure('附件签名暂未刷新，保留队列等待重试')
    return [mapped[stable_ref(x['url'])] for x in files]


class FileClient:
    def __init__(self, config, db, image_client):
        self.config, self.db, self.auth = config, db, image_client
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS file_uploads(cache_key TEXT PRIMARY KEY,file_key TEXT,uploaded_at REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS attachment_delivery(delivery_key TEXT PRIMARY KEY,message_id TEXT,sent_at REAL)')

    def progress_key(self, scope, mid, ref):
        return hashlib.sha256(json.dumps([self.config.get('feishu_app_id'), self.config.get('feishu_chat_id'), scope, mid, ref], ensure_ascii=False).encode()).hexdigest()

    def done(self, key):
        return self.db.execute('SELECT 1 FROM attachment_delivery WHERE delivery_key=?', (key,)).fetchone() is not None

    def mark(self, key, message_id=''):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO attachment_delivery VALUES(?,?,?)', (key, message_id, time.time()))

    def require_destination(self):
        import relay as r
        if not re.fullmatch(r'oc_[a-zA-Z0-9]+', str(self.config.get('feishu_chat_id', ''))):
            raise r.Failure('文件发送尚未配置目标飞书群chat_id；消息保留等待配置')

    @contextmanager
    def download(self, attachment):
        import relay as r
        from site_session import get_session
        url = image_url(attachment.get('url'))
        path = parse_qs(urlparse(url or '').query).get('path', [''])[0]
        allowed_prefix = 'resources/audios/' if attachment.get('kind') == 'audio' else 'resources/files/'
        if not url or not path.startswith(allowed_prefix):
            raise r.Failure('附件地址不属于已验证的yyscha文件接口')
        r.ensure_time(self.config)
        try:
            directory = Path(self.config['_config_path']).parent if self.config.get('_config_path') else None
            # TemporaryFile is private (0600); on POSIX it is unlinked immediately.
            with tempfile.TemporaryFile(mode='w+b', dir=directory) as file:
                started = time.monotonic()
                with get_session(self.config).open(Request(url, headers={'Referer':r.SITE+'/application/QQ/'}), timeout=60) as response:
                    mime = response.headers.get('Content-Type', '').split(';')[0].lower()
                    length = response.headers.get('Content-Length')
                    if mime in ('text/html', 'application/json'):
                        raise r.Failure('附件返回登录或错误页面，等待刷新签名')
                    if length is not None and (int(length) < 0 or int(length) > LIMIT):
                        raise r.Failure('附件超过30MB限制，保留队列等待处理')
                    size, first = 0, b''
                    while True:
                        chunk = response.read(CHUNK)
                        if time.monotonic()-started > TRANSFER_SECONDS:
                            raise r.Failure('附件下载超过时间限制')
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > LIMIT:
                            raise r.Failure('附件超过30MB限制，保留队列等待处理')
                        if len(first) < 1024:
                            first += chunk[:1024-len(first)]
                        file.write(chunk)
                if not size or (length is not None and int(length) != size):
                    raise r.Failure('附件为空或下载不完整')
                first = first.lower().lstrip()
                if first.startswith((b'<!doctype html', b'<html')) or b'loginmodal' in first or b'/ailogin/' in first:
                    raise r.Failure('附件内容是登录或错误页面')
                file.seek(0)
                yield file
        except HTTPError as exc:
            if exc.code == 429:
                raise r.RateLimited(r.retry_delay(exc.headers.get('Retry-After'))) from None
            if exc.code == 404:
                raise r.Failure('源站附件返回HTTP404，暂无法下载；保留队列等待重试') from None
            raise r.Failure(f'附件下载HTTP{exc.code}，保留队列等待重试') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise r.Failure('附件下载失败，等待刷新链接并重试') from None

    def upload(self, attachment):
        import relay as r
        self.require_destination()
        name = clean_name(attachment.get('name', 'attachment.bin'))
        cache = self.progress_key('', '', stable_ref(attachment['url'])+'\n'+name)
        row = self.db.execute('SELECT file_key,uploaded_at FROM file_uploads WHERE cache_key=?', (cache,)).fetchone()
        if row and 0 <= time.time()-row[1] < 7*86400:
            return row[0]
        self.auth.access_token()
        with self.download(attachment) as file:
            if attachment.get('kind') == 'audio':
                first = file.read(64)
                file.seek(0)
                if first[:4] != b'RIFF' or first[8:12] != b'WAVE':
                    raise r.Failure('语音返回的格式暂未支持，保留队列；不按错误扩展名发送')
                try:
                    with wave.open(file, 'rb') as audio:
                        duration = audio.getnframes() / audio.getframerate()
                    file.seek(0)
                except (wave.Error, EOFError, ZeroDivisionError):
                    raise r.Failure('语音WAV格式无效，保留队列等待处理') from None
                name = f'语音-{duration:g}秒.wav'
            return self._upload_stream(file, name, cache)

    def _upload_stream(self, file, name, cache):
        import relay as r
        payload = MultipartBody(file, name)
        for attempt in range(2):
            req = Request('https://open.feishu.cn/open-apis/im/v1/files', data=payload, headers={'Authorization':'Bearer '+self.auth.access_token(), 'Content-Type':'multipart/form-data; boundary='+payload.boundary, 'Content-Length':str(payload.length)})
            try:
                with build_opener(ProxyHandler({}), r.NoRedirect).open(req, timeout=60) as response:
                    raw = response.read(1024*1024+1)
                if len(raw)>1024*1024:
                    raise ValueError
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError
            except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                raise r.Failure('飞书文件上传失败，消息保留等待重试') from None
            if result.get('code') in (99991663,99991664,99991668) and not attempt:
                self.auth.token = None
                continue
            key = (result.get('data') or {}).get('file_key')
            if type(result.get('code')) is not int or result['code'] != 0 or not isinstance(key,str) or not key:
                raise r.Failure('飞书拒绝文件上传，请检查资源权限和应用发布状态')
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO file_uploads VALUES(?,?,?)',(cache,key,time.time()))
            return key
        raise r.Failure('飞书文件上传认证失败')

    def send(self, scope, mid, attachment):
        import relay as r
        self.require_destination()
        delivery = self.progress_key(scope, mid, stable_ref(attachment['url']))
        if self.done(delivery):
            return
        key = self.upload(attachment)
        for attempt in range(2):
            r.ensure_time(self.config)
            result = r.request_json('https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id',
                headers={'Authorization':'Bearer '+self.auth.access_token()},
                payload={'receive_id':self.config['feishu_chat_id'], 'msg_type':'file',
                         'content':json.dumps({'file_key':key}), 'uuid':str(uuid.UUID(delivery[:32]))})
            if result.get('code') in (99991663,99991664,99991668) and not attempt:
                self.auth.token = None
                continue
            message_id = (result.get('data') or {}).get('message_id')
            if type(result.get('code')) is not int or result['code'] != 0 or not isinstance(message_id,str) or not message_id:
                raise r.Failure('飞书文件发送未确认，请检查发送权限、发布状态和机器人是否已入群')
            self.mark(delivery,message_id)
            return
        raise r.Failure('飞书文件发送认证失败')

"""yyscha authenticated image download and Feishu upload/card adapter.

Adapted from Monitor-Bilibili-up_post deploy/h616/dynamic_images.py.
Only stdlib; separate source allowlist, no changes to Bilibili components.
"""
import hashlib
import json
import re
import time
import uuid
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import URLError, HTTPError

LIMIT = 9 * 1024 * 1024


def clipped(text, size):
    raw = str(text).encode('utf-8')
    return raw[:size].decode('utf-8', errors='ignore') + ('\n[内容过长，已截断]' if len(raw) > size else '')


def card_header(parts):
    import relay as r
    # Style follows the monitored UID, so a nickname change cannot change color.
    profiles = {r.UIDS[0]: ('blue', '🍑'), r.UIDS[1]: ('green', '⚡'), r.UIDS[2]: ('purple', '🟣')}
    legacy = str(parts.get('header', '网页消息')).rpartition(' · ')
    name = str(parts.get('sender_name') or legacy[0] or legacy[2])
    stamp = str(parts.get('message_time') or (legacy[2] if legacy[1] else ''))
    uid = parts.get('sender_uid')
    if uid not in profiles:
        uid = {'示例用户一': r.UIDS[0], '示例用户二': r.UIDS[1], '示例用户三': r.UIDS[2]}.get(name)
    color, badge = profiles.get(uid, ('blue', '💬'))
    result = {'template': color, 'title': {'tag': 'plain_text', 'content': badge + ' ' + clipped(name, 200)}}
    if stamp:
        result['subtitle'] = {'tag': 'plain_text', 'content': clipped(stamp, 200)}
    return result


def card_elements(body, parts, keyed_images, keyword='', include_header=True):
    def text(value, limit=10000):
        return {'tag': 'div', 'text': {'tag': 'plain_text', 'content': clipped(value, limit)}}
    def image(key, context):
        return {'tag': 'img', 'img_key': key, 'alt': {'tag': 'plain_text', 'content': '引用配图' if context == 'quote' else '回复配图'}, 'mode': 'fit_horizontal', 'preview': True}
    if not parts or not parts.get('header'):
        return [text((keyword + '\n' + body).strip(), 14000)] + [image(key, context) for key, context in keyed_images]
    header = '\n'.join(x for x in [keyword, parts.get('test_label'), parts['header'] if include_header else ''] if x)
    elements = [text(header, 1800)] if header else []
    quote = parts.get('quote')
    if quote:
        author = '引用 · ' + quote.get('author', '未知')
        if quote.get('time'):
            author += ' · ' + quote['time']
        quote_hint = '[引用配图见下方]' if any(context == 'quote' for _, context in keyed_images) else '[引用图片未附]'
        quote_text = re.sub(r'\[图片[^\]\n]*\]', quote_hint, quote.get('text', ''))
        quoted = [text(author, 1000), text(quote_text or '[引用内容不可用]', 6000)]
        quoted += [image(key, context) for key, context in keyed_images if context == 'quote']
        elements.append({'tag': 'column_set', 'flex_mode': 'none', 'background_style': 'grey', 'columns': [
            {'tag': 'column', 'width': 'weighted', 'weight': 1, 'elements': quoted}]})
        elements.append({'tag': 'hr'})
    body_hint = '[配图见下方]' if any(context != 'quote' for _, context in keyed_images) else '[图片未附]'
    reply_text = re.sub(r'\[图片[^\]\n]*\]', body_hint, parts.get('body', ''))
    elements.append(text(('回复\n\n' if quote else '') + reply_text, 10000))
    elements += [image(key, context) for key, context in keyed_images if context != 'quote' or not quote]
    return elements


def image_url(value):
    if not isinstance(value, str):
        return None
    url = urljoin('https://www.yyscha.cn/application/QQ/', value.strip())
    p = urlparse(url)
    try:
        allowed = p.scheme == 'https' and p.hostname == 'www.yyscha.cn' and p.port in (None, 443) and not p.username and not p.password
    except ValueError:
        return None
    if not allowed or p.path != '/application/QQ/serve.php' or p.fragment:
        return None
    query = parse_qs(p.query)
    if not query.get('record') or not query.get('path'):
        return None
    return url


def extract_images(item):
    from relay import DOM, Node
    root = DOM(str(item.get('html') or '')).root
    result, seen = [], set()
    def visit(node, quoted=False):
        if node.tag in ('script', 'style', 'iframe'):
            return
        quoted = quoted or node.has('reply-content')
        if node.tag == 'img':
            if node.has('face-emoji') or node.has('face-emoji-image'):
                return
            raw = node.attrs.get('data-original') or node.attrs.get('data-src') or node.attrs.get('src')
            url = image_url(raw)
            if url and url not in seen:
                seen.add(url)
                result.append({'url': url, 'context': 'quote' if quoted else 'body'})
        for child in node.children:
            if isinstance(child, Node):
                visit(child, quoted)
    visit(root)
    return result


def stable_ref(url):
    p = urlparse(url)
    query = parse_qs(p.query)
    return p.path + '?' + urlencode({'record': query.get('record', [''])[0], 'path': query.get('path', [''])[0]})


def image_mime(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'image/webp'
    if data.startswith(b'BM'):
        return 'image/bmp'
    return None


class ImageClient:
    def __init__(self, config, db):
        self.config, self.db = config, db
        self.token, self.expires = None, 0
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS image_uploads(cache_key TEXT PRIMARY KEY,image_key TEXT,uploaded_at REAL)')

    def access_token(self):
        import relay as r
        r.ensure_time(self.config)
        if self.token and time.monotonic() < self.expires:
            return self.token
        if not self.config.get('feishu_app_id') or not self.config.get('feishu_app_secret'):
            raise r.Failure('图片上传缺少飞书应用App ID/Secret，请填写配图凭据')
        result = r.request_json('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', payload={
            'app_id': self.config['feishu_app_id'], 'app_secret': self.config['feishu_app_secret']})
        if type(result.get('code')) is not int or result['code'] != 0 or not result.get('tenant_access_token'):
            raise r.Failure('飞书图片应用认证失败，请检查应用发布和凭据')
        self.token = result['tenant_access_token']
        self.expires = time.monotonic() + max(1, int(result.get('expire', 7200)) - 120)
        return self.token

    def download(self, url):
        import relay as r
        checked = image_url(url)
        if not checked:
            raise r.Failure('图片地址不属于已验证的yyscha资源接口')
        r.ensure_time(self.config)
        started = time.monotonic()
        req = Request(checked, headers={'Referer': 'https://www.yyscha.cn/application/QQ/'})
        try:
            from site_session import get_session
            with get_session(self.config).open(req, timeout=20) as response:
                mime = response.headers.get('Content-Type', '').split(';')[0].lower()
                if not mime.startswith('image/'):
                    raise r.Failure('图片接口未返回图片，登录或图片签名可能失效')
                if int(response.headers.get('Content-Length', '0')) > LIMIT:
                    raise r.Failure('图片超过9MiB上传限制')
                chunks, size = [], 0
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > LIMIT or time.monotonic() - started > 60:
                        raise r.Failure('图片下载超过大小或时间限制')
                    chunks.append(chunk)
            data = b''.join(chunks)
            actual_mime = image_mime(data)
            if not actual_mime:
                raise r.Failure('图片内容不是支持的PNG/JPEG/GIF/WebP/BMP格式')
            return data, actual_mime
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            raise r.Failure('图片读取失败，等待刷新签名并重试') from None

    def upload(self, url):
        import relay as r
        if not image_url(url):
            raise r.Failure('图片地址不属于已验证的yyscha资源接口')
        cache = hashlib.sha256((str(self.config.get('feishu_app_id')) + '\n' + stable_ref(url)).encode()).hexdigest()
        row = self.db.execute('SELECT image_key,uploaded_at FROM image_uploads WHERE cache_key=?', (cache,)).fetchone()
        if row and 0 <= time.time() - row[1] < 7 * 86400:
            return row[0]
        self.access_token()  # 缺少凭据时不先下载聊天图片。
        data, mime = self.download(url)
        boundary = 'yyscha-' + uuid.uuid4().hex
        payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n'
                   f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="chat-image"\r\nContent-Type: {mime}\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        for attempt in range(2):
            r.ensure_time(self.config)
            req = Request('https://open.feishu.cn/open-apis/im/v1/images', data=payload, headers={
                'Authorization': 'Bearer ' + self.access_token(), 'Content-Type': 'multipart/form-data; boundary=' + boundary})
            try:
                with build_opener(ProxyHandler({}), r.NoRedirect).open(req, timeout=40) as response:
                    raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise r.Failure('飞书上传响应过大')
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError
            except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                raise r.Failure('飞书图片上传请求失败，保留消息等待重试') from None
            if result.get('code') in (99991663, 99991664, 99991668) and not attempt:
                self.token = None
                continue
            key = (result.get('data') or {}).get('image_key')
            if type(result.get('code')) is not int or result['code'] != 0 or not isinstance(key, str) or not key:
                raise r.Failure('飞书拒绝图片上传，请检查应用图片权限与发布状态')
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO image_uploads VALUES(?,?,?)', (cache, key, time.time()))
            return key
        raise r.Failure('飞书图片上传认证失败')

    def send(self, body, images, parts=None):
        import relay as r
        cap = max(1, min(20, int(self.config.get('max_chat_images', 6))))
        keyed_images = []
        for image in images[:cap]:
            key = self.upload(image['url'])
            keyed_images.append((key, image.get('context', 'body')))
        elements = card_elements(body, parts, keyed_images, self.config.get('feishu_keyword', ''), include_header=False)
        if len(images) > cap:
            elements.append({'tag': 'div', 'text': {'tag': 'plain_text', 'content': f'共有{len(images)}张图片，本条展示前{cap}张。'}})
        card = {'config': {'wide_screen_mode': True}, 'elements': elements}
        if parts and parts.get('header'):
            card['header'] = card_header(parts)
        payload = {'msg_type': 'interactive', 'card': card}
        r.ensure_time(self.config)
        if self.config.get('feishu_secret'):
            stamp = int(time.time())
            payload.update(timestamp=str(stamp), sign=r.feishu_signature(self.config['feishu_secret'], stamp))
        result = r.request_json(self.config['feishu_webhook'], payload=payload)
        if type(result.get('code')) is not int or result['code'] != 0:
            with self.db:
                self.db.execute('DELETE FROM image_uploads')
            raise r.Failure('飞书图文卡片发送失败，保留消息等待重试')


def refresh_if_expired(config, day, uid, mid, images):
    import relay as r
    stale = False
    for image in images:
        try:
            exp = parse_qs(urlparse(image['url']).query).get('exp')
            stale = stale or bool(exp and float(exp[0]) <= time.time() + 60)
        except (ValueError, KeyError):
            stale = True
    if not stale:
        return images
    from history_test import fetch_day
    fresh = next((item for item in fetch_day(r.Source(config), day, uid) if item['id'] == mid), None)
    if fresh is None:
        raise r.Failure('待发送图片对应消息暂时找不到，保留队列等待重试')
    result = extract_images(fresh)
    if not result:
        raise r.Failure('待发送消息的图片签名暂未刷新，保留队列等待重试')
    return result

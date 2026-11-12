"""Authorized yyscha reader and durable Feishu relay. Python 3.11+, stdlib only."""
import argparse
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from feishu_transport import feishu_signature

SITE = 'https://www.yyscha.cn'
TZ = timezone(timedelta(hours=8))
PRIVATE = Path(os.environ.get('YYSCHA_PRIVATE_DIR', str(Path.home() / '.yyscha-qq-feishu') if os.name == 'nt' else '/overlay/yyscha/data'))
UIDS = ['u_example_sender_1', 'u_example_sender_2', 'u_example_sender_3']


class Failure(Exception):
    """Only fixed, credential-free diagnostic messages belong here."""


class AuthRequired(Failure):
    pass


class RateLimited(Failure):
    def __init__(self, retry_after=60):
        self.retry_after = retry_after
        super().__init__('网站要求延迟重试；暂停源站请求，保留队列')


def retry_delay(header, now=None):
    from email.utils import parsedate_to_datetime
    try:
        value = float(header)
        if value >= 0 and value < float('inf'):
            return max(60, value)
    except (ValueError, TypeError):
        pass
    try:
        return max(60, parsedate_to_datetime(header).timestamp() - (time.time() if now is None else now))
    except (ValueError, TypeError, OverflowError, AttributeError):
        return 60


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        target = args[-1] if args else kwargs.get('newurl')
        if isinstance(target, str):
            p = urlparse(target)
            if p.hostname in ('www.yyscha.cn', 'yyscha.cn') and ('/ailogin/' in p.path or 'login' in p.path.lower()):
                raise AuthRequired('网站跳转登录页面，请打开登录恢复入口')
        raise Failure('HTTP重定向：请检查登录状态或地址')


def request_json(url, headers=None, payload=None, session=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = dict(headers or {})
    if data is not None:
        headers['Content-Type'] = 'application/json; charset=utf-8'
    try:
        # 独立采集程序直接连接服务端，不继承启动环境注入的HTTP代理。
        opener = session if session is not None else build_opener(ProxyHandler({}), NoRedirect)
        with opener.open(Request(url, data=data, headers=headers), timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise Failure('响应过大')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise Failure('响应不是JSON对象')
        return result
    except HTTPError as exc:
        if session is not None and exc.code == 429:
            raise RateLimited(retry_delay(exc.headers.get('Retry-After'))) from None
        if session is not None and exc.code in (401, 403):
            raise AuthRequired('网站会话需要重新验证，请打开登录恢复入口') from None
        raise Failure(f'HTTP {exc.code}：检查网络、登录或机器人配置') from None
    except (URLError, TimeoutError, OSError):
        raise Failure('网络失败或超时，稍后重试') from None
    except (ValueError, UnicodeError):
        if session is not None and 'raw' in locals() and any(marker in raw.lower() for marker in (b'loginmodal', b'loginform', b'/ailogin/')):
            raise AuthRequired('网站返回登录页面，请打开登录恢复入口') from None
        raise Failure('响应不是有效JSON，可能登录已过期') from None


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def has(self, name):
        return name in self.attrs.get('class', '').split()

    def find(self, predicate):
        if predicate(self):
            return self
        for child in self.children:
            if isinstance(child, Node):
                found = child.find(predicate)
                if found is not None:
                    return found
        return None

    def plain(self, omit_reply=False):
        if self.tag in ('script', 'style', 'iframe') or (omit_reply and self.has('reply-content')):
            return ''
        if self.tag == 'img':
            if self.has('face-emoji') or self.has('face-emoji-image'):
                return self.attrs.get('alt') or '[QQ表情]'
            if self.has('avatar') or urlparse(self.attrs.get('src', '')).hostname == 'q.qlogo.cn':
                return ''
            return '[图片' + (('：' + self.attrs['alt']) if self.attrs.get('alt') else '') + ']'
        if self.tag in ('video', 'audio'):
            return '[媒体]'
        if self.tag == 'br':
            return '\n'
        if self.tag == 'a' and (self.has('message-file') or self.has('file-bubble')):
            name = self.find(lambda n: n.has('fname') or n.has('file-name'))
            if name:
                return '[文件：' + name.plain().strip() + ']\n'
        value = ''.join(c.plain(omit_reply) if isinstance(c, Node) else c for c in self.children)
        if self.tag == 'a' and not value.strip():
            value = '[附件或链接]'
        return value + ('\n' if self.tag in ('div', 'p', 'li') else '')


class DOM(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in ('img', 'br', 'hr', 'input', 'meta', 'link', 'source', 'wbr', 'area', 'embed', 'param', 'track', 'col', 'base'):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def message_parts(item, day):
    from file_feishu import extract_files
    files = extract_files(item)
    root = DOM(str(item.get('html') or '')).root
    content = root.find(lambda n: n.has('content')) or root
    reply = root.find(lambda n: n.has('reply-content'))
    body = content.plain(omit_reply=True).strip()
    if not item.get('html'):
        body = str(item.get('text') or '[无正文或未支持的媒体]')
    if not body:
        body = '[无文字正文或未支持的媒体]'
    if item.get('type') not in (None, '', 'text') and '[图片' not in body and '[媒体]' not in body and not files:
        body += '\n[非纯文字消息；媒体或附件请打开原页面查看]'
    try:
        stamp = datetime.fromtimestamp(float(item['ts']) / 1000, TZ).strftime('%m-%d %H:%M:%S')
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        stamp = day
    quote_data = None
    if reply:
        header = reply.find(lambda n: n.has('reply-content-header'))
        author = header.find(lambda n: n.tag == 'strong') if header else None
        when = reply.find(lambda n: n.has('reply-content-time'))
        quote = reply.find(lambda n: n.has('reply-content-text'))
        quote_data = {'author': author.plain().strip() if author else '未知',
                      'time': when.plain().strip() if when else '',
                      'text': quote.plain().strip() if quote else '[引用展示不可用]'}
    return {'header': f"{item.get('name', item['uid'])} · {stamp}", 'quote': quote_data,
            'body': body, 'link': f'{SITE}/application/QQ/day.php?' + urlencode({'day': day}),
            'format_version': 2, 'sender_uid': item['uid'],
            'sender_name': item.get('name', item['uid']), 'message_time': stamp, 'files': files}


def render(item, day):
    message = message_parts(item, day)
    parts = [message['header']]
    quote = message['quote']
    if quote:
        parts += [f"引用 {quote['author']} · {quote['time'] or '时间未知'}：",
                  '「' + quote['text'] + '」',
                  '（引用为网页展示，可能截断；完整上下文见原页面）', '', '回复：' + message['body']]
    else:
        parts.append(message['body'])
    return '\n'.join(parts)


def delivery_body(body, day, parts):
    # Older queued rows contain an automatically appended source URL. Remove
    # only that one trailing footer; preserve URLs authored in the actual chat.
    footer = '\n' + SITE + '/application/QQ/day.php?' + urlencode({'day': day})
    if parts.get('format_version', 0) < 2 and body.endswith(footer):
        return body[:-len(footer)]
    return body


class Source:
    def __init__(self, config):
        self.config = config

    def page(self, day, uid, offset, limit=60):
        url = SITE + '/application/QQ/api.php?' + urlencode(dict(action='day_messages', day=day, uid=uid, offset=offset, limit=limit))
        from site_session import get_session
        data = request_json(url, {'Cookie': self.config['site_cookie']}, session=get_session(self.config))
        if data.get('ok') is not True or not isinstance(data.get('items'), list):
            hint = str(data.get('msg') or data.get('message') or '').lower()
            if data.get('code') in (401, 403) or any(word in hint for word in ('登录', 'login', 'session', '未授权')):
                raise AuthRequired('网站登录已失效，请打开登录恢复入口；进度和队列已保留')
            raise Failure('消息业务失败：检查Cookie或登录状态')
        if not isinstance(data.get('day'), dict) or data['day'].get('key') != day or not data['day'].get('chat_key'):
            raise Failure('日期或聊天范围缺失/不符，停止推进游标')
        for key in ('offset', 'total'):
            if type(data.get(key)) is not int or data[key] < 0:
                raise Failure('分页字段无效')
        if data['offset'] != offset:
            raise Failure('普通分页offset不符，停止推进游标')
        for item in data['items']:
            if not isinstance(item, dict) or item.get('uid') != uid or not isinstance(item.get('id'), str) or not item['id']:
                raise Failure('消息ID缺失或UID筛选不符')
        return data


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS streams(day TEXT,uid TEXT,scope TEXT,total INTEGER,scanned REAL,PRIMARY KEY(day,uid));
    CREATE TABLE IF NOT EXISTS messages(scope TEXT,id TEXT,day TEXT,uid TEXT,ts REAL,body TEXT,status TEXT,attempts INTEGER DEFAULT 0,next_try REAL DEFAULT 0,error TEXT,PRIMARY KEY(scope,id));
    ''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(messages)')}
    if 'media_json' not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN media_json TEXT NOT NULL DEFAULT '[]'")
        db.commit()
    if 'parts_json' not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN parts_json TEXT NOT NULL DEFAULT '{}'")
        db.commit()
    return db


def scan(db, source, day, uid, baseline=False, now=None):
    now = time.time() if now is None else now
    stream = db.execute('SELECT scope,total,scanned FROM streams WHERE day=? AND uid=?', (day, uid)).fetchone()
    head = source.page(day, uid, 0)
    scope = SITE + '/' + str(head['day']['chat_key'])
    if stream and stream[0] != scope:
        raise Failure('聊天范围变化，停止读取')
    full = not stream or now - stream[2] >= 3600 or head['total'] < stream[1]
    start = 0 if full else max(0, min(stream[1], head['total']) - 60)
    offset, items, pages = start, [], 0
    target = head['total']  # 固定本轮快照边界；轮询期间新增留到下一轮，避免追赶无限流。
    while offset < target:
        page = head if offset == 0 else source.page(day, uid, offset)
        if SITE + '/' + str(page['day']['chat_key']) != scope or page['total'] < target:
            raise Failure('扫描中聊天或总数变化，请重试')
        batch = page['items']
        if not batch:
            raise Failure('分页提前为空，保留原游标等待重试')
        items.extend(batch[:target - offset])
        offset += len(batch)
        pages += 1
        if pages > 10000:
            raise Failure('分页过多，保留原游标')
    # 只有所有页成功后，基线/队列/游标一起提交。
    from media_feishu import extract_images
    with db:
        for item in items:
            media = json.dumps([] if baseline else extract_images(item), ensure_ascii=False)
            structured = json.dumps({} if baseline else message_parts(item, day), ensure_ascii=False)
            db.execute('INSERT OR IGNORE INTO messages(scope,id,day,uid,ts,body,status,media_json,parts_json) VALUES(?,?,?,?,?,?,?,?,?)',
                       (scope, item['id'], day, uid, float(item.get('ts') or 0), '' if baseline else render(item, day), 'baseline' if baseline else 'pending', media, structured))
            if not baseline:
                db.execute("UPDATE messages SET media_json=?,parts_json=? WHERE scope=? AND id=? AND status IN ('pending','retry')", (media, structured, scope, item['id']))
        db.execute('INSERT OR REPLACE INTO streams VALUES(?,?,?,?,?)', (day, uid, scope, target, now if full else stream[2]))
    return len(items)


def ensure_time(config):
    if config.get('require_trusted_time', False):
        from timeguard import require_trusted_time, TimeTrustError
        try:
            require_trusted_time()
        except (TimeTrustError, AttributeError):
            raise Failure('等待H616现有NTP可信时间记录，暂缓读取和发送') from None


def poll(db, source, config, now=None):
    ensure_time(config)
    now = now or datetime.now(TZ)
    day = now.date()
    # 配置变化应明确重新建立基线，不能默默补发新UID的历史。
    fingerprint = json.dumps(sorted(config['uids']))
    saved_uids = db.execute("SELECT value FROM meta WHERE key='uids'").fetchone()
    if saved_uids and saved_uids[0] != fingerprint:
        raise Failure('监听名单与数据库不符，请为新名单使用独立数据库建立基线')
    with db:
        db.execute("INSERT OR IGNORE INTO meta VALUES('uids',?)", (fingerprint,))
    initialized = db.execute("SELECT value FROM meta WHERE key='initialized'").fetchone()
    if not initialized:
        # 部分初始化失败重启时，仍按基线处理，绝不把旧消息变为待发送。
        for d in (day - timedelta(days=1), day):
            for uid in config['uids']:
                scan(db, source, d.isoformat(), uid, baseline=True)
        with db:
            db.execute("INSERT INTO meta VALUES('initialized','1')")
            db.execute("INSERT OR REPLACE INTO meta VALUES('last_day',?)", (day.isoformat(),))
        return
    last = db.execute("SELECT value FROM meta WHERE key='last_day'").fetchone()
    first = min(datetime.fromisoformat(last[0]).date(), day) if last else day
    if (now.hour * 60 + now.minute) < config.get('previous_day_minutes', 10):
        first = min(first, day - timedelta(days=1))
    while first <= day:
        for uid in config['uids']:
            scan(db, source, first.isoformat(), uid)
        first += timedelta(days=1)
    with db:
        db.execute("INSERT OR REPLACE INTO meta VALUES('last_day',?)", (day.isoformat(),))


def feishu(config, body):
    ensure_time(config)
    text = (config.get('feishu_keyword', '') + '\n' + body).strip()
    if len(text.encode()) > 16000:
        text = text.encode()[:14000].decode('utf-8', errors='ignore') + '\n[内容过长，已截断]'
    payload = {'msg_type': 'text', 'content': {'text': text}}
    if config.get('feishu_secret'):
        stamp = str(int(time.time()))
        payload.update(timestamp=stamp, sign=feishu_signature(config['feishu_secret'], int(stamp)))
    result = request_json(config['feishu_webhook'], payload=payload)
    if type(result.get('code')) is not int or result['code'] != 0:
        raise Failure('飞书业务失败：检查机器人安全设置、关键词与签名')


def drain(db, config, sender=feishu, now=None, image_client=None):
    now = time.time() if now is None else now
    rows = db.execute("SELECT scope,id,body,attempts,media_json,day,uid,parts_json FROM messages WHERE status IN ('pending','retry') AND next_try<=? ORDER BY ts,id LIMIT 10", (now,)).fetchall()
    for scope, mid, body, attempts, media_json, day, uid, parts_json in rows:
        try:
            media = json.loads(media_json)
            parts = json.loads(parts_json)
            body = delivery_body(body, day, parts)
            files = parts.get('files') or []
            file_client = None
            if files:
                from file_feishu import FileClient, refresh_files
                from media_feishu import ImageClient
                if image_client is None:
                    image_client = ImageClient(config, db)
                file_client = FileClient(config, db, image_client)
                file_client.require_destination()
                files = refresh_files(config, day, uid, mid, files)
                parts['files'] = files
                with db:
                    db.execute('UPDATE messages SET parts_json=? WHERE scope=? AND id=?', (json.dumps(parts, ensure_ascii=False), scope, mid))
                card_key = file_client.progress_key(scope, mid, 'card')
            if media or parts.get('quote') or (parts.get('header') and (image_client is not None or sender is feishu)):
                from media_feishu import ImageClient, refresh_if_expired
                if image_client is None:
                    image_client = ImageClient(config, db)
                if not file_client or not file_client.done(card_key):
                    fresh = refresh_if_expired(config, day, uid, mid, media) if media else []
                    if fresh != media:
                        with db:
                            db.execute('UPDATE messages SET media_json=? WHERE scope=? AND id=?', (json.dumps(fresh), scope, mid))
                    image_client.send(body, fresh, parts=parts)
                    if file_client:
                        file_client.mark(card_key)
            else:
                sender(config, body)
            if file_client:
                for attachment in files:
                    file_client.send(scope, mid, attachment)
        except Failure as exc:
            with db:
                db.execute("UPDATE messages SET status='retry',attempts=?,next_try=?,error=? WHERE scope=? AND id=?", (attempts + 1, now + min(3600, 15 * 2 ** min(attempts, 8)), str(exc), scope, mid))
                if isinstance(exc, RateLimited):
                    db.execute("INSERT OR REPLACE INTO meta VALUES('site_backoff_until',?)", (str(now + exc.retry_after),))
            logging.error('%s；消息已保留等待重试', exc)
            if isinstance(exc, RateLimited):
                break
        else:
            with db:
                db.execute("UPDATE messages SET status='sent',error=NULL WHERE scope=? AND id=?", (scope, mid))
        time.sleep(1)  # 避免突发队列连续请求机器人。


def load_config(path, allow_missing_cookie=False):
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise Failure('私密配置未创建或无效，请先运行设置工具') from None
    if not isinstance(config, dict):
        raise Failure('配置格式无效')
    cookie = config.get('site_cookie', '')
    if not isinstance(cookie, str) or ('=' not in cookie and not (allow_missing_cookie and not cookie)) or '\n' in cookie or '\r' in cookie:
        raise Failure('请填写有效Cookie')
    uids = config.get('uids')
    if not isinstance(uids, list) or not uids or any(not isinstance(x, str) or not x.startswith('u_') for x in uids) or len(set(uids)) != len(uids):
        raise Failure('监听UID列表无效或重复')
    webhook = config.get('feishu_webhook', '')
    p = urlparse(webhook)
    if webhook and (p.scheme != 'https' or p.netloc != 'open.feishu.cn' or not p.path.startswith('/open-apis/bot/v2/hook/') or p.query or p.fragment):
        raise Failure('飞书Webhook地址无效')
    if not isinstance(config.get('interval_seconds', 15), (int, float)) or config.get('interval_seconds', 15) < 15:
        raise Failure('轮询间隔至少15秒')
    if not isinstance(config.get('notification_polling', False), bool):
        raise Failure('notification_polling必须是布尔值')
    config['_config_path'] = str(Path(path).resolve())
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'preview', 'once', 'run', 'status', 'session-status', 'test-send'])
    parser.add_argument('--config', type=Path, default=PRIVATE / 'config.json')
    parser.add_argument('--db', type=Path, default=PRIVATE / 'state.sqlite3')
    parser.add_argument('--day', default=datetime.now(TZ).date().isoformat())
    parser.add_argument('--send', action='store_true', help='明确启用飞书发送；否则只入队')
    args = parser.parse_args()
    if args.send and os.name == 'nt' and (args.config.parent / 'sender-disabled').exists():
        raise Failure('正式发送已转交开发板，本机发送入口已停用；请使用登录与加密同步入口')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.command == 'status':
        db = connect(args.db)
        print(dict(db.execute('SELECT status,count(*) FROM messages GROUP BY status')))
        print('等待重试：', db.execute("SELECT count(*) FROM messages WHERE status='retry'").fetchone()[0])
        notification_status = db.execute("SELECT value FROM meta WHERE key='notification_poll_status'").fetchone()
        if notification_status:
            status = json.loads(notification_status[0])
            print('检测模式：', status.get('mode'), '最近结果：', status.get('outcome'), '下次间隔秒数：', status.get('next_delay_seconds'))
        return
    config = load_config(args.config)
    if args.command == 'session-status':
        from site_session import get_session
        status = get_session(config).status()
        print('已启用持久会话：', status['persistent'], 'Cookie数量：', status['cookie_count'])
        print('含记住登录凭据：', status['remember_present'])
        print('服务器已知到期时间（北京时间）：', [datetime.fromtimestamp(t, TZ).isoformat(timespec='seconds') for t in status['expiry_timestamps']])
        print('未提供有效期的导入Cookie，其客户端到期时间未知；服务器仍可能提前撤销会话。')
        return
    source = Source(config)
    if args.command == 'test-send':
        if not config.get('feishu_webhook'):
            raise Failure('测试发送前请配置飞书Webhook')
        feishu(config, 'yyscha 网页转发连接测试\n引用 示例用户：\n「这是一条模拟引用」\n\n回复：这是模拟回复，非真实聊天消息。\n[图片：媒体提示示例]\n' + SITE + '/application/QQ/')
        print('飞书业务code=0，请在接收群核对测试文字、引用与媒体提示。')
        return
    if args.command in ('check', 'preview'):
        ensure_time(config)
        for uid in config['uids']:
            data = source.page(args.day, uid, 0)
            print(f"{uid}: 业务成功，total={data['total']}，本页UID核验通过")
            if not data['items']:
                print('本页为空，尚无样本可验证该UID；可用 --day 指定有消息的日期')
            if args.command == 'preview':
                for item in data['items'][-3:]:
                    print(render(item, args.day), '\n')
        return
    if args.send and not config.get('feishu_webhook'):
        raise Failure('发送前请配置飞书Webhook')
    db = connect(args.db)
    from media_feishu import ImageClient
    image_client = ImageClient(config, db) if args.send else None
    from notification_polling import NotificationPolling
    notification = NotificationPolling() if config.get('notification_polling', False) and args.command == 'run' else None
    # 整个生命周期占用独占锁，避免两个进程同时发送同一队列。
    lock = sqlite3.connect(str(args.db) + '.lock', timeout=0)
    lock.execute('CREATE TABLE IF NOT EXISTS guard(id INTEGER)')
    try:
        lock.execute('BEGIN EXCLUSIVE')
    except sqlite3.OperationalError:
        raise Failure('同一数据库的监听程序已经运行') from None
    try:
        while True:
            started = time.monotonic()
            failed = False
            try:
                refreshed = load_config(args.config)
                changed_app = (refreshed.get('feishu_app_id'), refreshed.get('feishu_app_secret')) != (config.get('feishu_app_id'), config.get('feishu_app_secret'))
                config.update(refreshed)
                if changed_app and args.send:
                    image_client = ImageClient(config, db)
                if notification:
                    checked = notification.tick(db, source, config)
                else:
                    poll(db, source, config)
                    checked = True
                if not checked:
                    if args.send and notification.allow_delivery:
                        drain(db, config, image_client=image_client)
                    time.sleep(max(0, min(15, config.get('interval_seconds', 15)) - (time.monotonic() - started)))
                    continue
                prior_auth = db.execute("SELECT value FROM meta WHERE key='auth_required'").fetchone()
                with db:
                    db.execute("INSERT OR REPLACE INTO meta VALUES('auth_required','0')")
                    if prior_auth and prior_auth[0] == '1':
                        db.execute("INSERT OR REPLACE INTO meta VALUES('auth_recovery_pending','1')")
                recovered = db.execute("SELECT value FROM meta WHERE key='auth_recovery_pending'").fetchone()
                if args.send and recovered and recovered[0] == '1':
                    try:
                        feishu(config, '网页监听登录已恢复\n消息读取已通过验证，继续监听；原有基线和待发送队列保留。')
                    except Failure:
                        logging.error('登录恢复通知未完成，下轮重试')
                    else:
                        with db:
                            db.execute("INSERT OR REPLACE INTO meta VALUES('auth_recovery_pending','0')")
                logging.info('轮询完成；队列状态 %s', dict(db.execute('SELECT status,count(*) FROM messages GROUP BY status')))
            except AuthRequired as exc:
                failed = True
                logging.error('%s', exc)
                with db:
                    db.execute("INSERT OR REPLACE INTO meta VALUES('auth_required','1')")
                # Throttle alerts persistently, and use the existing notification
                # channel without sending passwords, cookie values or challenges.
                last = db.execute("SELECT value FROM meta WHERE key='auth_alert_at'").fetchone()
                if args.send and (not last or time.time() - float(last[0]) >= 6 * 3600):
                    try:
                        feishu(config, '网页监听登录失效\n采集已等待恢复，基线和队列保留。\n请打开本机或开发板的登录恢复入口，重新登录或导入Cookie；程序会自动重新读取配置。')
                    except Failure:
                        logging.error('登录失效提醒发送失败，下轮重试')
                    else:
                        with db:
                            db.execute("INSERT OR REPLACE INTO meta VALUES('auth_alert_at',?)", (str(time.time()),))
            except Failure as exc:
                failed = True
                logging.error('%s；保留游标，下轮重试', exc)
            if args.send and (notification is None or notification.allow_delivery):
                drain(db, config, image_client=image_client)
            if args.command == 'once':
                if failed:
                    raise Failure('本轮读取失败，未完成全部扫描')
                break
            time.sleep(max(0, config.get('interval_seconds', 15) - (time.monotonic() - started)))
    finally:
        lock.close()
        db.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('已停止，队列和基线已保留')
    except Failure as exc:
        logging.error('%s', exc)
        raise SystemExit(1)
    except Exception:
        logging.error('本地配置、解析或数据库异常；已停止。请检查配置与磁盘，不自动输出原始响应或凭据。')
        raise SystemExit(1)

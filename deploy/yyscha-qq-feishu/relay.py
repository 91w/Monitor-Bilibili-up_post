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
UIDS = ['u_example_1', 'u_example_2', 'u_example_3']


class Failure(Exception):
    """Only fixed, credential-free diagnostic messages belong here."""


class AuthRequired(Failure):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        target = args[-1] if args else kwargs.get('newurl')
        if isinstance(target, str):
            p = urlparse(target)
            if p.hostname in ('www.yyscha.cn', 'yyscha.cn') and ('/ailogin/' in p.path or 'login' in p.path.lower()):
                raise AuthRequired('��վ��ת��¼ҳ�棬��򿪵�¼�ָ����')
        raise Failure('HTTP�ض��������¼״̬���ַ')


def request_json(url, headers=None, payload=None, session=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = dict(headers or {})
    if data is not None:
        headers['Content-Type'] = 'application/json; charset=utf-8'
    try:
        # �����ɼ�����ֱ�����ӷ���ˣ����̳���������ע���HTTP������
        opener = session if session is not None else build_opener(ProxyHandler({}), NoRedirect)
        with opener.open(Request(url, data=data, headers=headers), timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise Failure('��Ӧ����')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise Failure('��Ӧ����JSON����')
        return result
    except HTTPError as exc:
        if session is not None and exc.code in (401, 403):
            raise AuthRequired('��վ�Ự��Ҫ������֤����򿪵�¼�ָ����') from None
        raise Failure(f'HTTP {exc.code}��������硢��¼�����������') from None
    except (URLError, TimeoutError, OSError):
        raise Failure('����ʧ�ܻ�ʱ���Ժ�����') from None
    except (ValueError, UnicodeError):
        if session is not None and 'raw' in locals() and any(marker in raw.lower() for marker in (b'loginmodal', b'loginform', b'/ailogin/')):
            raise AuthRequired('��վ���ص�¼ҳ�棬��򿪵�¼�ָ����') from None
        raise Failure('��Ӧ������ЧJSON�����ܵ�¼�ѹ���') from None


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
                return self.attrs.get('alt') or '[QQ����]'
            if self.has('avatar') or urlparse(self.attrs.get('src', '')).hostname == 'q.qlogo.cn':
                return ''
            return '[ͼƬ' + (('��' + self.attrs['alt']) if self.attrs.get('alt') else '') + ']'
        if self.tag in ('video', 'audio'):
            return '[ý��]'
        if self.tag == 'br':
            return '\n'
        value = ''.join(c.plain(omit_reply) if isinstance(c, Node) else c for c in self.children)
        if self.tag == 'a' and not value.strip():
            value = '[����������]'
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
    root = DOM(str(item.get('html') or '')).root
    content = root.find(lambda n: n.has('content')) or root
    reply = root.find(lambda n: n.has('reply-content'))
    body = content.plain(omit_reply=True).strip()
    if not item.get('html'):
        body = str(item.get('text') or '[�����Ļ�δ֧�ֵ�ý��]')
    if not body:
        body = '[���������Ļ�δ֧�ֵ�ý��]'
    if item.get('type') not in (None, '', 'text') and '[ͼƬ' not in body and '[ý��]' not in body:
        body += '\n[�Ǵ�������Ϣ��ý��򸽼����ԭҳ��鿴]'
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
        quote_data = {'author': author.plain().strip() if author else 'δ֪',
                      'time': when.plain().strip() if when else '',
                      'text': quote.plain().strip() if quote else '[����չʾ������]'}
    return {'header': f"{item.get('name', item['uid'])} �� {stamp}", 'quote': quote_data,
            'body': body, 'link': f'{SITE}/application/QQ/day.php?' + urlencode({'day': day})}


def render(item, day):
    message = message_parts(item, day)
    parts = [message['header']]
    quote = message['quote']
    if quote:
        parts += [f"���� {quote['author']} �� {quote['time'] or 'ʱ��δ֪'}��",
                  '��' + quote['text'] + '��',
                  '������Ϊ��ҳչʾ�����ܽضϣ����������ļ�ԭҳ�棩', '', '�ظ���' + message['body']]
    else:
        parts.append(message['body'])
    parts.append(message['link'])
    return '\n'.join(parts)


class Source:
    def __init__(self, config):
        self.config = config

    def page(self, day, uid, offset, limit=60):
        url = SITE + '/application/QQ/api.php?' + urlencode(dict(action='day_messages', day=day, uid=uid, offset=offset, limit=limit))
        from site_session import get_session
        data = request_json(url, {'Cookie': self.config['site_cookie']}, session=get_session(self.config))
        if data.get('ok') is not True or not isinstance(data.get('items'), list):
            hint = str(data.get('msg') or data.get('message') or '').lower()
            if data.get('code') in (401, 403) or any(word in hint for word in ('��¼', 'login', 'session', 'δ��Ȩ')):
                raise AuthRequired('��վ��¼��ʧЧ����򿪵�¼�ָ���ڣ����ȺͶ����ѱ���')
            raise Failure('��Ϣҵ��ʧ�ܣ����Cookie���¼״̬')
        if not isinstance(data.get('day'), dict) or data['day'].get('key') != day or not data['day'].get('chat_key'):
            raise Failure('���ڻ����췶Χȱʧ/������ֹͣ�ƽ��α�')
        for key in ('offset', 'total'):
            if type(data.get(key)) is not int or data[key] < 0:
                raise Failure('��ҳ�ֶ���Ч')
        if data['offset'] != offset:
            raise Failure('��ͨ��ҳoffset������ֹͣ�ƽ��α�')
        for item in data['items']:
            if not isinstance(item, dict) or item.get('uid') != uid or not isinstance(item.get('id'), str) or not item['id']:
                raise Failure('��ϢIDȱʧ��UIDɸѡ����')
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
        raise Failure('���췶Χ�仯��ֹͣ��ȡ')
    full = not stream or now - stream[2] >= 3600 or head['total'] < stream[1]
    start = 0 if full else max(0, min(stream[1], head['total']) - 60)
    offset, items, pages = start, [], 0
    target = head['total']  # �̶����ֿ��ձ߽磻��ѯ�ڼ�����������һ�֣�����׷����������
    while offset < target:
        page = head if offset == 0 else source.page(day, uid, offset)
        if SITE + '/' + str(page['day']['chat_key']) != scope or page['total'] < target:
            raise Failure('ɨ��������������仯��������')
        batch = page['items']
        if not batch:
            raise Failure('��ҳ��ǰΪ�գ�����ԭ�α�ȴ�����')
        items.extend(batch[:target - offset])
        offset += len(batch)
        pages += 1
        if pages > 10000:
            raise Failure('��ҳ���࣬����ԭ�α�')
    # ֻ������ҳ�ɹ��󣬻���/����/�α�һ���ύ��
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
            raise Failure('�ȴ�H616����NTP����ʱ���¼���ݻ���ȡ�ͷ���') from None


def poll(db, source, config, now=None):
    ensure_time(config)
    now = now or datetime.now(TZ)
    day = now.date()
    # ���ñ仯Ӧ��ȷ���½������ߣ�����ĬĬ������UID����ʷ��
    fingerprint = json.dumps(sorted(config['uids']))
    saved_uids = db.execute("SELECT value FROM meta WHERE key='uids'").fetchone()
    if saved_uids and saved_uids[0] != fingerprint:
        raise Failure('�������������ݿⲻ������Ϊ������ʹ�ö������ݿ⽨������')
    with db:
        db.execute("INSERT OR IGNORE INTO meta VALUES('uids',?)", (fingerprint,))
    initialized = db.execute("SELECT value FROM meta WHERE key='initialized'").fetchone()
    if not initialized:
        # ���ֳ�ʼ��ʧ������ʱ���԰����ߴ����������Ѿ���Ϣ��Ϊ�����͡�
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
        link = body.splitlines()[-1]
        text = text.encode()[:14000].decode('utf-8', errors='ignore') + '\n[���ݹ������ѽض�]\n' + link
    payload = {'msg_type': 'text', 'content': {'text': text}}
    if config.get('feishu_secret'):
        stamp = str(int(time.time()))
        payload.update(timestamp=stamp, sign=feishu_signature(config['feishu_secret'], int(stamp)))
    result = request_json(config['feishu_webhook'], payload=payload)
    if type(result.get('code')) is not int or result['code'] != 0:
        raise Failure('����ҵ��ʧ�ܣ��������˰�ȫ���á��ؼ�����ǩ��')


def drain(db, config, sender=feishu, now=None, image_client=None):
    now = time.time() if now is None else now
    rows = db.execute("SELECT scope,id,body,attempts,media_json,day,uid,parts_json FROM messages WHERE status IN ('pending','retry') AND next_try<=? ORDER BY ts,id LIMIT 10", (now,)).fetchall()
    for scope, mid, body, attempts, media_json, day, uid, parts_json in rows:
        try:
            media = json.loads(media_json)
            parts = json.loads(parts_json)
            if media or parts.get('quote'):
                from media_feishu import ImageClient, refresh_if_expired
                if image_client is None:
                    image_client = ImageClient(config, db)
                fresh = refresh_if_expired(config, day, uid, mid, media) if media else []
                if fresh != media:
                    with db:
                        db.execute('UPDATE messages SET media_json=? WHERE scope=? AND id=?', (json.dumps(fresh), scope, mid))
                image_client.send(body, fresh, parts=parts)
            else:
                sender(config, body)
        except Failure as exc:
            with db:
                db.execute("UPDATE messages SET status='retry',attempts=?,next_try=?,error=? WHERE scope=? AND id=?", (attempts + 1, now + min(3600, 15 * 2 ** min(attempts, 8)), str(exc), scope, mid))
            logging.error('%s����Ϣ�ѱ����ȴ�����', exc)
        else:
            with db:
                db.execute("UPDATE messages SET status='sent',error=NULL WHERE scope=? AND id=?", (scope, mid))
        time.sleep(1)  # ����ͻ������������������ˡ�


def load_config(path, allow_missing_cookie=False):
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise Failure('˽������δ��������Ч�������������ù���') from None
    if not isinstance(config, dict):
        raise Failure('���ø�ʽ��Ч')
    cookie = config.get('site_cookie', '')
    if not isinstance(cookie, str) or ('=' not in cookie and not (allow_missing_cookie and not cookie)) or '\n' in cookie or '\r' in cookie:
        raise Failure('����д��ЧCookie')
    uids = config.get('uids')
    if not isinstance(uids, list) or not uids or any(not isinstance(x, str) or not x.startswith('u_') for x in uids) or len(set(uids)) != len(uids):
        raise Failure('����UID�б���Ч���ظ�')
    webhook = config.get('feishu_webhook', '')
    p = urlparse(webhook)
    if webhook and (p.scheme != 'https' or p.netloc != 'open.feishu.cn' or not p.path.startswith('/open-apis/bot/v2/hook/') or p.query or p.fragment):
        raise Failure('����Webhook��ַ��Ч')
    if not isinstance(config.get('interval_seconds', 15), (int, float)) or config.get('interval_seconds', 15) < 15:
        raise Failure('��ѯ�������15��')
    config['_config_path'] = str(Path(path).resolve())
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'preview', 'once', 'run', 'status', 'session-status', 'test-send'])
    parser.add_argument('--config', type=Path, default=PRIVATE / 'config.json')
    parser.add_argument('--db', type=Path, default=PRIVATE / 'state.sqlite3')
    parser.add_argument('--day', default=datetime.now(TZ).date().isoformat())
    parser.add_argument('--send', action='store_true', help='��ȷ���÷��鷢�ͣ�����ֻ���')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.command == 'status':
        db = connect(args.db)
        print(dict(db.execute('SELECT status,count(*) FROM messages GROUP BY status')))
        print('�ȴ����ԣ�', db.execute("SELECT count(*) FROM messages WHERE status='retry'").fetchone()[0])
        return
    config = load_config(args.config)
    if args.command == 'session-status':
        from site_session import get_session
        status = get_session(config).status()
        print('�����ó־ûỰ��', status['persistent'], 'Cookie������', status['cookie_count'])
        print('����ס��¼ƾ�ݣ�', status['remember_present'])
        print('��������֪����ʱ�䣨����ʱ�䣩��', [datetime.fromtimestamp(t, TZ).isoformat(timespec='seconds') for t in status['expiry_timestamps']])
        print('δ�ṩ��Ч�ڵĵ���Cookie����ͻ��˵���ʱ��δ֪���������Կ�����ǰ�����Ự��')
        return
    source = Source(config)
    if args.command == 'test-send':
        if not config.get('feishu_webhook'):
            raise Failure('���Է���ǰ�����÷���Webhook')
        feishu(config, 'yyscha ��ҳת�����Ӳ���\n���� ʾ���û���\n������һ��ģ�����á�\n\n�ظ�������ģ��ظ�������ʵ������Ϣ��\n[ͼƬ��ý����ʾʾ��]\n' + SITE + '/application/QQ/')
        print('����ҵ��code=0�����ڽ���Ⱥ�˶Բ������֡�������ý����ʾ��')
        return
    if args.command in ('check', 'preview'):
        ensure_time(config)
        for uid in config['uids']:
            data = source.page(args.day, uid, 0)
            print(f"{uid}: ҵ��ɹ���total={data['total']}����ҳUID����ͨ��")
            if not data['items']:
                print('��ҳΪ�գ�������������֤��UID������ --day ָ������Ϣ������')
            if args.command == 'preview':
                for item in data['items'][-3:]:
                    print(render(item, args.day), '\n')
        return
    if args.send and not config.get('feishu_webhook'):
        raise Failure('����ǰ�����÷���Webhook')
    db = connect(args.db)
    from media_feishu import ImageClient
    image_client = ImageClient(config, db) if args.send else None
    # ������������ռ�ö�ռ����������������ͬʱ����ͬһ���С�
    lock = sqlite3.connect(str(args.db) + '.lock', timeout=0)
    lock.execute('CREATE TABLE IF NOT EXISTS guard(id INTEGER)')
    try:
        lock.execute('BEGIN EXCLUSIVE')
    except sqlite3.OperationalError:
        raise Failure('ͬһ���ݿ�ļ��������Ѿ�����') from None
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
                poll(db, source, config)
                logging.info('��ѯ��ɣ�����״̬ %s', dict(db.execute('SELECT status,count(*) FROM messages GROUP BY status')))
            except AuthRequired as exc:
                failed = True
                logging.error('%s', exc)
                # Throttle alerts persistently, and use the existing notification
                # channel without sending passwords, cookie values or challenges.
                last = db.execute("SELECT value FROM meta WHERE key='auth_alert_at'").fetchone()
                if args.send and (not last or time.time() - float(last[0]) >= 6 * 3600):
                    try:
                        feishu(config, '��ҳ������¼ʧЧ\n�ɼ��ѵȴ��ָ������ߺͶ��б�����\n��򿪱����򿪷���ĵ�¼�ָ���ڣ����µ�¼����Cookie��������Զ����¶�ȡ���á�')
                    except Failure:
                        logging.error('��¼ʧЧ���ѷ���ʧ�ܣ���������')
                    else:
                        with db:
                            db.execute("INSERT OR REPLACE INTO meta VALUES('auth_alert_at',?)", (str(time.time()),))
            except Failure as exc:
                failed = True
                logging.error('%s�������α꣬��������', exc)
            if args.send:
                drain(db, config, image_client=image_client)
            if args.command == 'once':
                if failed:
                    raise Failure('���ֶ�ȡʧ�ܣ�δ���ȫ��ɨ��')
                break
            time.sleep(max(0, config.get('interval_seconds', 15) - (time.monotonic() - started)))
    finally:
        lock.close()
        db.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('��ֹͣ�����кͻ����ѱ���')
    except Failure as exc:
        logging.error('%s', exc)
        raise SystemExit(1)
    except Exception:
        logging.error('�������á����������ݿ��쳣����ֹͣ��������������̣����Զ����ԭʼ��Ӧ��ƾ�ݡ�')
        raise SystemExit(1)

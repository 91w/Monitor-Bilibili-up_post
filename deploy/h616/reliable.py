"""Transactional outbox and bounded paginated recovery for the existing watcher."""
import json, logging, sqlite3, time
from pathlib import Path

class Outbox:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS cursor(uid TEXT PRIMARY KEY, id TEXT);
        CREATE TABLE IF NOT EXISTS queue(uid TEXT, id TEXT, title TEXT, body TEXT,
        attempts INTEGER DEFAULT 0, due REAL DEFAULT 0, sent INTEGER DEFAULT 0,
        PRIMARY KEY(uid,id));''')

    def record(self, uid, cursor, messages):
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO queue(uid,id,title,body) VALUES(?,?,?,?)', messages)
            self.db.execute('INSERT OR REPLACE INTO cursor VALUES(?,?)', (uid,cursor))

    def deliver(self, send, now):
        rows = self.db.execute('SELECT uid,id,title,body,attempts FROM queue WHERE sent=0 AND due<=? ORDER BY rowid LIMIT 50',(now,)).fetchall()
        for uid, ident, title, body, attempts in rows:
            try:
                send(title,body)
            except Exception as exc:
                with self.db:
                    self.db.execute('UPDATE queue SET attempts=attempts+1,due=? WHERE uid=? AND id=?',
                                    (now+min(3600,30*2**min(attempts,7)),uid,ident))
                logging.warning('Delivery deferred uid=%s id=%s error_type=%s',uid,ident,type(exc).__name__)
            else:
                with self.db:
                    self.db.execute('UPDATE queue SET sent=1 WHERE uid=? AND id=?',(uid,ident))
                logging.info('Delivered queued dynamic uid=%s id=%s',uid,ident)

def fetch_pages(w, user, config, old):
    cookie = str(config.get('bilibili_cookie','')).strip() or None
    if config.get('strip_bilibili_read_state_cookies', True):
        cookie = w.strip_bilibili_read_state_cookies(cookie)
    offset, seen, result = '', set(), {}
    limit = max(1,min(500,int(config.get('recovery_max_pages',100))))
    for page in range(limit):
        params={'host_mid':user.uid,'timezone_offset':'-480','features':w.BILIBILI_DYNAMIC_FEATURES}
        if offset: params['offset']=offset
        payload=w.request_bilibili_json(w.BILIBILI_DYNAMIC_URL,params,user.uid,cookie,'feed',prefer_cookie=bool(config.get('prefer_bilibili_cookie',False)))
        if payload.get('code') != 0: raise w.WatcherError('History API rejected request')
        data=payload.get('data',{})
        items=w.sorted_feed_dynamics(data.get('items') or [])
        for item in items:
            ident=str(item.get('id_str') or item.get('id') or '')
            if ident.isdigit(): result[ident]=item
        reached=old and any(int(i)<=int(old) and not w.is_pinned_dynamic(v) for i,v in result.items())
        if not old or reached or not data.get('has_more'): return list(result.values())
        next_offset=str(data.get('offset') or '')
        if not next_offset or next_offset in seen: raise w.WatcherError('History pagination stalled; cursor retained')
        seen.add(next_offset); offset=next_offset
        time.sleep(.5)
    raise w.WatcherError('History page limit reached; cursor retained; increase recovery_max_pages')

def install(w, base, ensure_time):
    box=Outbox(base/'data/outbox.sqlite3')
    # Import legacy progress once. Never overwrite an existing queue cursor.
    state=json.loads((base/'data/state.json').read_text())
    with box.db:
        box.db.executemany('INSERT OR IGNORE INTO cursor VALUES(?,?)',state.get('last_dynamic_ids',{}).items())
    last_poll=[-1e9]
    original_check=w.check_once
    def check(config,state_path,dry_run=False):
        ensure_time()
        if dry_run: return original_check(config,state_path,dry_run=True)
        if time.monotonic()-last_poll[0]>=int(config.get('poll_interval_seconds',300)):
            last_poll[0]=time.monotonic()
            try:
                alerts=w.normalize_state(w.load_json(state_path))
                args=(alerts,config.get('bilibili_cookie') or None,config['feishu_webhook'],config.get('feishu_secret') or None,config.get('feishu_keyword') or None,bool(config.get('cookie_alert_enabled',True)))
                w.maybe_alert_cookie_expiry(*args,int(config.get('cookie_expire_warn_days',14)),int(config.get('cookie_alert_interval_hours',24)),False)
                w.maybe_alert_cookie_login(*args,int(config.get('cookie_alert_interval_hours',24)),False)
                w.save_json(state_path,alerts)
            except Exception as exc:
                logging.warning('Cookie health check deferred: %s',type(exc).__name__)
            for user in w.normalize_up_users(config.get('up_users',[])):
                try:
                    row=box.db.execute('SELECT id FROM cursor WHERE uid=?',(user.uid,)).fetchone()
                    old=row[0] if row else ''
                    items=fetch_pages(w,user,config,old)
                    if not items: continue
                    items=sorted(items,key=lambda x:int(x.get('id_str') or x.get('id')))
                    newest=str(items[-1].get('id_str') or items[-1].get('id'))
                    pending=[v for v in items if old and int(v.get('id_str') or v.get('id'))>int(old)]
                    if not old and config.get('notify_on_first_run',False): pending=items[-1:]
                    messages=[]
                    for dynamic in pending:
                        item=w.extract_dynamic(dynamic,user.uid,user.name)
                        item=w.fill_dynamic_detail_text(item,user.uid,user.name,config.get('bilibili_cookie') or None,dynamic)
                        title,body=w.format_message(item,config.get('feishu_keyword') or None)
                        messages.append((user.uid,item['id'],title,body))
                    box.record(user.uid,newest if not old or int(newest)>int(old) else old,messages)
                    logging.info('History checked uid=%s queued=%s',user.uid,len(messages))
                except Exception as exc:
                    logging.warning('Collection deferred uid=%s error_type=%s reason=%s',user.uid,type(exc).__name__,str(exc))
        def send(title,body):
            ensure_time()
            w.post_to_feishu(config['feishu_webhook'],title,body,config.get('feishu_secret') or None)
        box.deliver(send,time.time())
        return 0
    w.check_once=check
    return box

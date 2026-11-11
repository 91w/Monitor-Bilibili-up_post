"""Only approved messages enter this durable outbox; no raw event logging."""
import hashlib,json,sqlite3,time
from pathlib import Path
from message_filter import should_forward
from message_rules import group_rule,clean_segments,plain_text,join_text

def event_key(event):
    value=':'.join(str(event.get(k,'')) for k in ('self_id','group_id','message_id'))
    if not event.get('self_id') or event.get('message_id') is None:raise ValueError('Missing message identity')
    return hashlib.sha256(value.encode()).hexdigest()

def selected_message(event,cfg):
    if not should_forward(event,cfg['allowed_group_ids'],cfg.get('include_owner',False)):return None
    if str(event.get('user_id',event['sender']['user_id']))==str(event.get('self_id')):return None
    stamp=int(event.get('time',0))
    if stamp<int(cfg['start_epoch']):return None
    segments=event.get('message')
    if isinstance(segments,str):segments=[{'type':'text','data':{'text':segments}}]
    if not isinstance(segments,list):raise ValueError('Invalid message segments')
    cleaned=clean_segments(segments,group_rule(cfg,event['group_id']))
    if not cleaned:return None
    text=''.join(str(s['data'].get('text','')) for s in cleaned)
    if cfg.get('include_keywords') and not any(w in text for w in cfg['include_keywords']):return None
    if any(w in text for w in cfg.get('exclude_keywords',[])):return None
    sender=event['sender']
    return {'group_id':str(event['group_id']),'user_id':str(sender['user_id']),
            'name':str(sender.get('card') or sender.get('nickname') or sender['user_id']),
            'role':sender['role'],'time':stamp,'message_id':str(event['message_id']),'segments':cleaned}

class Outbox:
    def __init__(self,path):
        self.db=sqlite3.connect(path,timeout=15);self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS messages(
          key TEXT PRIMARY KEY,payload TEXT NOT NULL,target TEXT NOT NULL,created REAL NOT NULL,
          next_at REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,
          plan TEXT,part INTEGER NOT NULL DEFAULT 0,sent REAL,last_error TEXT);
          CREATE INDEX IF NOT EXISTS pending ON messages(sent,next_at);
          CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
        # Idempotent migration; old queued messages remain closed and immediately eligible.
        columns={r['name'] for r in self.db.execute('PRAGMA table_info(messages)')}
        for name,definition in [('closed','INTEGER NOT NULL DEFAULT 1'),('merge_group','TEXT'),
                                ('merge_user','TEXT'),('ready_at','REAL NOT NULL DEFAULT 0'),('discarded','REAL')]:
            if name not in columns:
                try:self.db.execute('ALTER TABLE messages ADD COLUMN '+name+' '+definition)
                except sqlite3.OperationalError:
                    if name not in {r['name'] for r in self.db.execute('PRAGMA table_info(messages)')}:raise
        self.db.executescript('''CREATE TABLE IF NOT EXISTS message_events(
            key TEXT PRIMARY KEY,batch_key TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS batch_events ON message_events(batch_key);
            CREATE INDEX IF NOT EXISTS merge_open ON messages(merge_group,closed);''')
    def accept(self,event,cfg):
        payload=selected_message(event,cfg)
        if payload is None:return 'filtered'
        key=event_key(event);target=hashlib.sha256(cfg['feishu_webhook'].encode()).hexdigest()
        now=time.time();rule=group_rule(cfg,payload['group_id']);text=plain_text(payload)
        idle=max(1,min(30,float(rule.get('merge_idle_seconds',5))))
        maximum=max(idle,min(60,float(rule.get('merge_max_seconds',20))))
        merge=bool(rule.get('merge_text') and text is not None and len(text)<=2400)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if (self.db.execute('SELECT 1 FROM messages WHERE key=?',(key,)).fetchone() or
                self.db.execute('SELECT 1 FROM message_events WHERE key=?',(key,)).fetchone()):return 'duplicate'
            row=self.db.execute('SELECT * FROM messages WHERE merge_group=? AND closed=0 AND sent IS NULL AND discarded IS NULL ORDER BY created DESC LIMIT 1',
                                (payload['group_id'],)).fetchone()
            old=json.loads(row['payload']) if row else None
            if (merge and row and row['merge_user']==payload['user_id'] and row['target']==target and
                row['ready_at']>now and now<row['created']+maximum and row['attempts']==0 and row['plan'] is None and
                0<=payload['time']-old.get('time_end',old['time'])<=idle and payload['time']-old['time']<=maximum and
                old.get('merged_count',1)<20 and len(plain_text(old))+len(text)+1<=2400):
                old['segments']=[{'type':'text','data':{'text':join_text(plain_text(old),text)}}]
                old['message_ids']=old.get('message_ids',[old['message_id']])+[payload['message_id']]
                old['merged_count']=len(old['message_ids']);old['time_end']=payload['time']
                self.db.execute('UPDATE messages SET payload=?,ready_at=? WHERE key=?',
                                (json.dumps(old,ensure_ascii=False),min(now+idle,row['created']+maximum),row['key']))
                self.db.execute('INSERT INTO message_events VALUES(?,?)',(key,row['key']))
                return 'queued'
            if row:self.db.execute('UPDATE messages SET closed=1,ready_at=0 WHERE key=?',(row['key'],))
            self.db.execute('INSERT INTO messages(key,payload,target,created,closed,merge_group,merge_user,ready_at) VALUES(?,?,?,?,?,?,?,?)',
                            (key,json.dumps(payload,ensure_ascii=False),target,now,int(not merge),payload['group_id'],payload['user_id'],now+idle if merge else 0))
            self.db.execute('INSERT INTO message_events VALUES(?,?)',(key,key))
        return 'queued'
    def next(self,now=None,cfg=None):
        now=time.time() if now is None else now
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            while True:
                row=self.db.execute('SELECT * FROM messages WHERE sent IS NULL AND discarded IS NULL AND next_at<=? AND ready_at<=? ORDER BY created LIMIT 1',(now,now)).fetchone()
                if row is None:return None
                payload=json.loads(row['payload']);text=plain_text(payload)
                if cfg and text is not None and any(w in text or w in text.replace('\n','') for w in cfg.get('exclude_keywords',[])):
                    self.db.execute("UPDATE messages SET discarded=?,closed=1,payload='{}',plan=NULL WHERE key=?",(now,row['key']))
                    continue
                # Seal before network work, so concurrent ingestion cannot mutate a delivery.
                self.db.execute('UPDATE messages SET closed=1 WHERE key=?',(row['key'],))
                return row
    def prepare(self,key,plan):
        with self.db:self.db.execute('UPDATE messages SET plan=? WHERE key=? AND plan IS NULL',(json.dumps(plan,ensure_ascii=False),key))
    def advance(self,key,part):
        with self.db:self.db.execute('UPDATE messages SET part=?,last_error=NULL WHERE key=?',(part,key))
    def complete(self,key):
        with self.db:self.db.execute('UPDATE messages SET sent=?,last_error=NULL WHERE key=?',(time.time(),key))
    def fail(self,row,reason):
        delay=min(1800,5*2**min(row['attempts'],9))
        with self.db:self.db.execute('UPDATE messages SET attempts=attempts+1,next_at=?,last_error=? WHERE key=?',
                                    (time.time()+delay,reason[:80],row['key']))
    def get(self,key,default=None):
        row=self.db.execute('SELECT value FROM state WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def set(self,key,value):
        with self.db:self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',(key,json.dumps(value)))
    def status(self):
        return dict(self.db.execute('SELECT COUNT(*) total,COALESCE(SUM(sent IS NULL AND discarded IS NULL),0) pending,COALESCE(SUM(sent IS NOT NULL),0) sent,COALESCE(SUM(discarded IS NOT NULL),0) discarded,COALESCE(SUM(closed=0 AND sent IS NULL AND discarded IS NULL),0) buffering FROM messages').fetchone())
    def expire(self,now=None):
        now=time.time() if now is None else now
        # Keep hashes as deduplication tombstones for 90 days, without message content after 7 days.
        with self.db:
            self.db.execute("UPDATE messages SET payload='{}',plan=NULL WHERE sent<? AND payload!='{}'",(now-7*86400,))
            self.db.execute('DELETE FROM messages WHERE sent<? OR discarded<?',(now-90*86400,now-90*86400))
            self.db.execute('DELETE FROM message_events WHERE batch_key NOT IN (SELECT key FROM messages)')
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_uploads'").fetchone():
                self.db.execute('DELETE FROM image_uploads WHERE uploaded_at<?',(now-7*86400,))
        self.db.execute('PRAGMA wal_checkpoint(PASSIVE)')

def deliver_one(box,cfg,prepare,send,ensure_time):
    row=box.next(cfg=cfg)
    if row is None:return False
    try:
        ensure_time()
        if row['target']!=hashlib.sha256(cfg['feishu_webhook'].encode()).hexdigest():raise RuntimeError('recipient_changed')
        plan=json.loads(row['plan']) if row['plan'] else prepare(row['key'],json.loads(row['payload']))
        if not isinstance(plan,list) or not plan:raise RuntimeError('empty_plan')
        box.prepare(row['key'],plan)
        for i in range(row['part'],len(plan)):
            ensure_time();send(plan[i]);box.advance(row['key'],i+1)
        box.complete(row['key']);return True
    except Exception as error:
        # Never persist exception text: it may contain URLs with tokens or message bodies.
        box.fail(row,type(error).__name__);return False

"""Archive-independent message identities, retaining within-archive multiplicity."""
import hashlib
import json
import re
from collections import defaultdict
from urllib.parse import urlparse,parse_qs


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS message_identities(
      logical_key TEXT PRIMARY KEY,scope TEXT,uid TEXT,ts REAL,fingerprint TEXT,occurrence INTEGER,owner_id TEXT,weak_fingerprint TEXT);
    CREATE INDEX IF NOT EXISTS message_identity_lookup ON message_identities(scope,fingerprint);
    CREATE TABLE IF NOT EXISTS message_aliases(
      scope TEXT,id TEXT,logical_key TEXT,record_id TEXT,PRIMARY KEY(scope,id));
    ''')
    if 'weak_fingerprint' not in {row[1] for row in db.execute('PRAGMA table_info(message_identities)')}:
        db.execute('ALTER TABLE message_identities ADD COLUMN weak_fingerprint TEXT')
        db.commit()


def describe(item,day):
    import relay as r
    from media_feishu import extract_images
    parts=r.message_parts(item,day)
    media=extract_images(item)
    quote=parts.get('quote') or {}
    resources=[]
    for asset in media+list(parts.get('files') or []):
        parsed=urlparse(asset['url'])
        resources.append([asset.get('context','body'),parse_qs(parsed.query).get('path',[''])[0]])
    # Keep user-authored hyperlinks, excluding archive resource signatures.
    root=r.DOM(item.get('html') or '').root
    content=root.find(lambda n:n.has('content')) or root
    links=[]
    def visit(node):
        if node.tag in ('script','style','iframe'):return
        if node.tag=='a':
            href=node.attrs.get('href','')
            p=urlparse(href)
            if p.scheme in ('http','https') and p.path!='/application/QQ/serve.php':links.append(href)
        for child in node.children:
            if isinstance(child,r.Node):visit(child)
    visit(content)
    fp=digest({'uid':item['uid'],'ts':float(item.get('ts') or 0), 'type':item.get('type') or 'text',
               'body':parts['body'],'quote':{k:quote.get(k) for k in ('author','time','text')},
               'resources':resources,'links':links})
    record=item.get('record_id')
    record=record if isinstance(record,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',record) else ''
    weak=digest({'uid':item['uid'],'ts':float(item.get('ts') or 0),'type':item.get('type') or 'text',
                 'body':parts['body'],'links':links,'resource_roles':[x[0] for x in resources]})
    return {'id':item['id'],'uid':item['uid'],'ts':float(item.get('ts') or 0),'fingerprint':fp,'weak_fingerprint':weak,'record_id':record}


def describe_all(items,day):
    counts=defaultdict(int);result=[];seen=set()
    for item in items:
        if item['id'] in seen:continue
        seen.add(item['id']);value=describe(item,day)
        counts[(value['record_id'],value['fingerprint'])]+=1
        value['occurrence']=counts[(value['record_id'],value['fingerprint'])]
        result.append(value)
    return result


def needs_full_snapshot(db,scope,items,day):
    for item in items:
        if db.execute('SELECT 1 FROM message_aliases WHERE scope=? AND id=?',(scope,item['id'])).fetchone():continue
        value=describe(item,day)
        if value['record_id'] and db.execute('SELECT 1 FROM message_identities WHERE scope=? AND (fingerprint=? OR weak_fingerprint=?)',(scope,value['fingerprint'],value['weak_fingerprint'])).fetchone():return True
    return False


def register(db,scope,value,complete=True):
    existing=db.execute('SELECT logical_key FROM message_aliases WHERE scope=? AND id=?',(scope,value['id'])).fetchone()
    if existing:
        key=existing[0]
        return key,db.execute('SELECT owner_id FROM message_identities WHERE logical_key=?',(key,)).fetchone()[0],False
    # Partial snapshots reach here only when the fingerprint has no existing
    # identities; collisions must first be reread from offset zero.
    key=digest([scope,value['fingerprint'],value['occurrence']]) if value['record_id'] else digest([scope,'source-id',value['id']])
    exact=db.execute('SELECT 1 FROM message_identities WHERE logical_key=?',(key,)).fetchone()
    ambiguous=bool(not exact and value['record_id'] and db.execute('SELECT 1 FROM message_identities i JOIN message_aliases a ON a.logical_key=i.logical_key WHERE i.scope=? AND i.weak_fingerprint=? AND a.record_id<>? LIMIT 1',(scope,value['weak_fingerprint'],value['record_id'])).fetchone())
    db.execute('INSERT OR IGNORE INTO message_identities(logical_key,scope,uid,ts,fingerprint,occurrence,owner_id,weak_fingerprint) VALUES(?,?,?,?,?,?,?,?)',
               (key,scope,value['uid'],value['ts'],value['fingerprint'],value['occurrence'],value['id'],value['weak_fingerprint']))
    owner=db.execute('SELECT owner_id FROM message_identities WHERE logical_key=?',(key,)).fetchone()[0]
    db.execute('INSERT INTO message_aliases VALUES(?,?,?,?)',(scope,value['id'],key,value['record_id']))
    return key,owner,ambiguous


def owner_for(db,scope,mid):
    row=db.execute('SELECT i.owner_id FROM message_aliases a JOIN message_identities i ON i.logical_key=a.logical_key WHERE a.scope=? AND a.id=?',(scope,mid)).fetchone()
    return row[0] if row else mid


def seed(db,snapshots):
    """Backfill identities from verified full snapshots; never enqueue messages."""
    groups=defaultdict(list)
    for snapshot in snapshots:
        for value in snapshot['identities']:
            value=dict(value);value['scope']=snapshot['scope']
            key=digest([snapshot['scope'],value['fingerprint'],value['occurrence']]) if value['record_id'] else digest([snapshot['scope'],'source-id',value['id']])
            groups[key].append(value)
    states={(s,mid):state for s,mid,state in db.execute('SELECT scope,id,status FROM messages')}
    selected=0
    for key,values in groups.items():
        known=[v for v in values if (v['scope'],v['id']) in states]
        if not known:continue
        # A fully completed equivalent message supersedes unfinished copies.
        priority={'sent':0,'baseline':1,'retry':2,'pending':3,'duplicate':4,'quarantined':5}
        known.sort(key=lambda v:(priority.get(states[(v['scope'],v['id'])],6),v['record_id'],v['id']))
        owner=known[0]
        db.execute('INSERT OR IGNORE INTO message_identities(logical_key,scope,uid,ts,fingerprint,occurrence,owner_id,weak_fingerprint) VALUES(?,?,?,?,?,?,?,?)',(key,owner['scope'],owner['uid'],owner['ts'],owner['fingerprint'],owner['occurrence'],owner['id'],owner['weak_fingerprint']))
        for value in known:
            db.execute('INSERT OR IGNORE INTO message_aliases VALUES(?,?,?,?)',(value['scope'],value['id'],key,value['record_id']))
            db.execute('UPDATE messages SET logical_key=?,source_record=? WHERE scope=? AND id=?',(key,value['record_id'],value['scope'],value['id']))
            selected+=1
    return selected

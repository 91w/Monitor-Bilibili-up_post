"""Quiescent rotation of an exact message-DB allowlist. No live SQLite edits."""
import hashlib,json,os,stat,time
from pathlib import Path

DATABASES=('nt_msg.db','group_msg_fts.db','buddy_msg_fts.db','discuss_msg_fts.db','data_line_msg_fts.db','msg_fts.db')
SUFFIXES=('','-wal','-shm','-first.material','-last.material')
NAMES={n+s for n in DATABASES for s in SUFFIXES}

def digest(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda:f.read(65536),b''):h.update(block)
    return h.hexdigest()

def atomic(p,data):
    p=Path(p);tmp=p.with_suffix('.new')
    with tmp.open('w',encoding='utf-8') as f:
        os.chmod(tmp,0o600);json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    tmp.replace(p)
    if os.name=='posix':
        fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)

def candidates(db):
    db=Path(db)
    if db.is_symlink() or not db.is_dir():raise RuntimeError('Unsafe message DB directory')
    found=[]
    for name in sorted(NAMES):
        p=db/name
        if p.is_symlink():raise RuntimeError('Symlink in message DB allowlist')
        if not p.exists():continue
        st=p.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1:raise RuntimeError('Unexpected message DB file type')
        found.append(p)
    return found

def opened_inodes():
    found=set()
    for fd in Path('/proc').glob('[0-9]*/fd/*'):
        try:
            st=fd.stat();found.add((st.st_dev,st.st_ino))
        except OSError:pass
    return found

def rotate(db,backup,opened=None):
    db=Path(db);backup=Path(backup)
    files=candidates(db)
    if not any(p.name=='nt_msg.db' for p in files):raise RuntimeError('Main message DB missing')
    opened=opened_inodes() if opened is None else opened
    for p in files:
        st=p.stat()
        if (st.st_dev,st.st_ino) in opened:raise RuntimeError('Message DB still open; nothing rotated')
    backup.mkdir(mode=0o700,parents=True,exist_ok=False)
    if db.stat().st_dev!=backup.stat().st_dev:raise RuntimeError('Backup must be on same filesystem')
    manifest={'version':1,'epoch':int(time.time()),'stage':'preparing','files':[{'name':p.name,'bytes':p.stat().st_size,'sha256':digest(p)} for p in files]}
    atomic(backup/'manifest.json',manifest)
    moved=[]
    try:
        for p in files:
            p.replace(backup/p.name);moved.append(p.name)
        manifest['stage']='awaiting_verification';atomic(backup/'manifest.json',manifest)
    except BaseException:
        for name in reversed(moved):(backup/name).replace(db/name)
        manifest['stage']='rolled_back';atomic(backup/'manifest.json',manifest);raise
    return manifest

def restore(db,backup,opened=None):
    db=Path(db);backup=Path(backup);manifest=json.loads((backup/'manifest.json').read_text())
    items=manifest['files']
    if any(x['name'] not in NAMES for x in items):raise RuntimeError('Backup manifest contains unapproved file')
    if manifest['stage']=='backup_deleted':raise RuntimeError('Backup retention already ended')
    if manifest['stage']=='preparing':
        # A power loss can interrupt a multi-file rename before the commit marker.
        opened=opened_inodes() if opened is None else opened
        for item in items:
            old=backup/item['name'];original=db/item['name']
            if old.exists() and original.exists():raise RuntimeError('Ambiguous interrupted snapshot')
            source=old if old.exists() else original
            if source.is_symlink() or not source.is_file() or digest(source)!=item['sha256']:raise RuntimeError('Interrupted snapshot integrity mismatch')
            st=source.stat()
            if (st.st_dev,st.st_ino) in opened:raise RuntimeError('Interrupted snapshot is still open')
        for item in items:
            old=backup/item['name']
            if old.exists():old.replace(db/item['name'])
        manifest['stage']='rolled_back';atomic(backup/'manifest.json',manifest);return
    for item in items:
        p=backup/item['name']
        if p.is_symlink() or not p.is_file() or digest(p)!=item['sha256']:raise RuntimeError('Backup integrity mismatch')
    opened=opened_inodes() if opened is None else opened
    for p in candidates(db):
        st=p.stat()
        if (st.st_dev,st.st_ino) in opened:raise RuntimeError('Cannot restore while DB is open')
    fresh=backup/'new-before-restore';fresh.mkdir(mode=0o700,exist_ok=False)
    for p in candidates(db):p.replace(fresh/p.name)
    for item in items:(backup/item['name']).replace(db/item['name'])
    manifest['stage']='rolled_back';atomic(backup/'manifest.json',manifest)

def purge_verified_backup(backup,now=None):
    backup=Path(backup);manifest=json.loads((backup/'manifest.json').read_text())
    now=time.time() if now is None else now
    if manifest.get('stage') not in ('verified','purging') or not 0<=now-manifest.get('verified_epoch',now)<365*86400 or now-manifest.get('verified_epoch',now)<86400:return 0
    # Verify everything before any deletion. Never touch a failed/unverified snapshot.
    for item in manifest['files']:
        if item['name'] not in NAMES:raise RuntimeError('Unapproved backup filename')
        p=backup/item['name']
        if manifest['stage']=='purging' and not p.exists() and not p.is_symlink():continue
        if p.is_symlink() or not p.is_file() or digest(p)!=item['sha256']:raise RuntimeError('Backup integrity mismatch')
    manifest['stage']='purging';atomic(backup/'manifest.json',manifest)
    freed=0
    for item in manifest['files']:
        p=backup/item['name']
        if p.exists():freed+=p.stat().st_size;p.unlink()
    manifest.update(stage='backup_deleted',deleted_epoch=int(now));atomic(backup/'manifest.json',manifest)
    return freed

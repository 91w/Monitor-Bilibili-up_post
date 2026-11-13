"""Paused-only identity migration: dry-run by default; never sends messages."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import os
import shutil
import uuid

import relay as r
from history_test import fetch_day
from message_identity import describe_all, seed, owner_for


def reconcile(db, snapshots):
    before=dict(db.execute('SELECT status,count(*) FROM messages GROUP BY status'))
    with db:
        mapped=seed(db,snapshots)
        duplicate,unknown=0,0
        for scope,mid in db.execute("SELECT scope,id FROM messages WHERE status IN ('pending','retry')").fetchall():
            owner=owner_for(db,scope,mid)
            if owner!=mid:
                db.execute("UPDATE messages SET status='duplicate',duplicate_of=?,error='跨归档重复消息，迁移时隔离' WHERE scope=? AND id=?",(owner,scope,mid));duplicate+=1
            elif not db.execute('SELECT 1 FROM message_aliases WHERE scope=? AND id=?',(scope,mid)).fetchone():
                db.execute("UPDATE messages SET status='quarantined',error='当前快照无此ID，待核对' WHERE scope=? AND id=?",(scope,mid));unknown+=1
        db.execute("INSERT OR REPLACE INTO meta VALUES('identity_guard_ready','1')")
        db.execute("INSERT OR REPLACE INTO meta VALUES('identity_revision','v19')")
    return {'before':before,'after':dict(db.execute('SELECT status,count(*) FROM messages GROUP BY status')),
            'mapped_rows':mapped,'duplicates_isolated':duplicate,'unresolved_quarantined':unknown,'messages_sent':0}


def fetch_snapshots(db,config):
    source=r.Source(config);snapshots=[]
    for day,uid,scope in db.execute('SELECT day,uid,scope FROM streams'):
        if uid not in config['uids']:raise r.Failure('迁移名单与已有流不符，未应用')
        head=source.page(day,uid,0)
        if r.SITE+'/'+str(head['day'].get('chat_key'))!=scope:raise r.Failure('迁移源站聊天范围不符，未应用')
        class CachedHead:
            def page(self,d,u,offset):return head if offset==0 else source.page(d,u,offset)
        identities=describe_all(fetch_day(CachedHead(),day,uid),day)
        if any(not value['record_id'] for value in identities):raise r.Failure('源站缺少归档标识，未应用')
        snapshots.append({'day':day,'scope':scope,'uid':uid,'identities':identities})
    return snapshots


def run(config_path,db_path,apply=False):
    config_path,db_path=Path(config_path),Path(db_path)
    if not db_path.is_file():raise r.Failure('原数据库不存在，不能建立空库代替迁移')
    if 'outputs' in {part.lower() for part in db_path.resolve().parts}:raise r.Failure('运行数据库必须位于私密目录')
    if (config_path.parent/'enabled').exists():raise r.Failure('服务仍启用，请先停止')
    config=r.load_config(config_path)
    lock=sqlite3.connect(str(db_path)+'.lock',timeout=0)
    try:
        lock.execute('CREATE TABLE IF NOT EXISTS guard(id INTEGER)')
        lock.execute('BEGIN EXCLUSIVE')
        temporary=(db_path.parent/('.identity-migration-'+uuid.uuid4().hex)).resolve()
        if not temporary.is_relative_to(db_path.parent.resolve()):raise r.Failure('迁移临时目录不符')
        temporary.mkdir(mode=0o777 if os.name=='nt' else 0o700)
        try:
            trial_path=temporary/'trial.sqlite3'
            with closing(sqlite3.connect(db_path)) as source,closing(sqlite3.connect(trial_path)) as target:source.backup(target)
            trial=r.connect(trial_path)
            try:
                snapshots=fetch_snapshots(trial,config)
                report=reconcile(trial,snapshots)
            finally:trial.close()
        finally:
            if temporary.resolve().is_relative_to(db_path.parent.resolve()):shutil.rmtree(temporary)
        if apply:
            current=r.connect(db_path)
            try:report=reconcile(current,snapshots)
            finally:current.close()
        report['applied']=apply
        return report
    except sqlite3.OperationalError:
        raise r.Failure('迁移锁或数据库不可用，请先停止服务并检查私密路径') from None
    finally:lock.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=r.PRIVATE/'config.json')
    parser.add_argument('--db',type=Path,default=r.PRIVATE/'state.sqlite3')
    parser.add_argument('--apply',action='store_true',help='审核试运行结果并做好备份后才应用')
    args=parser.parse_args()
    try:print(json.dumps(run(args.config,args.db,args.apply),ensure_ascii=False))
    except r.Failure as exc:print(str(exc));return 1
    return 0

if __name__=='__main__':raise SystemExit(main())

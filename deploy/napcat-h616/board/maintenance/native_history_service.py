"""A threshold-triggered, journaled maintenance transaction for the local QQ DB."""
import argparse,fcntl,json,os,sys,time
from pathlib import Path
from native_history import atomic,candidates,rotate,restore,purge_verified_backup

BASE=Path('/overlay/napcat-lab');ROOT=BASE/'rootfs';POLICY=BASE/'native-history-policy.json';STATE=BASE/'native-history-state.json'
sys.path.insert(0,str(BASE/'relay'))
from queue_store import Outbox
from relay_service import api,ensure_time,catch_up,Sender

def read(p,default=None):
    try:return json.loads(Path(p).read_text())
    except FileNotFoundError:return default
def config():return read(BASE/'forward-config.json')
def dbdir():
    root=ROOT/'home/napcat/.config/QQ';found=list(root.glob('nt_qq_*/nt_db'))
    if len(found)!=1 or found[0].is_symlink() or not found[0].resolve().is_relative_to(root.resolve()):raise RuntimeError('Expected one safe account DB directory')
    return found[0]
def message_bytes():return sum(p.stat().st_size for p in candidates(dbdir()))
def enabled(value):
    c=config();c['enabled']=value;atomic(BASE/'forward-config.json',c)
def pending_check(recent=False):
    c=config();box=Outbox(BASE/'forward-outbox.sqlite3')
    try:
        if box.status()['pending']:raise RuntimeError('Pending messages exist; cleanup postponed')
        if recent:
            for g in c['allowed_group_ids']:
                state=box.get('history_status:'+str(g),{})
                if not state.get('complete') or not 0<=time.time()-state.get('epoch',0)<300:raise RuntimeError('History catch-up is not recent and complete')
    finally:box.db.close()
def wait_stopped():
    end=time.monotonic()+60;path=BASE/'cgroup/napcat-test/tasks'
    while time.monotonic()<end:
        if not path.exists() or not path.read_text().split():
            lock=(BASE/'lab-exec.lock').open('a')
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);return lock
            except BlockingIOError:lock.close()
        time.sleep(.5)
    raise RuntimeError('QQ did not quiesce within one minute')
def start_rotation(force=False):
    ensure_time();c=config();state=read(STATE,{})
    if not c.get('enabled') or (BASE/'storage-hold.json').exists():raise RuntimeError('QQ paused or storage protection active')
    if state.get('stage') in ('preparing','awaiting_verification','needs_phone_login','error'):raise RuntimeError('Previous cleanup needs attention')
    if state.get('stage')=='verified' and read(Path(state['backup'])/'manifest.json',{}).get('stage')!='backup_deleted':raise RuntimeError('Previous verified backup is still in its retention window')
    if not api(c,'get_status',{}).get('online'):raise RuntimeError('QQ must be online before cleanup')
    pending_check(recent=True)
    stats=os.statvfs(BASE)
    if stats.f_bavail*stats.f_frsize<5*1024**3:raise RuntimeError('Insufficient disk headroom')
    policy=read(POLICY,{'trigger_mib':3072})
    before=message_bytes()
    if not force and before<policy.get('trigger_mib',3072)*1024**2:return False
    tag=time.strftime('%Y%m%d-%H%M%S',time.gmtime())
    backup=BASE/'native-history-backups'/tag
    state={'stage':'preparing','started_epoch':int(time.time()),'backup':str(backup),'before_bytes':before}
    atomic(STATE,state);lock=None;changed=False
    try:
        enabled(False);lock=wait_stopped();pending_check(recent=True)
        manifest=rotate(dbdir(),backup);changed=True
        state.update(stage='awaiting_verification',moved_files=len(manifest['files']),preflight_bytes=before,before_bytes=sum(x['bytes'] for x in manifest['files']))
        atomic(STATE,state)
    except Exception as e:
        if changed:restore(dbdir(),backup)
        state.update(stage='error',error_type=type(e).__name__);atomic(STATE,state);raise
    finally:
        if lock:lock.close()
        enabled(True)
    print(json.dumps({'rotation_started':True,'files':state['moved_files'],'old_message_files_mib':round(state['before_bytes']/1024**2,2),'backup_preserved':True}),flush=True)
    return True
def verify():
    state=read(STATE,{})
    if state.get('stage') not in ('awaiting_verification','needs_phone_login'):return False
    c=config();online=False
    try:
        if not api(c,'get_status',{}).get('online'):raise RuntimeError('Offline')
        online=True
        login=api(c,'get_login_info',{})
        if str(login.get('user_id'))!=str(c['self_id']):raise RuntimeError('Account mismatch')
        box=Outbox(BASE/'forward-outbox.sqlite3')
        try:
            catch_up(box,c);box.set('history_error',None)
        finally:box.db.close()
        pending_check(recent=True)
        if not (dbdir()/'nt_msg.db').is_file():raise RuntimeError('Message DB was not recreated')
    except Exception:
        if time.time()-state['started_epoch']>120 and not online:
            state['stage']='needs_phone_login';atomic(STATE,state)
            if not state.get('notified'):
                try:
                    notices=Outbox(BASE/'verify-outbox.sqlite3')
                    try:Sender(c,notices)({'title':'QQ消息库维护提示','text':'消息库已备份，QQ维护检查尚未完成，可能需要手机授权或等待转发队列处理。请打开QQ管理入口检查；原消息备份和转发队列保留。'})
                    finally:notices.db.close()
                    state['notified']=True;atomic(STATE,state)
                except Exception:pass
        return False
    ensure_time();now=int(time.time());state.update(stage='verified',verified_epoch=now,after_bytes=message_bytes())
    atomic(STATE,state);backup=Path(state['backup']);manifest=read(backup/'manifest.json');manifest.update(stage='verified',verified_epoch=now);atomic(backup/'manifest.json',manifest)
    print(json.dumps({'verified':True,'qq_online':True,'before_mib':round(state['before_bytes']/1024**2,2),'after_mib':round(state['after_bytes']/1024**2,2),'backup_retention_hours':24}),flush=True)
    return True
def rollback():
    state=read(STATE,{})
    if not state.get('backup'):raise RuntimeError('No snapshot to restore')
    ensure_time();enabled(False);lock=None
    try:
        lock=wait_stopped();restore(dbdir(),Path(state['backup']));state['stage']='rolled_back';atomic(STATE,state)
    finally:
        if lock:lock.close()
        enabled(True)
    print('Original message DB restored. QQ may require phone authorization.')
def loop():
    import datetime as dt
    while True:
        transaction=(BASE/'native-history-maintenance.lock').open('a')
        try:
            fcntl.flock(transaction,fcntl.LOCK_EX|fcntl.LOCK_NB)
            ensure_time();state=read(STATE,{});verify()
            if state.get('stage')=='verified':purge_verified_backup(Path(state['backup']))
            policy=read(POLICY,{})
            now=dt.datetime.now(dt.timezone(dt.timedelta(hours=8)));day=now.strftime('%Y-%m-%d')
            if policy.get('enabled') and now.hour>=3 and policy.get('last_check_day')!=day:
                if time.time()-state.get('started_epoch',0)>7*86400:start_rotation()
                policy['last_check_day']=day;atomic(POLICY,policy)
        except Exception:pass
        finally:transaction.close()
        time.sleep(60)
def main():
    os.umask(0o077);parser=argparse.ArgumentParser();parser.add_argument('action',choices=('status','preview','rotate','verify','rollback','loop'));args=parser.parse_args()
    if args.action=='status':
        s=read(STATE,{});s.pop('backup',None);print(json.dumps({'policy':read(POLICY,{}),'state':s,'message_files_mib':round(message_bytes()/1024**2,2)}));return
    if args.action=='preview':
        pending_check(True);print(json.dumps({'candidate_files':len(candidates(dbdir())),'message_files_mib':round(message_bytes()/1024**2,2),'protected':'login, contacts, groups, settings, emoji and forward queue'}));return
    lock=(BASE/('native-history-worker.lock' if args.action=='loop' else 'native-history-maintenance.lock')).open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if args.action=='rotate':start_rotation(force=True)
    elif args.action=='verify':verify()
    elif args.action=='rollback':rollback()
    elif args.action=='loop':loop()
if __name__=='__main__':main()

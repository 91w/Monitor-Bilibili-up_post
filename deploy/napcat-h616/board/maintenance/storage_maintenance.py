"""Allowlisted file retention and a fail-closed QQ disk guard. Never prunes QQ DBs."""
import argparse,datetime as dt,fcntl,json,os,re,signal,stat,time
from pathlib import Path
MIB=1024**2;GIB=1024**3
BASE=Path('/overlay/napcat-lab')
DB_SUFFIXES=('.db','.sqlite','.sqlite3','.db-wal','.db-shm','-wal','-shm')

def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.new')
    with temp.open('w',encoding='utf-8') as f:
        os.chmod(temp,0o600);json.dump(data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    temp.replace(path)

def open_inodes():
    found=set()
    for process in Path('/proc').glob('[0-9]*'):
        try:
            for fd in (process/'fd').iterdir():
                try:
                    info=fd.stat()
                    if stat.S_ISREG(info.st_mode):found.add((info.st_dev,info.st_ino))
                except OSError:pass
        except OSError:pass
    return found

def safe_files(directory,base,max_files=100000):
    directory=Path(directory);base=Path(base).resolve()
    if directory.is_symlink() or not directory.exists() or not directory.resolve().is_relative_to(base):return
    count=0
    for current,dirs,files in os.walk(directory,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(current)/d).is_symlink()]
        for name in files:
            path=Path(current)/name
            try:info=path.lstat()
            except OSError:continue
            if not stat.S_ISREG(info.st_mode):continue
            count+=1
            if count>max_files:raise RuntimeError('Scan limit exceeded; cleanup stopped')
            yield path,info

def remove_checked(path,expected,base):
    base=Path(base).resolve();path=Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(base):return None
    if os.name=='posix':
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            actual_parent=Path('/proc/self/fd/'+str(fd)).resolve()
            if not actual_parent.is_relative_to(base):return None
            current=os.stat(path.name,dir_fd=fd,follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or (current.st_dev,current.st_ino)!=(expected.st_dev,expected.st_ino):return None
            freed=current.st_blocks*512 if current.st_nlink==1 else 0
            os.unlink(path.name,dir_fd=fd);return freed
        finally:os.close(fd)
    current=path.stat()
    if (current.st_dev,current.st_ino)!=(expected.st_dev,expected.st_ino):return None
    path.unlink();return current.st_size if current.st_nlink==1 else 0

def prune_bucket(base,files,now,days,budget,opened,apply=False):
    candidates=[];total=0
    for path,info in files:
        total+=info.st_size
        if path.name.lower().endswith(DB_SUFFIXES):continue
        if (info.st_dev,info.st_ino) in opened:continue
        age=now-max(info.st_mtime,info.st_ctime)
        if age<86400:continue
        candidates.append((max(info.st_mtime,info.st_ctime),path,info,age>=days*86400))
    result={'examined_bytes':total,'eligible_files':0,'removed_files':0,'freed_bytes':0}
    for _,path,info,expired in sorted(candidates,key=lambda x:x[0]):
        if not expired and total<=budget:continue
        result['eligible_files']+=1;total-=info.st_size
        if apply:
            try:freed=remove_checked(path,info,base)
            except (OSError,ValueError):continue
            if freed is not None:result['removed_files']+=1;result['freed_bytes']+=freed
    return result

def buckets(base):
    profile=base/'rootfs/home/napcat/.config/QQ'
    log_files=[]
    for p in base.iterdir():
        if re.fullmatch(r'(qq[a-zA-Z0-9_-]*|apt-[a-zA-Z0-9_-]+)\.log(?:\.[1-3])?',p.name) and not p.is_symlink():
            try:log_files.append((p,p.stat()))
            except OSError:pass
    yield 'test_logs',log_files,7,32*MIB
    yield 'napcat_logs',safe_files(base/'rootfs/opt/QQ/resources/app/napcat/logs',base),7,64*MIB
    crash=[];browser=[];native_logs=[]
    if profile.exists():
        for path,info in safe_files(profile,base):
            parts=path.relative_to(profile).parts
            if ('Crashpad' in parts or 'crash_files' in parts) and path.suffix.lower()=='.dmp':crash.append((path,info))
            elif any(p in ('Cache','Code Cache','GPUCache','DawnCache','GrShaderCache','ShaderCache') for p in parts):browser.append((path,info))
            elif any(p in ('log','logs','Log') for p in parts) and path.suffix.lower() in ('.log','.clog','.xlog','.txt'):native_logs.append((path,info))
    yield 'crash_dumps',crash,7,64*MIB
    yield 'browser_cache',browser,30,256*MIB
    yield 'qq_diagnostic_logs',native_logs,7,128*MIB
    yield 'completed_forward_media',safe_files(base/'forward-cache/completed',base),1,512*MIB
    # Pending media, QQ's nt_db/Login/msf/mmkv/Pic and arbitrary File directories
    # are intentionally outside all deletion buckets.
    archives=[]
    for name in ('QQ.deb','NapCat.Shell.zip','ubuntu-base-22.04.5-base-arm64.tar.gz','rootfs/var/tmp/QQ.deb'):
        p=base/name
        if p.exists() and not p.is_symlink():archives.append((p,p.stat()))
    yield 'old_install_archives',archives,14,1024*GIB
    yield 'apt_package_cache',((p,s) for p,s in safe_files(base/'rootfs/var/cache/apt/archives',base) if p.suffix=='.deb'),7,256*MIB

def cleanup(base=BASE,apply=False,now=None,opened=None):
    base=Path(base).resolve();now=time.time() if now is None else now
    opened=open_inodes() if opened is None else opened
    return {name:prune_bucket(base,files,now,days,cap,opened,apply) for name,files,days,cap in buckets(base)}

def profile_bytes(base):
    return sum(info.st_size for _,info in safe_files(Path(base)/'rootfs/home/napcat/.config/QQ',base))

def guard_level(free_bytes,qq_bytes):
    if free_bytes<3*GIB or qq_bytes>=4*GIB:return 'hold'
    if free_bytes<5*GIB or qq_bytes>=3*GIB:return 'warning'
    return 'ok'

def stop_qq(base):
    tasks=Path(base)/'cgroup/napcat-test/tasks'
    if not tasks.exists():return
    for pid in tasks.read_text().split():
        try:os.kill(int(pid),signal.SIGTERM)
        except ProcessLookupError:pass

def clock_ready():
    try:
        import sys
        if '/overlay/b-up/app' not in sys.path:sys.path.insert(0,'/overlay/b-up/app')
        from timeguard import require_trusted_time
        require_trusted_time();return True
    except Exception:return False

def run_once(base=BASE,apply=False,force_cleanup=False):
    base=Path(base).resolve();stats=os.statvfs(base);free=stats.f_bavail*stats.f_frsize
    now=time.time();trusted=clock_ready()
    state_file=base/'storage-health.json'
    try:previous=json.loads(state_file.read_text())
    except (OSError,ValueError):previous={}
    checked=previous.get('profile_checked_epoch',0);limited=False
    if force_cleanup or not 0<=now-checked<600 or free<5*GIB:
        try:profile=profile_bytes(base)
        except RuntimeError:profile=4*GIB;limited=True
        checked=now
    else:profile=previous.get('qq_profile_bytes',0)
    local=dt.datetime.fromtimestamp(now,dt.timezone(dt.timedelta(hours=8)))
    day=local.strftime('%Y-%m-%d')
    due=trusted and (force_cleanup or (local.hour>=3 and previous.get('last_cleanup_day')!=day) or guard_level(free,profile)!='ok')
    try:cleaned=cleanup(base,apply=apply,now=now) if due else {}
    except RuntimeError:cleaned={'scan_limit_reached':True};limited=True
    stats=os.statvfs(base);free=stats.f_bavail*stats.f_frsize
    if due:
        try:profile=profile_bytes(base)
        except RuntimeError:profile=4*GIB;limited=True
    if limited:profile=max(profile,4*GIB)
    level=guard_level(free,profile)
    state={'epoch':int(now),'level':level,'qq_profile_mib':round(profile/MIB,1),'qq_profile_bytes':profile,'profile_checked_epoch':checked,'free_gib':round(free/GIB,2),
           'clock_trusted':trusted,'last_cleanup_day':day if due and apply else previous.get('last_cleanup_day'),
           'last_cleanup':cleaned or previous.get('last_cleanup',{}),'hold_active':(base/'storage-hold.json').exists()}
    if apply and level=='hold':
        atomic(base/'storage-hold.json',{'epoch':int(now),'reason':'storage_limit','free_gib':state['free_gib'],'qq_profile_mib':state['qq_profile_mib']})
        stop_qq(base);state['hold_active']=True
    if apply:
        state['alert_delivery']=notify(state,base)
        atomic(state_file,state)
    return state

def notify(state,base):
    if state['level']=='ok':return 'not_needed'
    target=Path(base)/'forward-config.json'
    if not target.exists():return 'waiting_for_qq_feishu_config'
    try:
        cfg=json.loads(target.read_text());url=cfg.get('feishu_webhook','')
        from urllib.parse import urlparse
        p=urlparse(url)
        if p.scheme!='https' or p.hostname!='open.feishu.cn' or not p.path.startswith('/open-apis/bot/v2/hook/'):return 'invalid_target'
        marker=Path(base)/'storage-alert-state.json'
        sent=json.loads(marker.read_text()) if marker.exists() else {}
        if sent.get('level')==state['level'] and 0<=time.time()-sent.get('at',0)<86400:return 'already_notified'
        if not clock_ready():return 'waiting_for_clock'
        from bilibili_feishu_watcher import post_to_feishu
        title=(cfg.get('feishu_keyword','')+' QQ存储容量提醒').strip()
        body=f"状态：{state['level']}；剩余空间：{state['free_gib']} GiB；QQ用户数据：{state['qq_profile_mib']} MiB。"
        if state['hold_active']:body+='\n已暂停QQ测试/运行，保留登录数据、数据库与待转发消息；B站监听未停止。'
        post_to_feishu(url,title,body,cfg.get('feishu_secret') or None)
        atomic(marker,{'level':state['level'],'at':time.time()});return 'sent'
    except Exception:return 'retry_later'

if __name__=='__main__':
    os.umask(0o077)
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--loop',action='store_true');p.add_argument('--cleanup-now',action='store_true');p.add_argument('--resume',action='store_true');args=p.parse_args()
    if args.resume:
        stats=os.statvfs(BASE);free=stats.f_bavail*stats.f_frsize
        if free<5*GIB or profile_bytes(BASE)>=3*GIB:raise SystemExit('Not enough safe storage headroom to clear hold')
        (BASE/'storage-hold.json').unlink(missing_ok=True)
        print('Storage hold cleared; QQ was not automatically restarted');raise SystemExit(0)
    lock=(BASE/'storage-maintenance.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Storage maintenance already running')
    while True:
        try:
            state=run_once(apply=args.apply,force_cleanup=args.cleanup_now)
            if not args.loop:print(json.dumps(state));break
        except Exception:
            if not args.loop:raise
            if args.apply:
                try:atomic(BASE/'storage-hold.json',{'epoch':int(time.time()),'reason':'maintenance_error'})
                finally:stop_qq(BASE)
        time.sleep(60)

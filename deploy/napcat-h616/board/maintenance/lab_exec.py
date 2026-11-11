import json,os,signal,subprocess,sys,time,resource,fcntl,threading,re
from collections import deque
from rotating_sink import RotatingSink
from login_settings import environment_for_login
from pathlib import Path
base=Path('/overlay/napcat-lab');group=base/'cgroup/napcat-test'
os.umask(0o077)
label=sys.argv[1];timeout=int(sys.argv[2]);cmd=sys.argv[3:]
if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',label):raise SystemExit('Invalid test label')
if (base/'storage-hold.json').exists():raise SystemExit('QQ storage protection active; inspect storage-health.json before retrying')
if os.statvfs(base).f_bavail*os.statvfs(base).f_frsize<3*1024**3:raise SystemExit('QQ storage protection: less than 3GiB free')
if not group.exists():raise SystemExit('Memory guard not ready')
lock=(base/'lab-exec.lock').open('a')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:raise SystemExit('Another test is still active; try again after it stops')
(group/'memory.max_usage_in_bytes').write_text('0')
def before():
    os.setsid();os.nice(10);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    (group/'tasks').write_text(str(os.getpid()))
    Path('/proc/self/oom_score_adj').write_text('500')
start=time.monotonic();samples=deque(maxlen=600);reason='completed';stopping=False;peak_rss=0;min_available=None
def request_stop(signum,frame):
    global stopping
    stopping=True
signal.signal(signal.SIGTERM,request_stop);signal.signal(signal.SIGINT,request_stop)
log=RotatingSink(base/(label+'.log'))
child_env=environment_for_login(base,label,cmd,os.environ)
p=subprocess.Popen(['/usr/sbin/chroot',str(base/'rootfs')]+cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,preexec_fn=before,env=child_env)
for secret_key in ('NAPCAT_QUICK_PASSWORD','NAPCAT_QUICK_PASSWORD_MD5'):child_env.pop(secret_key,None)
def pump():
    try:
        while True:
            block=os.read(p.stdout.fileno(),4096)
            if not block:break
            log.write(block)
    finally:log.close()
reader=threading.Thread(target=pump,daemon=True);reader.start()
(base/'lab-active.json').write_text(json.dumps({'pid':os.getpid(),'label':label}))
print('LAB_TEST_STARTED '+label+' '+str(os.getpid()),flush=True)
while p.poll() is None:
    elapsed=time.monotonic()-start
    available=next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    rss=int((group/'memory.usage_in_bytes').read_text())
    memstat=dict(line.split() for line in (group/'memory.stat').read_text().splitlines())
    samples.append({'elapsed':round(elapsed,1),'memory_bytes':rss,'rss_bytes':int(memstat.get('total_rss',memstat.get('rss',0))),'cache_bytes':int(memstat.get('total_cache',memstat.get('cache',0))),'host_available_kib':available})
    peak_rss=max(peak_rss,samples[-1]['rss_bytes']);min_available=available if min_available is None else min(min_available,available)
    if stopping or (timeout>0 and elapsed>timeout) or available<180*1024:
        reason='stopped' if stopping else ('timeout' if timeout>0 and elapsed>timeout else 'host-memory-low');os.killpg(p.pid,signal.SIGTERM);break
    time.sleep(1)
try:p.wait(timeout=5)
except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
# Only processes explicitly put into this dedicated test cgroup are cleaned up.
for pid in (group/'tasks').read_text().split():
    try:os.kill(int(pid),signal.SIGTERM)
    except ProcessLookupError:pass
reader.join(timeout=5)
result={'label':label,'command':cmd,'returncode':p.returncode,'reason':reason,'elapsed_seconds':round(time.monotonic()-start,1),'peak_memory_bytes':int((group/'memory.max_usage_in_bytes').read_text()),'peak_rss_bytes':peak_rss,'min_host_available_kib':min_available or 0,'samples':list(samples)}
(base/(label+'-metrics.json')).write_text(json.dumps(result))
print(json.dumps({k:v for k,v in result.items() if k not in ('samples','command')}),flush=True)
if label.startswith('qq'):
    print('Private QQ/NapCat startup log saved; tokens and login QR are not printed.',flush=True)
else:print((base/(label+'.log')).read_text(errors='replace')[-5000:],flush=True)
raise SystemExit(0 if reason=='completed' and p.returncode==0 else 1)

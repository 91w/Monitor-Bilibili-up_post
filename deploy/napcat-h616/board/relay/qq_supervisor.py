"""Supervise the existing bounded-memory QQ entry without growing metrics forever."""
import json,os,signal,subprocess,time
from pathlib import Path
BASE=Path('/overlay/napcat-lab');PY='/overlay/b-up-runtime/python/bin/python3'
stopping=False;child=None
def stop(sig,frame):
    global stopping
    stopping=True
    if child and child.poll() is None:child.terminate()
def enabled():
    try:return json.loads((BASE/'forward-config.json').read_text()).get('enabled') and not (BASE/'storage-hold.json').exists()
    except Exception:return False
def main():
    global child
    import fcntl
    os.umask(0o077);lock=(BASE/'qq-supervisor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not stopping:
        if not enabled():time.sleep(10);continue
        try:
            subprocess.run([PY,str(BASE/'setup_lab.py')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=40)
            account=str(json.loads((BASE/'forward-config.json').read_text())['self_id'])
            if not account.isdecimal():raise ValueError('Invalid account')
            cmd=[PY,str(BASE/'lab_exec.py'),'qq-live','0','/usr/sbin/runuser','-u','napcat','--','/usr/bin/env','HOME=/home/napcat','/usr/bin/dbus-run-session','--','/usr/bin/xvfb-run','-a','/opt/QQ/qq','--no-sandbox','--disable-gpu','-q',account]
            child=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            while child.poll() is None and not stopping:
                if not enabled():child.terminate();break
                time.sleep(5)
            try:child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                # Controller normally terminates only its dedicated QQ process group.
                subprocess.run([PY,str(BASE/'stop_test.py'),str(child.pid)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                child.kill();child.wait()
        except Exception:pass
        for _ in range(30):
            if stopping:break
            time.sleep(1)
if __name__=='__main__':main()

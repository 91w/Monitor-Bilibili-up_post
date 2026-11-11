import json,os,signal,sys,time
from pathlib import Path
base=Path('/overlay/napcat-lab');sys.path.insert(0,'/overlay/b-up/app')
from timeguard import require_trusted_time
require_trusted_time();os.umask(0o077)
p=base/'forward-config.json';cfg=json.loads(p.read_text())
if not (base/'forward-outbox.sqlite3').exists():cfg['start_epoch']=int(time.time())
cfg['enabled']=True;temp=p.with_suffix('.new');temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2));temp.chmod(0o600);temp.replace(p)
target=Path('/etc/inittab');old=target.read_text();backup=base/'inittab.before-qq-relay'
if not backup.exists():backup.write_text(old);backup.chmod(0o600)
lines=['null::respawn:/overlay/b-up-runtime/python/bin/python3 /overlay/napcat-lab/relay/relay_service.py',
       'null::respawn:/overlay/b-up-runtime/python/bin/python3 /overlay/napcat-lab/relay/qq_supervisor.py']
new=old.rstrip()+'\n'
for line in lines:
    if line not in new:new+=line+'\n'
temp=target.with_suffix('.relay-new');temp.write_text(new);temp.chmod(target.stat().st_mode&0o777);temp.replace(target);os.kill(1,signal.SIGHUP)
print('QQ forwarding enabled from current baseline; relay and QQ supervisor registered with init.')

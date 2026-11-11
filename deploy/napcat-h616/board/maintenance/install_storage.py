import os,signal
from pathlib import Path
base=Path('/overlay/napcat-lab');target=Path('/etc/inittab')
line='null::respawn:/overlay/b-up-runtime/python/bin/python3 /overlay/napcat-lab/storage_maintenance.py --apply --loop'
old=target.read_text();backup=base/'inittab.before-storage-maintenance'
if not backup.exists():backup.write_text(old);backup.chmod(0o600)
if line not in old:
    temp=target.with_name('inittab.storage-new')
    with temp.open('w') as f:
        f.write(old.rstrip()+'\n'+line+'\n');f.flush();os.fsync(f.fileno())
    temp.chmod(target.stat().st_mode&0o777);temp.replace(target)
    os.kill(1,signal.SIGHUP)
print('Storage maintenance registered with init; QQ itself was not enabled for autostart')

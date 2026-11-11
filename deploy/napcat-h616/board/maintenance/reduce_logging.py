import json,os
from pathlib import Path
base=Path('/overlay/napcat-lab');folder=base/'rootfs/opt/QQ/resources/app/napcat/config'
backup=base/'private-config-backups';backup.mkdir(exist_ok=True);backup.chmod(0o700)
changed=0
for p in folder.glob('napcat*.json'):
    try:cfg=json.loads(p.read_text())
    except (OSError,ValueError):continue
    original=p.stat();saved=backup/(p.name+'.before-log-limits')
    if not saved.exists():saved.write_bytes(p.read_bytes());saved.chmod(0o600)
    cfg.update(fileLog=False,consoleLog=True,consoleLogLevel='warn',fileLogLevel='warn')
    temp=p.with_suffix('.logging-new');temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
    temp.chmod(original.st_mode&0o777);os.chown(temp,original.st_uid,original.st_gid);temp.replace(p);changed+=1
print('Logging reduced for',changed,'configs; ordinary info/debug message output disabled on next QQ startup. No account details printed.')

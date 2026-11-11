import json,os,signal,sys
from pathlib import Path
base=Path('/overlay/napcat-lab');sys.path.insert(0,str(base));sys.path.insert(0,str(base/'relay'))
from native_history import atomic
from relay_service import ensure_time,api
ensure_time();os.umask(0o077)
state=json.loads((base/'native-history-state.json').read_text())
if state.get('stage')!='verified':raise SystemExit('Live database rebuild has not been verified; scheduling remains disabled')
cfg=json.loads((base/'forward-config.json').read_text())
if not api(cfg,'get_status',{}).get('online'):raise SystemExit('QQ must be online before scheduling')
previous=json.loads((base/'native-history-policy.json').read_text()) if (base/'native-history-policy.json').exists() else {}
policy={'enabled':previous.get('enabled',False),'trigger_mib':previous.get('trigger_mib',3072),'check_hour_beijing':3,'minimum_interval_days':7,'verified_backup_retention_hours':24}
atomic(base/'native-history-policy.json',policy)
target=Path('/etc/inittab');old=target.read_text();backup=base/'inittab.before-native-history'
if not backup.exists():backup.write_text(old);backup.chmod(0o600)
line='null::respawn:/overlay/b-up-runtime/python/bin/python3 /overlay/napcat-lab/native_history_service.py loop'
if line not in old:
    temp=target.with_suffix('.native-new');temp.write_text(old.rstrip()+'\n'+line+'\n');temp.chmod(target.stat().st_mode&0o777);temp.replace(target);os.kill(1,signal.SIGHUP)
print('Native maintenance worker registered; rotation enable state preserved (default off). Phone authorization may be required after rotation.')

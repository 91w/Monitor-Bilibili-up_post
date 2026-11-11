"""Local operational control; prints no credentials or message bodies."""
import json,os,sys,time
from pathlib import Path
from queue_store import Outbox
from relay_service import BASE,DB,api
def main():
    os.umask(0o077);command=sys.argv[1] if len(sys.argv)>1 else 'status'
    p=BASE/'forward-config.json';cfg=json.loads(p.read_text())
    if command in ('start','stop'):
        if command=='start' and (BASE/'storage-hold.json').exists():raise SystemExit('Storage hold active; inspect storage-health.json')
        cfg['enabled']=command=='start';temp=p.with_suffix('.new');temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2));temp.chmod(0o600);temp.replace(p)
        print('Forwarding enabled; QQ supervisor will start it.' if cfg['enabled'] else 'Forwarding paused; QQ supervisor will stop it. Pending queue preserved.')
    elif command=='status':
        result={'enabled':bool(cfg.get('enabled')),'storage_hold':(BASE/'storage-hold.json').exists()}
        try:
            status=api(cfg,'get_status',{});result['qq_online']=status.get('online');result['qq_good']=status.get('good')
        except Exception:result['qq_online']=False;result['qq_status']='login_or_api_unavailable'
        if DB.exists():
            box=Outbox(DB)
            try:
                result['outbox']=box.status()
                result['history']={str(g):box.get('history_status:'+str(g)) for g in cfg['allowed_group_ids']}
                result['history_error']=box.get('history_error')
                result['retrying']=box.db.execute('SELECT COUNT(*) FROM messages WHERE sent IS NULL AND attempts>0').fetchone()[0]
            finally:box.db.close()
        print(json.dumps(result,ensure_ascii=False))
    else:raise SystemExit('Usage: relay_ctl.py status|start|stop')
if __name__=='__main__':main()

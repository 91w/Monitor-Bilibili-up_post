"""Configure only the private local OneBot transports. Do not print credentials."""
import json,os,secrets,signal,sys,time
from pathlib import Path
BASE=Path('/overlay/napcat-lab');PY='/overlay/b-up-runtime/python/bin/python3'
sys.path.insert(0,'/overlay/b-up/app')
from timeguard import require_trusted_time
require_trusted_time();os.umask(0o077)
source=BASE/'forward-config.incoming.json'
cfg=json.loads(source.read_text())
if not cfg.get('feishu_webhook'):raise SystemExit('Private QQ webhook missing')
folder=BASE/'rootfs/opt/QQ/resources/app/napcat/config'
files=list(folder.glob('onebot11_*.json'))
if len(files)!=1:raise SystemExit('Expected exactly one QQ OneBot account configuration')
p=files[0];uin=p.stem.removeprefix('onebot11_')
if not uin.isdecimal():raise SystemExit('Unexpected account config name')
oldpath=BASE/'forward-config.json';old=json.loads(oldpath.read_text()) if oldpath.exists() else {}
cfg.update(enabled=False,self_id=uin,start_epoch=old.get('start_epoch',int(time.time())),event_token=old.get('event_token') or secrets.token_urlsafe(32),api_token=old.get('api_token') or secrets.token_urlsafe(32))
def atomic(p,data,owner=None):
    temp=p.with_suffix('.new');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2));temp.chmod(0o600)
    if owner:os.chown(temp,owner.st_uid,owner.st_gid)
    temp.replace(p)
onebot=json.loads(p.read_text());backup=BASE/'private-config-backups'/('onebot-before-relay.json')
if not backup.exists():backup.write_bytes(p.read_bytes());backup.chmod(0o600)
network=onebot.setdefault('network',{})
for key in ('httpServers','httpClients','websocketServers','websocketClients','httpSseServers','plugins'):network.setdefault(key,[])
network['httpServers']=[x for x in network['httpServers'] if x.get('name')!='qq-forward-api']+[
    {'name':'qq-forward-api','enable':True,'host':'127.0.0.1','port':6101,'enableCors':False,'enableWebsocket':False,'messagePostFormat':'array','token':cfg['api_token'],'debug':False}]
network['httpClients']=[x for x in network['httpClients'] if x.get('name')!='qq-forward-events']+[
    {'name':'qq-forward-events','enable':True,'url':'http://127.0.0.1:6102/events','messagePostFormat':'array','reportSelfMessage':False,'token':cfg['event_token'],'debug':False}]
atomic(p,onebot,p.stat());atomic(oldpath,cfg);source.unlink()
print('Private OneBot loopback transport configured; forwarding remains disabled pending delivery verification.')

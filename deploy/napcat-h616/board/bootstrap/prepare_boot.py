import json,os,secrets
from pathlib import Path
p=Path('/overlay/napcat-lab/rootfs/opt/QQ/resources/app/napcat/config/webui.json')
cfg=json.loads(p.read_text()) if p.exists() else {}
cfg.update(host='127.0.0.1',port=6099)
cfg.setdefault('token',secrets.token_urlsafe(32));cfg.setdefault('loginRate',3)
p.write_text(json.dumps(cfg));p.chmod(0o600);os.chown(p,1000,1000)
print('WebUI limited to loopback; authentication key saved privately, not printed')

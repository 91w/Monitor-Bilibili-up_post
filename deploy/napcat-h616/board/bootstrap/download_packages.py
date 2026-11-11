import hashlib,json,time
from pathlib import Path
import requests
base=Path('/overlay/napcat-lab');s=requests.Session();s.trust_env=False
jobs=[('QQ.deb','https://qqdl.gtimg.cn/qqfile/QQNT/9.9.36/beta/9ee04bef/linuxqq_3.2.34-53644_arm64.deb',600*1024*1024),('NapCat.Shell.zip','https://github.com/NapNeko/NapCatQQ/releases/download/v4.18.42/NapCat.Shell.zip',64*1024*1024)]
for name,url,cap in jobs:
    p=base/name
    if p.exists():print('Already downloaded:',name,flush=True);continue
    print('Downloading:',name,flush=True)
    r=s.get(url,stream=True,timeout=(15,30));r.raise_for_status();size=0;started=time.monotonic();temp=p.with_suffix('.partial')
    digest=hashlib.sha256()
    with temp.open('wb') as f:
        for data in r.iter_content(262144):
            size+=len(data)
            if size>cap or time.monotonic()-started>600:raise RuntimeError('Download cap exceeded')
            f.write(data);digest.update(data)
    temp.replace(p)
    (base/(name+'.manifest.json')).write_text(json.dumps({'url':url,'size':size,'sha256':digest.hexdigest()}))
    print('Downloaded:',name,size,'SHA256',digest.hexdigest(),flush=True)

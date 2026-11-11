import hashlib,json,os,tarfile,time
from pathlib import Path
import requests
os.umask(0o077)
base=Path('/overlay/napcat-lab');base.mkdir(exist_ok=True)
name='ubuntu-base-22.04.5-base-arm64.tar.gz'
prefix='https://cdimage.ubuntu.com/ubuntu-base/releases/22.04/release/'
s=requests.Session();s.trust_env=False
sums=s.get(prefix+'SHA256SUMS',timeout=(10,25));sums.raise_for_status()
expected=next(line.split()[0] for line in sums.text.splitlines() if line.split()[-1].lstrip('*')==name)
archive=base/name
if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:
    r=s.get(prefix+name,stream=True,timeout=(10,30));r.raise_for_status()
    temp=archive.with_suffix('.partial');size=0;started=time.monotonic()
    with temp.open('wb') as f:
        for part in r.iter_content(262144):
            size+=len(part)
            if size>64*1024*1024 or time.monotonic()-started>300:raise RuntimeError('Download limit exceeded')
            f.write(part)
    if hashlib.sha256(temp.read_bytes()).hexdigest()!=expected:raise RuntimeError('SHA256 mismatch')
    temp.replace(archive)
print('Official Ubuntu base SHA256 verified:',expected,flush=True)
root=base/'rootfs';root.mkdir(exist_ok=True)
skipped=[]
with tarfile.open(archive,'r:gz') as tar:
    for member in tar:
        try:
            filtered=tarfile.data_filter(member,root)
        except tarfile.FilterError:
            skipped.append(member.name);continue
        if filtered is not None:tar.extract(filtered,root,filter='data')
(base/'root-manifest.json').write_text(json.dumps({'url':prefix+name,'sha256':expected,'skipped':skipped}))
print('Extracted independent rootfs; skipped special or escaping links:',len(skipped),flush=True)

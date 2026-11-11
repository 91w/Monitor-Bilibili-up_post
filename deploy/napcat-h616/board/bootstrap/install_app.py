import hashlib,json,os,shutil,subprocess,zipfile
from pathlib import Path
os.umask(0o077)
base=Path('/overlay/napcat-lab');root=base/'rootfs';shell=base/'NapCat.Shell.zip'
expected='02e5ff0f79916c1e04c81c1b4590056406fb4293f1f1300c955e71fd08b2829d'
if hashlib.sha256(shell.read_bytes()).hexdigest()!=expected:raise SystemExit('NapCat release hash mismatch')
deb=root/'var/tmp/QQ.deb'
if not deb.exists():os.link(base/'QQ.deb',deb)
subprocess.run(['/usr/sbin/chroot',str(root),'/usr/bin/dpkg-deb','-f','/var/tmp/QQ.deb','Architecture','Version','Depends'],check=True)
subprocess.run(['/usr/sbin/chroot',str(root),'/usr/bin/dpkg-deb','-x','/var/tmp/QQ.deb','/'],check=True)
app=root/'opt/QQ/resources/app';target=app/'napcat';target.mkdir(exist_ok=True)
with zipfile.ZipFile(shell) as z:
    for member in z.infolist():
        destination=(target/member.filename).resolve()
        if not destination.is_relative_to(target.resolve()):raise RuntimeError('Unsafe ZIP entry')
        z.extract(member,target)
        mode=member.external_attr>>16
        if mode:os.chmod(destination,mode&0o777)
if not (target/'napcat.mjs').exists():raise SystemExit('NapCat entry not found')
package=app/'package.json';backup=app/'package.napcat-original.json'
if not backup.exists():shutil.copyfile(package,backup)
(app/'loadNapCat.cjs').write_text("const path=require('node:path');const{pathToFileURL}=require('node:url');import(pathToFileURL(path.join(__dirname,'napcat/napcat.mjs')).href);\n")
data=json.loads(package.read_text());data['main']='./loadNapCat.cjs';package.write_text(json.dumps(data))
subprocess.run(['/usr/sbin/chroot',str(root),'/bin/sh','-c','id -u napcat >/dev/null 2>&1 || useradd -m -u 1000 -s /bin/bash napcat'],check=True)
subprocess.run(['/usr/sbin/chroot',str(root),'/bin/chown','-R','1000:1000','/opt/QQ/resources/app/napcat','/home/napcat'],check=True)
print('QQ and NapCat placed only inside lab rootfs; no QQ account configured')

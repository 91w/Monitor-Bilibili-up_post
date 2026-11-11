import os,stat,subprocess
from pathlib import Path
base=Path('/overlay/napcat-lab');root=base/'rootfs'
if not (root/'usr/bin/apt-get').exists():raise SystemExit('Rootfs not ready')
for name in ('proc','dev','dev/shm','sys','tmp'): (root/name).mkdir(parents=True,exist_ok=True)
for name,minor in [('null',3),('zero',5),('random',8),('urandom',9)]:
    p=root/'dev'/name
    if not p.exists():os.mknod(p,stat.S_IFCHR|0o666,os.makedev(1,minor))
os.chmod(root/'tmp',0o1777)
resolv=root/'etc/resolv.conf'
if resolv.is_symlink():resolv.unlink()
resolv.write_text(Path('/etc/resolv.conf').read_text())
(root/'etc/hosts').write_text('127.0.0.1 localhost napcat-lab\n::1 localhost\n')
policy=root/'usr/sbin/policy-rc.d';policy.write_text('#!/bin/sh\nexit 101\n');policy.chmod(0o755)
mounted=Path('/proc/mounts').read_text()
if str(root/'proc') not in mounted:subprocess.run(['mount','-t','proc','-o','nosuid,nodev,noexec','proc',str(root/'proc')],check=True)
if str(root/'dev/shm') not in mounted:subprocess.run(['mount','-t','tmpfs','-o','size=64m,nosuid,nodev','tmpfs',str(root/'dev/shm')],check=True)
if str(root/'sys') not in mounted:
    subprocess.run(['mount','-o','bind','/sys',str(root/'sys')],check=True)
    subprocess.run(['mount','-o','remount,bind,ro',str(root/'sys')],check=True)
cg=base/'cgroup';cg.mkdir(exist_ok=True)
if str(cg) not in mounted:subprocess.run(['mount','-t','cgroup','-o','memory','memory',str(cg)],check=True)
group=cg/'napcat-test';group.mkdir(exist_ok=True)
(group/'memory.limit_in_bytes').write_text(str(512*1024*1024))
memsw=group/'memory.memsw.limit_in_bytes'
if memsw.exists():memsw.write_text(str(512*1024*1024))
print('Private rootfs prepared; test-process cgroup RAM cap=512MiB; host libraries unchanged')

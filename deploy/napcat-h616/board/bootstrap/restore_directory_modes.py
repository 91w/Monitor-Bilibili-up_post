import os,tarfile
from pathlib import Path
base=Path('/overlay/napcat-lab');root=(base/'rootfs').resolve();count=0
# Python's safe tar filter deliberately omits directory modes; restore the
# verified distribution's traversal permissions inside this lab root only.
with tarfile.open(base/'ubuntu-base-22.04.5-base-arm64.tar.gz','r:gz') as tar:
    for member in tar:
        if not member.isdir():continue
        target=(root/member.name).resolve()
        if not target.is_relative_to(root) or not target.is_dir():continue
        os.chmod(target,member.mode&0o1777)
        os.chown(target,member.uid,member.gid)
        count+=1
print('Restored verified rootfs directory modes:',count)

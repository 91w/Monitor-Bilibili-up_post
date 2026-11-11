"""Small bounded binary stdout sink; runtime log data never goes to the console."""
import os
from pathlib import Path
class RotatingSink:
    def __init__(self,path,max_bytes=2*1024*1024,backups=3):
        self.path=Path(path);self.max_bytes=max_bytes;self.backups=backups
        if self.path.is_symlink():raise ValueError('Refusing symlink log')
        self.file=self.path.open('wb');os.chmod(self.path,0o600);self.size=0
    def write(self,data):
        while data:
            if self.size>=self.max_bytes:self.rotate()
            block=data[:self.max_bytes-self.size];data=data[len(block):]
            self.file.write(block);self.file.flush();self.size+=len(block)
    def rotate(self):
        self.file.close()
        for i in range(self.backups,0,-1):
            dst=self.path.with_name(self.path.name+'.'+str(i))
            if dst.is_symlink():raise ValueError('Refusing symlink rotation')
            if i==self.backups:
                if dst.exists():dst.unlink()
            src=self.path if i==1 else self.path.with_name(self.path.name+'.'+str(i-1))
            if src.is_symlink():raise ValueError('Refusing symlink rotation')
            if src.exists():src.replace(dst)
        self.file=self.path.open('wb');os.chmod(self.path,0o600);self.size=0
    def close(self):self.file.close()

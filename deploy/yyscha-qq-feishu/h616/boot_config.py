"""Add only the two project-owned init supervisors, preserving other services."""
import os
from pathlib import Path
import shutil
import signal


def configure(base=Path('/overlay/yyscha'), inittab=Path('/etc/inittab'), sbin=Path('/usr/sbin')):
    names = ('yyscha-service', 'yyscha-update-service')
    wrappers = {}
    for name in names:
        script = base / 'app' / name
        if not script.is_file():
            raise RuntimeError('Missing project supervisor')
        content = f'#!/bin/sh\nwhile [ ! -f {script.as_posix()} ]; do sleep 10; done\nexec sh {script.as_posix()}\n'
        target = sbin / name
        if target.exists() and target.read_text() != content:
            raise RuntimeError('Refusing to overwrite a different wrapper')
        wrappers[target] = content
    current = inittab.read_text()
    additions = [f'null::respawn:{(sbin / name).as_posix()}' for name in names
                 if f'null::respawn:{(sbin / name).as_posix()}' not in current.splitlines()]
    if not additions:
        return False
    temp = inittab.with_name(inittab.name + '.yyscha-new')
    if temp.exists():
        raise RuntimeError('Pending boot edit found')
    backups = base / 'backups'
    backups.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        backups.chmod(0o700)
    backup = backups / 'inittab-before-yyscha-supervisors'
    if not backup.exists():
        shutil.copyfile(inittab, backup)
    for path, content in wrappers.items():
        if not path.exists():
            with path.open('x') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != 'nt':
                path.chmod(0o755)
    with temp.open('x') as handle:
        handle.write(current + ('' if current.endswith('\n') else '\n') + '\n'.join(additions) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != 'nt':
        temp.chmod(0o644)
    os.replace(temp, inittab)
    return True


if __name__ == '__main__':
    os.umask(0o077)
    try:
        if configure():
            os.sync()
            os.kill(1, signal.SIGHUP)
        print('Project boot supervisors configured; other service entries preserved.')
    except Exception:
        print('Boot configuration not completed; inspect project backups before retrying.')
        raise SystemExit(1)

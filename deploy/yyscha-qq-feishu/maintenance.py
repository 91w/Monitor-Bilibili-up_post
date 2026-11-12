"""Private consistent snapshots and credential-free deployment preflight."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import struct
import sys
import zipfile


SOURCE_FILES = ('*.py', '*.md', 'config.example.json', 'LICENSE', '.gitignore', 'h616/*', 'vendor/**/*', 'cloud/*')


def sender_active(database):
    lock = Path(str(database) + '.lock')
    if not lock.exists():
        return False
    db = sqlite3.connect(f'{lock.resolve().as_uri()}?mode=rw', uri=True, timeout=0)
    try:
        db.execute('BEGIN EXCLUSIVE')
        db.rollback()
        return False
    except sqlite3.OperationalError:
        return True
    finally:
        db.close()


def backup_database(source, destination):
    with closing(sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Snapshot integrity check failed')


def freeze(source, private, destination):
    # Contains secrets and message content: destination must remain outside outputs.
    destination = destination.resolve()
    if 'outputs' in {part.lower() for part in destination.parts}:
        raise ValueError('Private snapshots must not be saved in outputs')
    destination.mkdir(parents=True, exist_ok=False)
    if os.name != 'nt':
        destination.chmod(0o700)
    files = sorted({p for pattern in SOURCE_FILES for p in source.glob(pattern)
                    if p.is_file() and p.name != 'config.json' and '__pycache__' not in p.parts})
    with zipfile.ZipFile(destination / 'source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(source))
    config = private / 'config.json'
    shutil.copyfile(config, destination / 'config.json')
    for name in ('state.sqlite3', 'site-session.sqlite3'):
        path = private / name
        if path.exists():
            backup_database(path, destination / name)
    counts = {}
    if (destination / 'state.sqlite3').exists():
        with closing(sqlite3.connect(destination / 'state.sqlite3')) as db:
            counts = dict(db.execute('SELECT status,count(*) FROM messages GROUP BY status'))
    report = {'created_utc': datetime.now(timezone.utc).isoformat(),
              'sender_lock_active': sender_active(private / 'state.sqlite3'),
              'message_counts': counts,
              'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in destination.iterdir() if p.is_file()}}
    (destination / 'snapshot.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if os.name != 'nt':
        for path in destination.iterdir():
            path.chmod(0o600)
    return report


def preflight():
    result = {'python': sys.version.split()[0], 'user_bits': struct.calcsize('P') * 8,
              'tls': ssl.OPENSSL_VERSION, 'sqlite': sqlite3.sqlite_version,
              'cryptography': bool(importlib.util.find_spec('cryptography')),
              'pynacl': bool(importlib.util.find_spec('nacl'))}
    if os.name != 'nt':
        for mount in ('/', '/overlay'):
            result['free_bytes_' + mount] = shutil.disk_usage(mount).free
        result['trusted_time'] = Path('/var/run/b-up-time-ready').exists()
        result['bup_enabled'] = Path('/overlay/b-up/data/enabled').exists()
        result['yyscha_enabled'] = Path('/overlay/yyscha/data/enabled').exists()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('freeze', 'preflight'))
    parser.add_argument('--private', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    if args.command == 'freeze':
        if not args.private or not args.destination:
            parser.error('freeze requires --private and --destination')
        report = freeze(Path(__file__).parent, args.private, args.destination)
    else:
        report = preflight()
    print(json.dumps(report, ensure_ascii=True, indent=2))

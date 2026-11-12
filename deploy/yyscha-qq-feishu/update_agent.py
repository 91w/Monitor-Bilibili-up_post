"""Headless outbound-only update agent and desktop outbox commands."""
import argparse
from contextlib import closing
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import random
import sqlite3
import time

from cos_transport import CloudError, PRIVATE
from relay_updates import DesktopUpdates, BoardUpdates, enqueue_current


def waiting_for_auth(private):
    path = Path(private) / 'state.sqlite3'
    if not path.exists():
        return False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute("SELECT value FROM meta WHERE key='auth_required'").fetchone()
        return bool(row and row[0] == '1')
    except sqlite3.Error:
        return False


def run(private, config):
    os.umask(0o077)
    logs = private.parent / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / 'updates.log', maxBytes=512*1024, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    failures = 0
    receiver = BoardUpdates(private, config)
    try:
        while (private / 'enabled').exists():
            try:
                results = receiver.synchronize()
                if results:
                    logging.info('更新包结果：%s', results)
                failures = 0
                delay = (60 if waiting_for_auth(private) else 900) + random.uniform(0, 15)
            except Exception:
                failures += 1
                delay = min(3600, 60 * 2**min(failures - 1, 6)) + random.uniform(0, 10)
                logging.warning('会话更新检查未完成，保留状态并退避重试；未输出原始异常或凭据')
            deadline = time.monotonic() + delay
            while (private / 'enabled').exists() and time.monotonic() < deadline:
                time.sleep(min(5, max(0, deadline - time.monotonic())))
                if waiting_for_auth(private) and deadline - time.monotonic() > 75:
                    deadline = time.monotonic() + 60
    finally:
        receiver.close()


def main():
    ca = Path('/overlay/b-up-runtime/python/lib/python3.11/site-packages/certifi/cacert.pem')
    if os.name != 'nt' and ca.is_file():
        os.environ.setdefault('SSL_CERT_FILE', str(ca))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('board-once', 'board-run', 'upload', 'sync', 'status'))
    parser.add_argument('--private', type=Path, default=PRIVATE)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    config = args.config or args.private / 'config.json'
    if args.command == 'board-run':
        run(args.private, config)
        return
    if args.command == 'upload':
        print(json.dumps(enqueue_current(config)))
        return
    component = BoardUpdates(args.private, config) if args.command == 'board-once' else DesktopUpdates(args.private)
    try:
        result = (component.status() if args.command == 'status' else component.synchronize())
        print(json.dumps(result, ensure_ascii=True))
    finally:
        component.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        print('会话更新操作未完成；已保留本地状态，未输出原始异常或凭据。')
        raise SystemExit(1)

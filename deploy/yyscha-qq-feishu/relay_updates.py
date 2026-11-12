"""Durable encrypted outbox and crash-recoverable, verified board updates."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from cos_transport import CosClient, CloudError, load_credentials, check_time, DEVICE
from device_pairing import load_pair
import secure_package as p


def database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS packages(id TEXT PRIMARY KEY,version INTEGER NOT NULL,
            digest TEXT NOT NULL,packet BLOB NOT NULL,state TEXT NOT NULL,receipt BLOB,
            created REAL NOT NULL);''')
    if 'acked' not in {row[1] for row in db.execute('PRAGMA table_info(packages)')}:
        db.execute('ALTER TABLE packages ADD COLUMN acked INTEGER NOT NULL DEFAULT 0')
        db.commit()
    return db


@contextmanager
def exclusive(path):
    lock = sqlite3.connect(str(path) + '.lock', timeout=0)
    try:
        lock.execute('CREATE TABLE IF NOT EXISTS guard(id INTEGER)')
        lock.execute('BEGIN EXCLUSIVE')
        yield
    except sqlite3.OperationalError:
        raise CloudError('同一设备的会话更新操作正在运行，请稍后重试') from None
    finally:
        lock.close()


class DesktopUpdates:
    def __init__(self, private, client=None):
        self.private = Path(private)
        self.identity, self.peer = load_pair(private, 'desktop')
        self.client = client or CosClient(load_credentials(self.private / 'cos-uploader.json'))
        self.path = self.private / 'relay-upload.sqlite3'
        self.db = database(self.path)

    def close(self):
        self.db.close()

    def enqueue(self, verified_cookie):
        # Caller must first validate the exact candidate against the website.
        check_time()
        with exclusive(self.path):
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row = self.db.execute("SELECT value FROM meta WHERE key='allocated_version'").fetchone()
                version = int(row[0]) + 1 if row else 1
                packet = p.seal(verified_cookie, DEVICE, version, p.unb64(self.peer['decrypt_public']),
                                p.unb64(self.identity['sender_secret']))
                header = p.parse(packet)['header']
                self.db.execute('INSERT INTO packages(id,version,digest,packet,state,receipt,created) VALUES(?,?,?,?,?,?,?)',
                    (header['package_id'], version, hashlib.sha256(packet).hexdigest(), packet, 'queued', None, time.time()))
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('allocated_version',?)", (str(version),))
                self.db.commit()
                return header['package_id']
            except Exception:
                self.db.rollback()
                raise

    def synchronize(self):
        check_time()
        with exclusive(self.path):
            rows = self.db.execute("SELECT id,packet,state FROM packages WHERE state IN ('queued','uploaded') ORDER BY version DESC LIMIT 20").fetchall()
            for package_id, packet, state in rows:
                header = p.parse(packet)['header']
                # A lost upload acknowledgement is retried with identical ID/bytes.
                if state == 'queued' and header['expires'] > time.time():
                    self.client.request('PUT', self.client.object_key('inbox', package_id), data=packet)
                    with self.db:
                        self.db.execute("UPDATE packages SET state='uploaded' WHERE id=?", (package_id,))
                try:
                    signed = self.client.request('GET', self.client.object_key('receipts', package_id))
                except CloudError as exc:
                    if exc.status == 404 and exc.code == 'NoSuchKey':
                        if header['expires'] <= time.time():
                            with self.db:
                                self.db.execute("UPDATE packages SET state='expired' WHERE id=?", (package_id,))
                        continue
                    raise
                result = p.verify_receipt(signed, header, packet, p.unb64(self.peer['receipt_public']))
                with self.db:
                    self.db.execute('UPDATE packages SET state=?,receipt=? WHERE id=?', (result, signed, package_id))
        return self.status()

    def status(self):
        row = self.db.execute('SELECT id,version,state FROM packages ORDER BY version DESC LIMIT 1').fetchone()
        return dict(package_id=row[0], version=row[1], state=row[2]) if row else {'state': 'empty'}


def enqueue_current(config_path):
    import relay as r
    from auth_recovery import verify_session
    from site_session import get_session, SiteSession
    config_path = Path(config_path)
    config = r.load_config(config_path)
    live = get_session(config)
    raw = live.cookie_header()
    candidate = SiteSession(raw)
    updates = None
    try:
        verify_session(config, candidate, raw)
        updates = DesktopUpdates(config_path.parent)
        package_id = updates.enqueue(candidate.cookie_header())
        try:
            state = updates.synchronize()
        except CloudError:
            state = updates.status()
            state['query_pending'] = True
        return state
    finally:
        candidate.close()
        live.close()
        if updates:
            updates.close()


class BoardUpdates:
    def __init__(self, private, config_path=None, client=None):
        self.private = Path(private)
        self.identity, self.peer = load_pair(private, 'board')
        self.config_path = Path(config_path) if config_path else self.private / 'config.json'
        self.client = client or CosClient(load_credentials(self.private / 'cos-receiver.json'))
        self.path = self.private / 'relay-receiver.sqlite3'
        self.db = database(self.path)

    def close(self):
        self.db.close()

    def applied_version(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='applied_version'").fetchone()
        return int(row[0]) if row else 0

    def _finish(self, header, packet, state):
        signed = p.receipt(header, packet, state, p.unb64(self.identity['receipt_secret']))
        with self.db:
            self.db.execute('UPDATE packages SET state=?,receipt=? WHERE id=?', (state, signed, header['package_id']))
            if state == 'applied':
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('applied_version',?)", (str(header['version']),))
        return signed

    def _verify(self, cookie):
        import relay as r
        from auth_recovery import verify_session
        from site_session import SiteSession
        config = r.load_config(self.config_path, allow_missing_cookie=True)
        session = SiteSession(cookie)
        try:
            verify_session(config, session, cookie)
            return session
        except Exception:
            session.close()
            raise

    def _promote(self, session, marker):
        from auth_recovery import promote_session
        promote_session(self.config_path, session, relay_marker=marker)

    def handle(self, packet):
        check_time()
        envelope = p.authenticate(packet, DEVICE, p.unb64(self.peer['sender_public']))
        header = envelope['header']
        digest = hashlib.sha256(packet).hexdigest()
        marker = {'package_id': header['package_id'], 'version': header['version'], 'sha256': digest}
        with exclusive(self.path):
            row = self.db.execute('SELECT digest,state,receipt FROM packages WHERE id=?', (header['package_id'],)).fetchone()
            if row and row[0] != digest:
                raise CloudError('同一包ID出现不同内容，已拒绝')
            if row and row[1] in ('applied', 'rejected'):
                return row[2]
            if not row:
                with self.db:
                    self.db.execute('INSERT INTO packages(id,version,digest,packet,state,receipt,created) VALUES(?,?,?,?,?,?,?)',
                        (header['package_id'], header['version'], digest, packet, 'received', None, time.time()))
            # Config replacement and this marker share one fsynced atomic file.
            # If power was lost before the ledger commit, finish without applying
            # the session a second time, even when the transmission later expires.
            current = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
            if row and row[1] == 'promoting' and current.get('relay_update') == marker:
                if header['version'] >= self.applied_version():
                    return self._finish(header, packet, 'applied')
            try:
                _, cookie = p.open_package(packet, DEVICE, p.unb64(self.identity['decrypt_public']),
                    p.unb64(self.identity['decrypt_secret']), p.unb64(self.peer['sender_public']), self.applied_version())
            except p.PackageError:
                return self._finish(header, packet, 'rejected')
            import relay as r
            try:
                session = self._verify(cookie)
            except r.AuthRequired:
                return self._finish(header, packet, 'rejected')
            # Network failures and unclassified business failures are retried;
            # they never discard the previous session or advance applied_version.
            try:
                with self.db:
                    self.db.execute("UPDATE packages SET state='promoting' WHERE id=?", (header['package_id'],))
                self._promote(session, marker)
            finally:
                session.close()
            return self._finish(header, packet, 'applied')

    def synchronize(self):
        check_time()
        # Complete interrupted local promotions before accepting newer versions.
        for packet, in self.db.execute("SELECT packet FROM packages WHERE state='promoting' ORDER BY version DESC").fetchall():
            self.handle(packet)
        self.flush_receipts()
        packets = []
        marker = ''
        for _ in range(10):
            entries, marker = self.client.list_inbox(marker)
            for entry in entries:
                packet = self.client.request('GET', entry['key'])
                try:
                    header = p.authenticate(packet, DEVICE, p.unb64(self.peer['sender_public']))['header']
                    if entry['key'] != self.client.object_key('inbox', header['package_id']):
                        continue
                    packets.append((header['version'], entry['key'], packet))
                except p.PackageError:
                    continue  # Never acknowledge or apply unauthenticated cloud content.
            if not marker:
                break
        results = []
        for version, key, packet in sorted(packets, key=lambda item: item[0], reverse=True):
            signed = self.handle(packet)
            header = p.parse(packet)['header']
            self.client.request('PUT', self.client.object_key('receipts', header['package_id']), data=signed)
            # Both applied and rejected outcomes are durable before cleanup.
            self.client.request('DELETE', key)
            with self.db:
                self.db.execute('UPDATE packages SET acked=1 WHERE id=?', (header['package_id'],))
            results.append({'version': version, 'state': p.parse(signed)['body']['status']})
        return results

    def flush_receipts(self):
        # Receipt recovery does not depend on the original cloud object surviving
        # a timeout, a reboot or the cloud lifecycle cleanup.
        for package_id, signed in self.db.execute("SELECT id,receipt FROM packages WHERE receipt IS NOT NULL AND acked=0 LIMIT 100").fetchall():
            self.client.request('PUT', self.client.object_key('receipts', package_id), data=signed)
            self.client.request('DELETE', self.client.object_key('inbox', package_id))
            with self.db:
                self.db.execute('UPDATE packages SET acked=1 WHERE id=?', (package_id,))

"""Website fingerprint polling; queue processing remains independent of changes."""
import hashlib
import json
import math
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import relay as r

STATE = 'notification_poll_state'
STATUS = 'notification_poll_status'


def number(schedule, name, default, minimum, maximum):
    try:
        value = float(schedule.get(name, default))
        return value if math.isfinite(value) and minimum <= value <= maximum else default
    except (TypeError, ValueError):
        return default


def delay_seconds(schedule, now):
    """Match alert.js foreground timing, using Shanghai time and server schedule."""
    schedule = schedule if isinstance(schedule, dict) else {}
    now = now.astimezone(r.TZ)
    def minutes(key, default):
        try:
            h, m = map(int, schedule.get(key, default).split(':'))
            if not 0 <= h < 24 or not 0 <= m < 60:
                raise ValueError
            return h * 60 + m
        except (ValueError, TypeError, AttributeError):
            h, m = map(int, default.split(':'))
            return h * 60 + m
    start, end = minutes('start', '09:00'), minutes('end', '16:30')
    current = now.hour * 60 + now.minute
    if now.weekday() < 5 and start <= current <= end + 6:
        interval = number(schedule, 'interval_minutes', 10, 1, 1440)
        slot = start + math.floor((current - start) / interval) * interval
        elapsed = current - slot + now.second / 60
        grace = number(schedule, 'collect_grace_minutes', 1.5, 0, 60)
        window = number(schedule, 'dense_window_minutes', 3, 0, 60)
        if slot <= end and grace <= elapsed <= grace + window:
            return max(15, number(schedule, 'workday_dense_sec', 25, 1, 86400))
        return max(60, number(schedule, 'workday_sparse_sec', 180, 1, 86400))
    return max(120, number(schedule, 'off_hours_sec', 240, 1, 86400))


def next_delay_seconds(schedule, now):
    delay = delay_seconds(schedule, now)
    schedule = schedule if isinstance(schedule, dict) else {}
    local = now.astimezone(r.TZ)
    if local.weekday() >= 5:
        return delay
    try:
        start_h, start_m = map(int, schedule.get('start', '09:00').split(':'))
        end_h, end_m = map(int, schedule.get('end', '16:30').split(':'))
        if not (0 <= start_h < 24 and 0 <= end_h < 24 and 0 <= start_m < 60 and 0 <= end_m < 60):
            return delay
    except (ValueError, TypeError, AttributeError):
        return delay
    start, end = start_h * 60 + start_m, end_h * 60 + end_m
    interval = number(schedule, 'interval_minutes', 10, 1, 1440)
    grace = number(schedule, 'collect_grace_minutes', 1.5, 0, 60)
    current = local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1e6
    for index in range(max(0, int((end - start) / interval) + 1)):
        boundary = (start + index * interval + grace) * 60
        remaining = boundary - current
        if remaining > 0:
            # Never sleep through a dense-window opening; keep a 15s minimum.
            return min(delay, max(15, remaining))
    return delay


class ServicesDisabled(r.Failure):
    pass


def probe(config, fingerprint):
    from site_session import get_session
    r.ensure_time(config)
    url = r.SITE + '/application/QQ/api.php?' + urlencode({'action': 'alert_probe', 'fp': fingerprint})
    try:
        with get_session(config).open(url, timeout=20) as response:
            raw = response.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise r.Failure('轻量检测响应过大，暂缓源站请求')
        data = json.loads(raw)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise r.AuthRequired('轻量检测登录失效，请打开登录恢复入口') from None
        if exc.code == 503:
            try:
                body = json.loads(exc.read(64 * 1024 + 1))
                if isinstance(body, dict) and body.get('services_disabled') is True:
                    raise ServicesDisabled('网站已关闭相关服务，暂停读取并稍后复查')
            except (ValueError, OSError):
                pass
        if exc.code in (429, 503):
            raise r.RateLimited(r.retry_delay(exc.headers.get('Retry-After'))) from None
        raise r.Failure('轻量检测HTTP失败，稍后重试') from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise r.Failure('轻量检测网络或响应异常，稍后重试') from None
    if not isinstance(data, dict):
        raise r.Failure('轻量检测格式异常，稍后重试')
    if data.get('services_disabled') is True:
        raise ServicesDisabled('网站已关闭相关服务，暂停读取并稍后复查')
    if data.get('ok') is not True or not isinstance(data.get('fp'), str) or not 0 < len(data['fp']) <= 256:
        if data.get('code') in (401, 403):
            raise r.AuthRequired('轻量检测登录失效，请打开登录恢复入口')
        raise r.Failure('轻量检测业务未通过，稍后重试')
    return {'fp': data['fp'], 'schedule': data.get('schedule') if isinstance(data.get('schedule'), dict) else {}}


class NotificationPolling:
    def __init__(self):
        self.next_probe = 0
        self.failures = 0
        self.allow_delivery = True
        self.first = True
        self.context = None
        self.read_pending = False

    def tick(self, db, source, config, now=None, probe_fn=None, poll_fn=None):
        now = time.time() if now is None else now
        local = datetime.fromtimestamp(now, r.TZ)
        backoff = db.execute("SELECT value FROM meta WHERE key='site_backoff_until'").fetchone()
        if backoff and float(backoff[0]) > now:
            self.allow_delivery = False
            self.next_probe = float(backoff[0])
            return False
        context = hashlib.sha256(json.dumps([config.get('site_cookie'), config['uids']], sort_keys=True).encode()).hexdigest()
        context_changed = self.context is not None and self.context != context
        self.read_pending = self.read_pending or context_changed
        if now < self.next_probe and not context_changed:
            return False
        row = db.execute('SELECT value FROM meta WHERE key=?', (STATE,)).fetchone()
        try:
            state = json.loads(row[0]) if row else {}
        except (ValueError, TypeError):
            state = {}
        if not isinstance(state, dict): state = {}
        self.allow_delivery = False
        self.context = context
        outcome, delay = 'error', 60
        try:
            candidate = (probe_fn or probe)(config, str(state.get('fp', '')))
            delay = next_delay_seconds(candidate['schedule'], local)
            auth = db.execute("SELECT value FROM meta WHERE key='auth_required'").fetchone()
            must_read = (self.first or self.read_pending or state.get('day') != local.date().isoformat()
                         or now - float(state.get('scanned_at', 0)) >= 3600
                         or state.get('fp') != candidate['fp'] or (auth and auth[0] == '1'))
            if must_read:
                (poll_fn or r.poll)(db, source, config, local)
                state = {'fp': candidate['fp'], 'day': local.date().isoformat(), 'scanned_at': now}
                # Commit the fingerprint only after all monitored message scans succeed.
                with db:
                    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (STATE, json.dumps(state)))
                self.read_pending = False
                outcome = 'messages_checked'
            else:
                outcome = 'unchanged'
            self.first, self.context, self.failures = False, context, 0
            self.allow_delivery = True
            return bool(must_read)
        except ServicesDisabled:
            outcome, delay = 'services_disabled', 1800
            raise
        except r.TimeNotReady:
            outcome, delay = 'waiting_for_time', 15
            raise
        except r.RateLimited as exc:
            outcome, delay = 'rate_limited', exc.retry_after
            with db:
                db.execute("INSERT OR REPLACE INTO meta VALUES('site_backoff_until',?)", (str(now + delay),))
            raise
        except r.Failure:
            self.failures += 1
            delay = min(3600, 60 * 2 ** min(self.failures - 1, 6))
            raise
        finally:
            self.next_probe = now + delay
            status = {'mode': 'alert_probe', 'outcome': outcome, 'checked_at': now,
                      'next_probe_at': self.next_probe, 'next_delay_seconds': delay,
                      'last_message_check_at': state.get('scanned_at')}
            with db:
                db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (STATUS, json.dumps(status)))

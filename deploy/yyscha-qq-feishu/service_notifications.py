"""Notify once after successful startup checks; deduplicate within a boot."""
import json
import time
from datetime import datetime
from pathlib import Path


def notify_ready(db, config, now=None, boot_id=None, sender=None):
    import relay as r
    now = time.time() if now is None else now
    if boot_id is None:
        try:
            boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        except OSError:
            boot_id = 'desktop-session'
    row = db.execute("SELECT value FROM meta WHERE key='service_ready_notice'").fetchone()
    try:
        previous = json.loads(row[0]) if row else {}
    except ValueError:
        previous = {}
    if previous.get('boot_id') == boot_id and now - previous.get('sent_at', 0) < 600:
        return True
    attempted = db.execute("SELECT value FROM meta WHERE key='service_ready_attempt'").fetchone()
    attempt = json.loads(attempted[0]) if attempted else {}
    if attempt.get('boot_id') == boot_id and now - attempt.get('at', 0) < 60:
        return False
    reason = ('启动提醒首次启用' if not previous else
              '开发板重启后已恢复' if previous.get('boot_id') != boot_id else '转发服务已重新启动')
    stamp = datetime.fromtimestamp(now, r.TZ).strftime('%m-%d %H:%M:%S')
    mode = '网站轻量检测' if config.get('notification_polling') else '消息轮询'
    retry = db.execute("SELECT count(*) FROM messages WHERE status='retry'").fetchone()[0]
    body = f'开发板服务启动成功\n{reason}\n时间：{stamp}（北京时间）\n网络、可信时间及首次消息读取检查已通过。\n模式：{mode}\n待重试消息：{retry} 条'
    with db:
        db.execute("INSERT OR REPLACE INTO meta VALUES('service_ready_attempt',?)", (json.dumps({'boot_id': boot_id, 'at': now}),))
    (sender or r.feishu)(config, body)
    with db:
        db.execute("INSERT OR REPLACE INTO meta VALUES('service_ready_notice',?)", (json.dumps({'boot_id': boot_id, 'sent_at': now}),))
    return True

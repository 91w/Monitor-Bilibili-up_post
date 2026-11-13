"""Linux launcher: rotating logs and secret redaction for the existing watcher."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import os

BASE = Path("/overlay/b-up")
os.umask(0o077)
os.environ["TZ"] = "CST-8"
import time
time.tzset()

config = json.loads((BASE / "data/config.json").read_text(encoding="utf-8-sig"))
secrets = [str(config.get(k, "")) for k in ("bilibili_cookie", "feishu_webhook", "feishu_secret", "feishu_app_secret")]
# Also redact individual cookie values in exceptions if an upstream response includes them.
for item in str(config.get("bilibili_cookie", "")).split(";"):
    if "=" in item:
        value = item.split("=", 1)[1].strip()
        if len(value) >= 8:
            secrets.append(value)

class RedactingFormatter(logging.Formatter):
    def format(self, record):
        result = super().format(record)
        for value in secrets:
            if value:
                result = result.replace(value, "[REDACTED]")
        return result

handler = RotatingFileHandler(
    BASE / "logs/watcher.log", maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
)
handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
os.chdir(BASE / "data")
sys.path.insert(0, str(BASE / "app"))
import bilibili_feishu_watcher as watcher
from timeguard import require_trusted_time, TimeTrustError

def ensure_time():
    try:
        require_trusted_time()
    except TimeTrustError as exc:
        raise watcher.WatcherError(str(exc)) from exc

original_check_once = watcher.check_once
original_post_to_feishu = watcher.post_to_feishu

def guarded_check_once(*args, **kwargs):
    ensure_time()
    return original_check_once(*args, **kwargs)

def guarded_post_to_feishu(*args, **kwargs):
    # Re-check immediately before timestamp/signature generation and transmission.
    ensure_time()
    return original_post_to_feishu(*args, **kwargs)

watcher.check_once = guarded_check_once
watcher.post_to_feishu = guarded_post_to_feishu

from reliable import install
queue = install(watcher, BASE, ensure_time)

from startup_notice import install_startup_notice
startup_notifier = install_startup_notice(watcher, queue.db, ensure_time)

if __name__ == "__main__":
    try:
        raise SystemExit(watcher.main())
    except watcher.WatcherError as exc:
        logging.error("%s", exc)
        raise SystemExit(2)
    except Exception:
        logging.exception("Watcher exited unexpectedly")
        raise SystemExit(1)

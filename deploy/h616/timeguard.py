"""Require a recent NTP sync and agreement between wall clock and boot uptime."""
from pathlib import Path
import json
import math
import time

MARKER = Path("/var/run/b-up-time-ready")
MAX_AGE_SECONDS = 1800
MAX_CLOCK_SKEW_SECONDS = 5.0

class TimeTrustError(RuntimeError):
    pass

def boot_time():
    return time.clock_gettime(time.CLOCK_BOOTTIME)

def validate(marker, now, uptime):
    try:
        epoch = float(marker["epoch"])
        synced_uptime = float(marker["uptime"])
        stratum = int(marker["stratum"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise TimeTrustError("NTP sync record is invalid") from exc
    if not all(math.isfinite(v) for v in (epoch, synced_uptime, now, uptime)):
        raise TimeTrustError("NTP sync record has invalid numbers")
    if not 1 <= stratum <= 15 or epoch < 1767225600 or synced_uptime < 0:
        raise TimeTrustError("Clock has not been synchronized to a valid NTP source")
    age = uptime - synced_uptime
    if age < -1 or age > MAX_AGE_SECONDS:
        raise TimeTrustError("NTP sync is stale or belongs to a previous boot")
    skew = now - (epoch + age)
    if abs(skew) > MAX_CLOCK_SKEW_SECONDS:
        raise TimeTrustError("System clock changed unexpectedly; waiting for NTP")
    return {"trusted": True, "sync_age_seconds": round(age, 2),
            "clock_skew_seconds": round(skew, 3), "stratum": stratum}

def require_trusted_time():
    try:
        marker = json.loads(MARKER.read_text())
    except (OSError, ValueError) as exc:
        raise TimeTrustError("Waiting for NTP synchronization") from exc
    return validate(marker, time.time(), boot_time())

if __name__ == "__main__":
    try:
        print(json.dumps(require_trusted_time()))
    except TimeTrustError as exc:
        print(json.dumps({"trusted": False, "reason": str(exc)}))
        raise SystemExit(1)

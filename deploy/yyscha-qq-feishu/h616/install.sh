#!/bin/sh
# Run on the H616 only, from the extracted package root. Does not enable service.
set -eu
umask 077
BASE=/overlay/yyscha
PY=/overlay/b-up-runtime/python/bin/python3
[ -x "$PY" ] || { echo 'Existing b-up Python runtime not found'; exit 1; }
"$PY" -c 'import sys,ssl,sqlite3; assert sys.version_info >= (3,11)'
[ -s /overlay/b-up-runtime/python/lib/python3.11/site-packages/certifi/cacert.pem ] || { echo 'Trusted CA bundle missing; installation stopped'; exit 1; }
for f in relay.py feishu_transport.py timeguard.py media_feishu.py history_test.py site_session.py auth_recovery.py recovery_web.py cookie_import.py h616/runner.py h616/yyscha-service h616/yyschactl; do
    [ -f "$f" ] || { echo 'Run from extracted package root'; exit 1; }
done
if [ -e "$BASE/data/enabled" ]; then echo 'Stop the yyscha service before updating'; exit 1; fi
mkdir -p "$BASE/app" "$BASE/data" "$BASE/logs"
chmod 700 "$BASE/data" "$BASE/logs"
for f in relay.py feishu_transport.py timeguard.py media_feishu.py history_test.py site_session.py auth_recovery.py recovery_web.py cookie_import.py; do cp "$f" "$BASE/app/$f"; done
for f in runner.py yyscha-service yyschactl; do cp "h616/$f" "$BASE/app/$f"; done
chmod 700 "$BASE/app/yyscha-service" "$BASE/app/yyschactl"
echo 'Installed; no config/database overwritten, service remains disabled.'

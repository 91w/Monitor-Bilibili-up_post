#!/bin/sh
# Run on the H616 only, from the extracted package root. Does not enable service.
set -eu
umask 077
BASE=/overlay/yyscha
PY=/overlay/b-up-runtime/python/bin/python3
[ -x "$PY" ] || { echo 'Existing b-up Python runtime not found'; exit 1; }
"$PY" -c 'import sys,ssl,sqlite3; assert sys.version_info >= (3,11)'
[ -s /overlay/b-up-runtime/python/lib/python3.11/site-packages/certifi/cacert.pem ] || { echo 'Trusted CA bundle missing; installation stopped'; exit 1; }
for f in relay.py message_identity.py notification_polling.py service_notifications.py feishu_transport.py timeguard.py media_feishu.py file_feishu.py history_test.py site_session.py auth_recovery.py recovery_web.py cookie_import.py cos_transport.py crypto_runtime.py secure_package.py device_pairing.py relay_updates.py update_agent.py vendor/libnacl/libsodium.so h616/runner.py h616/yyscha-service h616/yyscha-update-service h616/yyschactl; do
    [ -f "$f" ] || { echo 'Run from extracted package root'; exit 1; }
done
if [ -e "$BASE/data/enabled" ]; then echo 'Stop the yyscha service before updating'; exit 1; fi
mkdir -p "$BASE/app" "$BASE/data" "$BASE/logs"
chmod 700 "$BASE/data" "$BASE/logs"
for f in relay.py message_identity.py notification_polling.py service_notifications.py feishu_transport.py timeguard.py media_feishu.py file_feishu.py history_test.py site_session.py auth_recovery.py recovery_web.py cookie_import.py cos_transport.py crypto_runtime.py secure_package.py device_pairing.py relay_updates.py update_agent.py; do cp "$f" "$BASE/app/$f"; done
mkdir -p "$BASE/app/vendor/libnacl"
cp vendor/libnacl/*.py vendor/libnacl/libsodium.so "$BASE/app/vendor/libnacl/"
for f in runner.py yyscha-service yyscha-update-service yyschactl install-boot.sh boot_config.py; do cp "h616/$f" "$BASE/app/$f"; done
chmod 600 "$BASE/app/"*.py "$BASE/app/vendor/libnacl/"*.py "$BASE/app/vendor/libnacl/libsodium.so"
chmod 700 "$BASE/app/yyscha-service" "$BASE/app/yyscha-update-service" "$BASE/app/yyschactl"
"$PY" -c "import sys; sys.path.insert(0,'$BASE/app'); import crypto_runtime; assert crypto_runtime.libnacl.sodium_version_string() == b'1.0.22'"
echo 'Installed; no config/database overwritten, service remains disabled.'

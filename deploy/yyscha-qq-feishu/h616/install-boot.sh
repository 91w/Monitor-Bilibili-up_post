#!/bin/sh
set -eu
umask 077
exec /overlay/b-up-runtime/python/bin/python3 /overlay/yyscha/app/boot_config.py

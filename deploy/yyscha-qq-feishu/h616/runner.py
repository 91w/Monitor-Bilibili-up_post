"""Adapted from Monitor-Bilibili-up_post deploy/h616/runner.py."""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

os.umask(0o077)
os.environ['TZ'] = 'CST-8'
import time
time.tzset()
BASE = Path('/overlay/yyscha')
sys.path.insert(0, str(BASE / 'app'))
import relay


def main():
    config = relay.load_config(BASE / 'data/config.json')
    if config.get('require_trusted_time') is not True:
        raise relay.Failure('H616配置必须开启require_trusted_time')
    secrets = [config.get(k, '') for k in ('site_cookie', 'feishu_webhook', 'feishu_secret', 'feishu_app_secret')]
    secrets += [x.split('=', 1)[1].strip() for x in config['site_cookie'].split(';') if '=' in x]

    class RedactingFormatter(logging.Formatter):
        def format(self, record):
            result = super().format(record)
            for value in secrets:
                if value:
                    result = result.replace(value, '[REDACTED]')
            return result

    (BASE / 'logs').mkdir(exist_ok=True)
    handler = RotatingFileHandler(BASE / 'logs/relay.log', maxBytes=1024*1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(RedactingFormatter('%(asctime)s %(levelname)s %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    relay.ensure_time(config)
    relay.main()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except relay.Failure as exc:
        logging.error('%s', exc)
        sys.exit(1)
    except Exception:
        logging.error('服务本地异常，等待守护重启；未输出原始异常和凭据')
        sys.exit(1)

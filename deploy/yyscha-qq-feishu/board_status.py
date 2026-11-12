"""USB maintenance status; never copies a board config, cookie or private key."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', action='store_true')
    args = parser.parse_args()
    adb = shutil.which('adb')
    if not adb:
        print('未找到USB维护工具；请连接开发板并使用已安装的ADB。外网会话同步仍可使用15入口。')
        return 1
    mode = 'session' if args.session else 'status'
    result = subprocess.run([adb, *(['-s', os.environ['YYSCHA_ADB_SERIAL']] if os.environ.get('YYSCHA_ADB_SERIAL') else []), 'shell', 'sh',
                             '/overlay/yyscha/app/yyschactl', mode],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    text = result.stdout.decode('utf-8', errors='replace').replace('\r', '')
    allowed = ('已启用持久会话', '含记住登录凭据', '服务器已知到期时间', '未提供有效期',
               'enabled=', 'watcher=', 'watcher_pid=', '等待重试', '检测模式：', '{')
    lines = [line for line in text.splitlines() if line.startswith(allowed)]
    if not lines:
        print('未取得开发板状态，请确认USB连接。此入口不显示电脑迁移前的旧队列。')
        return 1
    print('以下为USB读取的开发板状态：')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, subprocess.TimeoutExpired):
        print('USB状态读取失败；未输出凭据。')
        raise SystemExit(1)

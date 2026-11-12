"""Daily desktop launcher; Unicode UI lives in Python, outside CMD parsing."""
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
ACTIONS = {
    '1': ('登录、更新 Cookie 并加密同步开发板', '15-cookie-sync.cmd'),
    '2': ('查询或重试 Cookie 同步', '16-sync-status.cmd'),
    '3': ('查看开发板消息队列（需要 USB）', '17-board-status.cmd'),
    '4': ('查看开发板会话有效期（需要 USB）', '18-board-session.cmd'),
}


def check():
    targets = [ROOT / filename for _, filename in ACTIONS.values()]
    targets += [ROOT / 'DAILY-USAGE.md']
    targets += [ROOT / name for name in ('recovery_web.py', 'update_agent.py', 'board_status.py')]
    missing = [str(path) for path in targets if not path.is_file()]
    if missing:
        print('Missing menu targets:')
        for path in missing:
            print(path)
        return 1
    print('All daily menu targets exist. No action was launched.')
    return 0


def main():
    if '--check' in sys.argv[1:]:
        return check()
    while True:
        print('\n    yyscha - 飞书转发：常用操作\n')
        for key, (label, _) in ACTIONS.items():
            print(f'    {key}. {label}')
        print('    5. 打开日常操作说明\n    0. 退出\n')
        try:
            selected = input('请选择：').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if selected == '0':
            return 0
        if selected == '5':
            os.startfile(ROOT / 'DAILY-USAGE.md')
        elif selected in ACTIONS:
            target = ROOT / ACTIONS[selected][1]
            try:
                subprocess.call([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(target)])
            except KeyboardInterrupt:
                print('\n已返回菜单。')
        else:
            print('请输入 0 到 5 之间的数字。')


if __name__ == '__main__':
    sys.exit(main())

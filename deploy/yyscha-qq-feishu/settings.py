"""Local settings editor. Never sends messages or prints credentials."""
import json
import os
import tkinter as tk
from tkinter import messagebox, filedialog
from cookie_import import cookie_from_txt
from relay import PRIVATE, UIDS, Failure, load_config


def main():
    path = PRIVATE / 'config.json'
    previous = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            messagebox.showerror('������Ч', '���������޷���ȡ�������ڱ��ؼ�飻δ����ԭ�ļ���')
            return
    root = tk.Tk()
    root.title('yyscha ��ҳת��˽������')
    root.geometry(f'820x{min(820, root.winfo_screenheight() - 120)}')
    root.configure(padx=20, pady=15)
    canvas = tk.Canvas(root, highlightthickness=0)
    scrollbar = tk.Scrollbar(root, orient='vertical', command=canvas.yview)
    scrollbar.pack(side='right', fill='y')
    canvas.pack(side='left', fill='both', expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    form = tk.Frame(canvas)
    window = canvas.create_window((0, 0), window=form, anchor='nw')
    form.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
    root.bind('<MouseWheel>', lambda event: canvas.yview_scroll(-int(event.delta / 120), 'units'))
    tk.Label(form, text='ƾ��ֻ���浽 ����˽��Ŀ¼�����治�ᷢ�ͻ�����������', anchor='w').pack(fill='x')
    fields = {}
    for key, label, secret in [('site_cookie', '��վ����ͷ Cookie����ֵ��', True),
                               ('feishu_webhook', '��������Ⱥ������ Webhook�����Ժ��', True),
                               ('feishu_secret', '����ǩ����Կ��ѡ�', True),
                               ('feishu_keyword', '���鰲ȫ�ؼ��ʣ�ѡ�', False),
                               ('feishu_app_id', '������ͼӦ�� App ID�������ѷ���Ӧ�ã�', False),
                               ('feishu_app_secret', '������ͼӦ�� App Secret', True),
                               ('uids', '���� UID��Ӣ�Ķ��ŷָ������л��ߺ�Ҫֱ����ɾ��', False)]:
        tk.Label(form, text=label).pack(anchor='w', pady=(16, 3))
        entry = tk.Entry(form, show='��' if secret else '', font=('', 11))
        entry.pack(fill='x')
        value = previous.get(key, UIDS if key == 'uids' else '')
        entry.insert(0, ','.join(value) if key == 'uids' else value)
        fields[key] = entry
        if key == 'site_cookie':
            def import_cookie():
                filename = filedialog.askopenfilename(title='ѡ�񱾻� cookies.txt', filetypes=[('Cookie�ı��ļ�', '*.txt'), ('�����ļ�', '*.*')])
                if not filename:
                    return
                try:
                    value, count = cookie_from_txt(filename)
                except ValueError as exc:
                    messagebox.showerror('����ʧ��', str(exc))
                    return
                except OSError:
                    messagebox.showerror('����ʧ��', '�޷���ȡ��ѡ�ļ��������ļ�Ȩ�ޡ�')
                    return
                fields['site_cookie'].delete(0, 'end')
                fields['site_cookie'].insert(0, value)
                messagebox.showinfo('�ѵ���', f'����ȡ{count}��������yyscha��Ϣ�ӿڵ�Cookie��\n������վCookieδ���룬ԭ�ļ�δ���ơ�\n��������汾�����ã������ж�ȡ��顣')
            tk.Button(form, text='�� cookies.txt ���루ѡ�񱾵��ļ���', command=import_cookie).pack(anchor='w', pady=(5, 0))

    def save():
        config = dict(previous)
        config.update({k: v.get().strip() for k, v in fields.items()})
        config['uids'] = [x.strip() for x in config['uids'].replace('��', ',').split(',') if x.strip()]
        if config['site_cookie'].lower().startswith('cookie:'):
            config['site_cookie'] = config['site_cookie'][7:].strip()
        config.setdefault('interval_seconds', 15)
        config.setdefault('previous_day_minutes', 10)
        config.setdefault('require_trusted_time', False)
        if previous and previous.get('uids') != config['uids'] and (PRIVATE / 'state.sqlite3').exists():
            messagebox.showerror('�赥����������', '�������ݿ�ʱ�뱣��UID������������Դ���������ú����ݿ���ɻ��ߡ�')
            return
        try:
            PRIVATE.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix('.new')
            with temp.open('w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            load_config(temp)
            temp.replace(path)
        except Failure as exc:
            temp.unlink(missing_ok=True)
            messagebox.showerror('δ����', str(exc))
            return
        except OSError:
            messagebox.showerror('δ����', '˽�������޷�д�룬����Ȩ�ޡ�')
            return
        messagebox.showinfo('�ѱ���', '���ñ����ڱ���˽��Ŀ¼����һ�����С�02-����ȡ.cmd������󱾵�Ԥ����')
        root.destroy()

    tk.Label(form, text='Ĭ��������ʾ���û�1��ʾ���û�2��ʾ���û�3������д�Լ���Ŀ��UID��\n��ѡ�񱾵�cookies.txt�Զ���ȡ�����ֶ�ճ����վ����ͷCookie��\n�ļ�ֻ�ڱ�����ȡ�������ϴ����죻����������汾�����á�', justify='left').pack(anchor='w', pady=18)
    tk.Button(form, text='���汾������', command=save, pady=8).pack(fill='x')
    root.mainloop()


if __name__ == '__main__':
    main()

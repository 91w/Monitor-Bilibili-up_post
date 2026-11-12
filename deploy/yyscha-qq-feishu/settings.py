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
            messagebox.showerror('配置无效', '现有配置无法读取，请先在本地检查；未覆盖原文件。')
            return
    root = tk.Tk()
    root.title('yyscha 网页转发私密设置')
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
    tk.Label(form, text='凭据只保存到 work 私密目录；保存不会发送或启动监听。', anchor='w').pack(fill='x')
    fields = {}
    for key, label, secret in [('site_cookie', '网站请求头 Cookie（仅值）', True),
                               ('feishu_webhook', '独立飞书群机器人 Webhook（可稍后填）', True),
                               ('feishu_secret', '飞书签名密钥（选填）', True),
                               ('feishu_keyword', '飞书安全关键词（选填）', False),
                               ('feishu_app_id', '飞书配图应用 App ID（复用已发布应用）', False),
                               ('feishu_app_secret', '飞书配图应用 App Secret', True),
                               ('feishu_chat_id', '文件接收群 chat_id（oc_ 开头；应用机器人须已入群）', False),
                               ('uids', '监听 UID（英文逗号分隔；已有基线后不要直接增删）', False)]:
        tk.Label(form, text=label).pack(anchor='w', pady=(16, 3))
        entry = tk.Entry(form, show='●' if secret else '', font=('', 11))
        entry.pack(fill='x')
        value = previous.get(key, UIDS if key == 'uids' else '')
        entry.insert(0, ','.join(value) if key == 'uids' else value)
        fields[key] = entry
        if key == 'site_cookie':
            def import_cookie():
                filename = filedialog.askopenfilename(title='选择本机 cookies.txt', filetypes=[('Cookie文本文件', '*.txt'), ('所有文件', '*.*')])
                if not filename:
                    return
                try:
                    value, count = cookie_from_txt(filename)
                except ValueError as exc:
                    messagebox.showerror('导入失败', str(exc))
                    return
                except OSError:
                    messagebox.showerror('导入失败', '无法读取所选文件，请检查文件权限。')
                    return
                fields['site_cookie'].delete(0, 'end')
                fields['site_cookie'].insert(0, value)
                messagebox.showinfo('已导入', f'已提取{count}项适用于yyscha消息接口的Cookie。\n其他网站Cookie未导入，原文件未复制。\n请继续保存本地配置，再运行读取检查。')
            tk.Button(form, text='从 cookies.txt 导入（选择本地文件）', command=import_cookie).pack(anchor='w', pady=(5, 0))

    def save():
        config = dict(previous)
        config.update({k: v.get().strip() for k, v in fields.items()})
        config['uids'] = [x.strip() for x in config['uids'].replace('，', ',').split(',') if x.strip()]
        if config['site_cookie'].lower().startswith('cookie:'):
            config['site_cookie'] = config['site_cookie'][7:].strip()
        config.setdefault('interval_seconds', 15)
        config.setdefault('previous_day_minutes', 10)
        config.setdefault('require_trusted_time', False)
        if previous and previous.get('uids') != config['uids'] and (PRIVATE / 'state.sqlite3').exists():
            messagebox.showerror('需单独建立基线', '已有数据库时请保持UID名单；新增来源需另用配置和数据库完成基线。')
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
            messagebox.showerror('未保存', str(exc))
            return
        except OSError:
            messagebox.showerror('未保存', '私密配置无法写入，请检查权限。')
            return
        messagebox.showinfo('已保存', '配置保存在本机work目录。下一步运行“02-检查读取.cmd”，随后本地预览。')
        root.destroy()

    tk.Label(form, text='默认名单：示例用户一、示例用户二、示例用户三。\n可选择本地cookies.txt自动提取，或手动粘贴网站请求头Cookie。\n文件只在本机读取，无需上传聊天；导入后点击保存本地配置。', justify='left').pack(anchor='w', pady=18)
    tk.Button(form, text='保存本地配置', command=save, pady=8).pack(fill='x')
    root.mainloop()


if __name__ == '__main__':
    main()

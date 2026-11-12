"""Local-only credential editor. Never sends credentials or starts a relay."""
import json
import os
from pathlib import Path
import uuid

from cos_transport import BUCKET, REGION, DEVICE, PRIVATE, CloudError, validate_credentials

FILES = {'uploader': 'cos-uploader.json', 'receiver': 'cos-receiver-bootstrap.json'}


def save_credentials(role, secret_id, secret_key):
    if role not in FILES:
        raise CloudError('配置角色无效')
    config = validate_credentials(dict(bucket=BUCKET, region=REGION, device=DEVICE,
                                       secret_id=secret_id.strip(), secret_key=secret_key.strip()))
    PRIVATE.mkdir(parents=True, exist_ok=True)
    path = PRIVATE / FILES[role]
    temp = PRIVATE / ('.cos-config-' + uuid.uuid4().hex)
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(config, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def main():
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.title('COS 本机私密凭据设置')
    root.geometry('760x540')
    root.configure(padx=24, pady=18)
    tk.Label(root, text='只保存到本机 work 私密目录；不会上传、测试或启动监听。', anchor='w').pack(fill='x')
    tk.Label(root, text=f'桶：{BUCKET}    地域：{REGION}', anchor='w').pack(fill='x', pady=8)
    tk.Label(root, text='开发板凭据暂存于本机用于 USB 初次配置，部署完成后再清理本机暂存。', anchor='w').pack(fill='x')
    for role, title in [('uploader', '电脑端：yyscha-uploader'), ('receiver', '开发板端：yyscha-receiver')]:
        frame = tk.LabelFrame(root, text=title, padx=12, pady=12)
        frame.pack(fill='x', pady=12)
        fields = []
        for label in ('SecretId', 'SecretKey'):
            tk.Label(frame, text=label).pack(anchor='w')
            entry = tk.Entry(frame, show='●', font=('', 11))
            entry.pack(fill='x', pady=3)
            fields.append(entry)
        saved = (PRIVATE / FILES[role]).exists()
        transferred = role == 'receiver' and (PRIVATE / 'receiver-bootstrap-transferred').exists()
        status = tk.Label(frame, text='已保存，可重新填写覆盖' if saved else
                          ('已转交开发板，电脑暂存已清理' if transferred else '尚未保存'))
        status.pack(side='left', pady=6)
        def save(role=role, fields=fields, status=status):
            try:
                save_credentials(role, fields[0].get(), fields[1].get())
                for field in fields:
                    field.delete(0, 'end')
                status.config(text='已保存到本机私密目录')
                messagebox.showinfo('保存成功', '凭据已私密保存。未发起网络请求，未验证实际权限。')
            except CloudError as exc:
                messagebox.showerror('未保存', str(exc))
            except Exception:
                messagebox.showerror('未保存', '保存失败，请检查本机私密目录权限；未输出凭据。')
        tk.Button(frame, text='保存这一端凭据', command=save).pack(side='right', pady=6)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('私密设置入口启动失败；未输出凭据。')
        raise SystemExit(1)

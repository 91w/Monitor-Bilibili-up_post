"""Enter app credentials locally; no secrets are written to the delivery folder."""
import json,os,shutil,tkinter as tk
from pathlib import Path
from tkinter import messagebox

CONFIG=Path(os.environ.get('B_UP_CONFIG_PATH', str(Path(__file__).resolve().parents[2]/'config.json')))
BACKUP=CONFIG.with_name('config.before-images.json')
def main():
    root=tk.Tk();root.title('飞书配图凭证设置');root.geometry('650x355');root.configure(padx=22,pady=18)
    tk.Label(root,text='填写已发布应用的 App ID 和 App Secret',font=('',15,'bold')).pack(anchor='w')
    tk.Label(root,text='凭证仅保存到原项目 config.json，不发送到聊天或 GitHub。',wraplength=600).pack(anchor='w',pady=10)
    tk.Label(root,text='App ID').pack(anchor='w');app=tk.Entry(root,width=65);app.pack(fill='x')
    tk.Label(root,text='App Secret').pack(anchor='w',pady=(12,0));secret=tk.Entry(root,width=65,show='●');secret.pack(fill='x')
    tk.Label(root,text='原配置路径：'+str(CONFIG),wraplength=600).pack(anchor='w',pady=14)
    def save():
        app_id=app.get().strip();key=secret.get().strip()
        if not app_id.startswith('cli_') or not key:messagebox.showerror('未保存','请检查 App ID（通常以 cli_ 开头）和 Secret 是否填写完整。');return
        try:
            data=json.loads(CONFIG.read_text(encoding='utf-8-sig'))
            data.update(feishu_app_id=app_id,feishu_app_secret=key,enable_dynamic_images=True)
            BACKUP.parent.mkdir(parents=True,exist_ok=True)
            if not BACKUP.exists():shutil.copyfile(CONFIG,BACKUP)
            tmp=CONFIG.with_suffix('.json.new')
            with tmp.open('w',encoding='utf-8') as f:
                json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
            tmp.replace(CONFIG)
        except Exception:messagebox.showerror('未保存','无法写入配置，请检查文件格式、路径与权限。');return
        secret.delete(0,'end')
        messagebox.showinfo('保存成功','凭证已保存到本机 config.json。\n请告诉我“已保存”，暂时不用自己上传或重启开发板。')
        root.destroy()
    tk.Button(root,text='保存到本机配置',command=save,pady=8).pack(fill='x')
    tk.Label(root,text='当前文字监听继续运行；代码接入并验证后才启用配图。',fg='#555').pack(pady=8)
    root.mainloop()
if __name__=='__main__':main()

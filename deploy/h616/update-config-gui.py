import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
import subprocess, sys, threading
root=tk.Tk();root.title('开发板监听配置更新');root.geometry('630x245')
config=tk.StringVar(value=r'D:\bWebMonitor\b-up\outputs\config.json')
host=tk.StringVar(value='192.168.0.145')
wireless=tk.BooleanVar(value=True)
tk.Label(root,text='配置文件（包含 Cookie，请仅保存在自己的电脑）').pack(anchor='w',padx=15,pady=6)
tk.Entry(root,textvariable=config,width=85).pack(padx=15)
tk.Button(root,text='选择 config.json',command=lambda:config.set(filedialog.askopenfilename(filetypes=[('JSON','*.json')]) or config.get())).pack()
tk.Checkbutton(root,text='通过同一局域网无线更新（取消勾选则使用 USB）',variable=wireless).pack(anchor='w',padx=15)
tk.Label(root,text='开发板 IP 地址（路由器若重新分配地址，请在这里修改）').pack(anchor='w',padx=15)
tk.Entry(root,textvariable=host,width=30).pack()
status=tk.StringVar(value='只更新配置，保留开发板的监听进度与待发送消息。')
tk.Label(root,textvariable=status).pack(pady=5)
def upload():
    args=[sys.executable,str(Path(__file__).with_name('update-config.py')),'--config',config.get()]
    if wireless.get(): args+=['--host',host.get().strip()]
    button.config(state='disabled');status.set('正在更新，请稍候……')
    def work():
        try:
            result=subprocess.run(args,capture_output=True,text=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            ok=result.returncode==0
        except Exception: ok=False
        def done():
            button.config(state='normal')
            status.set('更新成功，监听进程会自动重新加载配置。' if ok else '更新失败：检查网络、IP、证书、USB 和 JSON 格式；原配置一般保持不变。')
        root.after(0,done)
    threading.Thread(target=work,daemon=True).start()
button=tk.Button(root,text='上传并应用配置',command=upload);button.pack()
root.mainloop()

"""Collect credentials in the user's local window and send via USB stdin only."""
import json,subprocess,threading,tkinter as tk
from tkinter import messagebox
ADB=r'C:\path\to\adb.exe'
REMOTE='/overlay/b-up-runtime/python/bin/python3 /overlay/napcat-lab/login_settings.py '
FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)

def call(action,data=None):
    result=subprocess.run([ADB,'-s','YOUR_ADB_SERIAL','shell',REMOTE+action],input=(json.dumps(data,ensure_ascii=True)+'\n').encode() if data is not None else None,
        capture_output=True,timeout=15,creationflags=FLAGS)
    # ADB can return zero when a remote command fails; require a fixed success marker.
    value=result.stdout.decode('utf-8',errors='replace').strip()
    if result.returncode:raise RuntimeError('USB command failed')
    if action=='status':return json.loads(value)
    marker={'save':'QQ_PASSWORD_SETTINGS_SAVED','clear':'QQ_PASSWORD_SETTINGS_CLEARED','protocol-test':'QQ_PASSWORD_SETTINGS_PROTOCOL_OK'}[action]
    if marker not in [line.strip() for line in value.splitlines()]:raise RuntimeError('Save not confirmed')
    return None

def main():
    root=tk.Tk();root.title('QQ账号密码：自动登录配置');root.geometry('700x510');root.configure(padx=24,pady=18)
    tk.Label(root,text='QQ密码回退登录',font=('',18,'bold')).pack(anchor='w')
    tk.Label(root,text='请保持开发板USB连接。当前QQ保持在线，此配置用于下次需要登录时。\n优先使用现有登录状态，失败再尝试密码；验证码或手机验证仍需本人完成。',justify='left',wraplength=650).pack(anchor='w',pady=12)
    tk.Label(root,text='当前监听使用的QQ号').pack(anchor='w')
    account=tk.Entry(root);account.pack(fill='x',pady=(3,10))
    tk.Label(root,text='QQ账号密码（不是WebUI Token，也不是飞书密钥）').pack(anchor='w')
    password=tk.Entry(root,show='●');password.pack(fill='x',pady=(3,10))
    status=tk.StringVar(value='正在读取账号信息……');tk.Label(root,textvariable=status,wraplength=650,justify='left').pack(anchor='w',pady=8)
    def save():
        data={'account':account.get().strip(),'password':password.get()}
        if not data['account'].isascii() or not data['account'].isdecimal() or not data['password']:messagebox.showerror('未保存','请填写QQ号和密码');return
        save_button.config(state='disabled');status.set('正在保存到开发板私密目录……')
        def work():
            try:
                call('save',data)
                root.after(0,lambda:password.delete(0,'end'))
                root.after(0,lambda:status.set('已保存到开发板。当前QQ没有退出；下次启动时自动使用密码回退。\n此操作不代表密码已登录验证成功。'))
            except Exception:
                root.after(0,lambda:status.set('保存未完成。这不是QQ服务器的密码校验结果。'))
                root.after(0,lambda:messagebox.showerror('未保存','配置保存未完成，请确认USB连接，关闭旧工具后重新打开再试。\nQQ号须与当前监听账号一致。不要在聊天里发送密码。'))
            finally:
                data.clear();root.after(0,lambda:save_button.config(state='normal'))
        threading.Thread(target=work,daemon=True).start()
    def clear():
        try:call('clear');password.delete(0,'end');status.set('已移除保存的密码。下次启动不使用密码回退。')
        except Exception:messagebox.showerror('未清除','请检查USB连接。')
    save_button=tk.Button(root,text='保存到开发板，保持当前QQ运行',command=save,pady=8);save_button.pack(fill='x',pady=5)
    tk.Button(root,text='移除保存的密码（下次启动生效）',command=clear).pack(pady=5)
    tk.Button(root,text='USB重新连接后：读取开发板账号',command=lambda:load()).pack(pady=5)
    tk.Label(root,text='密码仅保存到开发板root可读的私密文件，不保存到电脑交付目录。\n文件采用权限保护；没有宣称密码在开发板上加密。',justify='left',fg='#555',wraplength=650).pack(anchor='w',pady=8)
    def load():
        try:
            data=call('status');account.delete(0,'end');account.insert(0,data['account']);status.set('已有密码配置，可填写新密码替换。' if data['configured'] else '请填写密码并保存；不要把密码发到聊天中。')
        except Exception:status.set('暂未读到开发板，请连接USB后再保存。')
    root.after(150,load);root.mainloop()
if __name__=='__main__':main()

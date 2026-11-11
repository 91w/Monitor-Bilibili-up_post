"""Open the running instance over USB. WebUI credentials stay in memory."""
import json,socket,subprocess,tkinter as tk,webbrowser,time,threading,urllib.request
from tkinter import messagebox
from urllib.parse import quote
ADB=r'C:\path\to\adb.exe'
FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)
PORT=16099
def adb(*args):
    return subprocess.run([ADB,'-s','YOUR_ADB_SERIAL',*args],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=12,check=True,creationflags=FLAGS).stdout.strip()
def main():
    root=tk.Tk();root.title('正在运行的QQ管理页');root.geometry('640x400');root.configure(padx=20,pady=20)
    clipboard_secret={'value':None}
    tk.Label(root,text='QQ正在开发板后台运行',font=('',16,'bold')).pack(anchor='w')
    tk.Label(root,text='保持USB连接。固定网页入口： http://127.0.0.1:16099/webui/\n关闭此窗口会保留网页入口，后台QQ继续运行。\n拔插USB或电脑重启后，重新运行本工具恢复入口。',justify='left',wraplength=590).pack(anchor='w',pady=15)
    status=tk.StringVar(value='正在检查开发板和网页资源……')
    tk.Label(root,textvariable=status,justify='left',wraplength=590).pack(anchor='w',pady=8)
    def open_page():
        button.config(state='disabled');status.set('正在检查USB连接并恢复固定端口……')
        def work():
            try:
                if adb('get-state')!='device':raise RuntimeError('USB')
                existing=[line.split() for line in adb('forward','--list').splitlines()]
                maps=[v for v in existing if len(v)>=3 and v[1]=='tcp:'+str(PORT)]
                if maps and not all(v[0]=='YOUR_ADB_SERIAL' and v[2]=='tcp:6099' for v in maps):raise RuntimeError('PORT')
                if not maps:
                    try:
                        with socket.socket() as sock:sock.bind(('127.0.0.1',PORT))
                    except OSError:raise RuntimeError('PORT')
                adb('forward','tcp:'+str(PORT),'tcp:6099')
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open('http://127.0.0.1:'+str(PORT)+'/webui/?check='+str(int(time.time())),timeout=8) as response:
                    if response.status!=200:raise RuntimeError('WEBUI')
                code="import json;print(json.load(open('/overlay/napcat-lab/rootfs/opt/QQ/resources/app/napcat/config/webui.json'))['token'])"
                token=adb('shell','/overlay/b-up-runtime/python/bin/python3 -c "'+code+'"')
                if not token or '\n' in token:raise RuntimeError('TOKEN')
                # The root route redirects without preserving its query string.
                # Give the token directly to the supported login page instead.
                webbrowser.open('http://127.0.0.1:'+str(PORT)+'/webui/web_login?token='+quote(token,safe='')+'&v='+str(int(time.time())))
                token=None
                root.after(0,lambda:status.set('固定入口已恢复。请使用新打开的页面，关闭旧13159端口的标签页。\n已登录时直接管理，只有QQ要求验证时才需扫码。'))
            except Exception as error:
                reason='本机16099端口被其他应用占用。' if str(error)=='PORT' else '请确认开发板USB已连接。若仍失败，请把错误提示告诉助手。'
                root.after(0,lambda:messagebox.showerror('暂时无法打开',reason+'\n不要发送Token。'))
                root.after(0,lambda:status.set('入口检查未通过；没有重启或退出QQ。'))
            finally:root.after(0,lambda:button.config(state='normal'))
        threading.Thread(target=work,daemon=True).start()
    def close():
        try:
            if clipboard_secret['value'] and root.clipboard_get()==clipboard_secret['value']:root.clipboard_clear()
        except tk.TclError:pass
        clipboard_secret['value']=None
        root.destroy()
    def copy_token():
        try:
            code="import json;print(json.load(open('/overlay/napcat-lab/rootfs/opt/QQ/resources/app/napcat/config/webui.json'))['token'])"
            token=adb('shell','/overlay/b-up-runtime/python/bin/python3 -c "'+code+'"')
            if not token or '\n' in token:raise RuntimeError()
            root.clipboard_clear();root.clipboard_append(token)
            clipboard_secret['value']=token
            status.set('管理Token已复制。粘贴到Web Login的Token框，再点击登录。\n这不是QQ账号重新登录。剪贴板将在60秒后按需清空。')
            def clear():
                try:
                    if root.clipboard_get()==token:root.clipboard_clear()
                except tk.TclError:pass
            root.after(60000,clear)
        except Exception:messagebox.showerror('复制失败','请保持USB连接，不要在聊天中发送Token。')
    button=tk.Button(root,text='恢复入口并打开当前QQ管理页',command=open_page,pady=8);button.pack(fill='x')
    tk.Button(root,text='仍显示Web Login时：复制管理Token',command=copy_token).pack(pady=5)
    tk.Button(root,text='关闭工具，保留网页入口和后台运行',command=close).pack(pady=8)
    root.protocol('WM_DELETE_WINDOW',close)
    root.after(200,open_page)
    root.mainloop()
if __name__=='__main__':main()

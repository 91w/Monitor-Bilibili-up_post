"""Router-assisted address entry, with a three-day cache and pinned board verification."""
import ipaddress,json,os,subprocess,sys,threading,time,webbrowser
from pathlib import Path
import tkinter as tk

MAC=os.environ.get('B_UP_BOARD_MAC','aa:bb:cc:dd:ee:ff').lower()
ROUTER=os.environ.get('B_UP_ROUTER_URL','http://router.local/')
CACHE=Path(os.environ.get('B_UP_STATE_DIR',str(Path.home()/'.b-up')))/'home-address-cache.json'
TTL=3*86400

def validate_ip(value):
    addr=ipaddress.ip_address(value.strip())
    if addr.version!=4 or not addr.is_private or addr.is_loopback or addr.is_unspecified or addr.is_multicast or addr.is_link_local:
        raise ValueError('请填写路由器中开发板对应的局域网 IPv4 地址')
    return str(addr)

def load_cache(path=CACHE,now=None):
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        age=(time.time() if now is None else now)-float(data['verified_at'])
        if data.get('mac')==MAC and 0<=age<TTL:
            return validate_ip(data['ip'])
    except (OSError,ValueError,KeyError,TypeError):pass
    return None

def save_cache(ip,path=CACHE):
    # Keep the original verification date within the same three-day window.
    if load_cache(path)==ip:return
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.new')
    temp.write_text(json.dumps({'ip':ip,'mac':MAC,'verified_at':time.time()}),encoding='utf-8')
    temp.replace(path)

def main():
    root=tk.Tk();root.title('开发板状态与配置入口');root.geometry('660x420')
    root.configure(padx=24,pady=18)
    tk.Label(root,text='连接你的开发板',font=('',19,'bold')).pack(anchor='w')
    tk.Label(root,text='① 登录路由器 → ② 对照 MAC 查 IP → ③ 输入后打开网页',wraplength=600).pack(anchor='w',pady=12)
    tk.Button(root,text='打开路由器管理页',command=lambda:webbrowser.open(ROUTER)).pack(anchor='w')
    tk.Label(root,text='在“已连接设备／DHCP 地址列表”中寻找：').pack(anchor='w',pady=(18,4))
    row=tk.Frame(root);row.pack(anchor='w')
    tk.Label(row,text=MAC,font=('Consolas',17,'bold')).pack(side='left')
    def copy_mac():root.clipboard_clear();root.clipboard_append(MAC)
    tk.Button(row,text='复制 MAC',command=copy_mac).pack(side='left',padx=12)
    tk.Label(root,text='输入路由器列表中上述 MAC 所对应的 IP：').pack(anchor='w',pady=(16,4))
    address=tk.StringVar();entry=tk.Entry(root,textvariable=address,font=('Consolas',16));entry.pack(fill='x')
    status=tk.StringVar(value='路由器密码只在路由器页面输入；本工具不读取或保存密码。')
    tk.Label(root,textvariable=status,wraplength=600,justify='left',fg='#34546e').pack(anchor='w',pady=12)
    tk.Label(root,text='验证成功后记住地址 3 天；期间启动时仍检查设备身份。\n关闭本窗口会关闭本机网页入口，不会停止开发板监听。',justify='left',fg='#666').pack(anchor='w',side='bottom')
    state={'process':None,'closing':False}
    def set_status(text):
        if not state['closing']:status.set(text)
    def start(automatic=False):
        try:ip=validate_ip(address.get())
        except ValueError as exc:set_status(str(exc));return
        process=state['process']
        if process and process.poll() is None:
            set_status('网页入口已运行。需要切换 IP 时，请关闭本窗口后重新打开。');return
        button.config(state='disabled');set_status('正在连接并验证开发板，请稍候……')
        def work():
            ok=False
            try:
                env=os.environ.copy();env['PYTHONIOENCODING']='utf-8'
                process=subprocess.Popen([sys.executable,'-u',str(Path(__file__).with_name('local-config-web.py')),'--host',ip],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                state['process']=process
                for line in process.stdout:
                    if '网页只在本机开放' in line:
                        ok=True
                        try:save_cache(ip)
                        except OSError:pass
                        root.after(0,lambda:set_status('已验证并打开状态／配置网页。3 天内可直接启动，保持本窗口打开。'))
                process.wait()
            except Exception:pass
            if state['closing']:return
            def finish():
                button.config(state='normal')
                if not ok:
                    set_status('连接未通过：请在路由器列表核对 MAC 对应的最新 IP，再输入重试。')
                    if automatic:webbrowser.open(ROUTER)
                else:set_status('网页入口已关闭，可以再次打开。')
            root.after(0,finish)
        threading.Thread(target=work,daemon=True).start()
    button=tk.Button(root,text='验证并打开状态／配置网页',command=start,pady=8);button.pack(fill='x',pady=4)
    def close():
        state['closing']=True
        p=state['process']
        if p and p.poll() is None:p.terminate()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    cached=load_cache()
    if cached:
        address.set(cached);root.after(250,lambda:start(True))
    else:
        root.after(400,lambda:webbrowser.open(ROUTER));entry.focus_set()
    root.mainloop()

if __name__=='__main__':main()

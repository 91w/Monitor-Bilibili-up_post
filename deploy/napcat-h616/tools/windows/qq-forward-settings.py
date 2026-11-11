import json,os,tkinter as tk
from pathlib import Path
from tkinter import messagebox
from urllib.parse import urlparse
CONFIG=Path(r'D:\CodexProjects\work\napcat-h616-lab\private\forward-config.json')
def main():
    root=tk.Tk();root.title('QQ群管理员 → 飞书配置');root.geometry('720x470');root.configure(padx=22,pady=16)
    tk.Label(root,text='仅指定群管理员发言',font=('',17,'bold')).pack(anchor='w')
    tk.Label(root,text='普通成员、私聊和其他群不转发，不入库，不下载其图片。',wraplength=670).pack(anchor='w',pady=10)
    data=json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    tk.Label(root,text='目标QQ群号（多个用逗号分隔）').pack(anchor='w')
    groups=tk.Entry(root);groups.insert(0,','.join(data.get('allowed_group_ids',[])));groups.pack(fill='x')
    fields={}
    for key,label,mask in [('feishu_webhook','独立飞书群机器人Webhook',True),('feishu_secret','签名密钥（仅开启签名校验时填写）',True),('feishu_keyword','关键词（仅配置关键词校验时填写）',False)]:
        tk.Label(root,text=label).pack(anchor='w',pady=(12,3));e=tk.Entry(root,show='●' if mask else '');e.pack(fill='x');fields[key]=e
    owner=tk.BooleanVar(value=bool(data.get('include_owner',False)))
    tk.Checkbutton(root,text='同时包含群主（默认不勾选）',variable=owner).pack(anchor='w',pady=10)
    def save():
        ids=[x.strip() for x in groups.get().replace('，',',').split(',') if x.strip()]
        if not ids or not all(x.isdecimal() and int(x)>0 for x in ids):messagebox.showerror('未保存','请填写有效群号');return
        webhook=fields['feishu_webhook'].get().strip();parsed=urlparse(webhook)
        if parsed.scheme!='https' or parsed.hostname!='open.feishu.cn' or not parsed.path.startswith('/open-apis/bot/v2/hook/') or parsed.username or parsed.password:messagebox.showerror('未保存','请填写独立飞书群的完整机器人Webhook');return
        cfg={'allowed_group_ids':list(dict.fromkeys(ids)),'include_owner':owner.get(),'enabled':False,'role_filter':'admin','store_unmatched_messages':False,'download_unmatched_media':False}
        for key in ('include_keywords','exclude_keywords','group_rules'):
            if key in data:cfg[key]=data[key]
        cfg.update({k:v.get().strip() for k,v in fields.items()})
        CONFIG.parent.mkdir(parents=True,exist_ok=True);temp=CONFIG.with_suffix('.new')
        try:
            with temp.open('w',encoding='utf-8') as f:json.dump(cfg,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
            temp.replace(CONFIG)
        except Exception:messagebox.showerror('未保存','请检查配置文件写入权限');return
        for e in fields.values():e.delete(0,'end')
        messagebox.showinfo('已保存','仅保存到本机私密目录，转发仍关闭。\n请告诉助手“QQ转发配置已保存”。');root.destroy()
    tk.Button(root,text='保存配置，暂不启用发送',command=save,pady=9).pack(fill='x')
    tk.Label(root,text='不需要网站Cookie，也不需要重新填写B站配图应用的Secret。',fg='#555').pack(anchor='w',pady=10)
    root.mainloop()
if __name__=='__main__':main()

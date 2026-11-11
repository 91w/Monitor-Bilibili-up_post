"""Read-only LAN status plus durable daily report worker. No credentials in responses."""
import datetime as dt, json, os, sqlite3, subprocess, time, threading, logging
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
BASE=Path('/overlay/b-up')
CST=dt.timezone(dt.timedelta(hours=8))

def database():
    c=sqlite3.connect(BASE/'data/outbox.sqlite3',timeout=5)
    c.execute('CREATE TABLE IF NOT EXISTS events(at REAL, kind TEXT, uid TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS reports(day TEXT PRIMARY KEY,body TEXT,sent INTEGER DEFAULT 0,attempts INTEGER DEFAULT 0,due REAL DEFAULT 0)')
    c.execute('CREATE TABLE IF NOT EXISTS monitor_meta(key TEXT PRIMARY KEY,value REAL)')
    c.commit();return c

def snapshot(now=None):
    now=time.time() if now is None else now
    config=json.loads((BASE/'data/config.json').read_text(encoding='utf-8-sig'))
    from timeguard import require_trusted_time
    try: clock=require_trusted_time()
    except Exception: clock={'trusted':False}
    try:
        pid=int(Path('/var/run/b-up-watcher.pid').read_text())
        running=b'/overlay/b-up/app/runner.py' in Path(f'/proc/{pid}/cmdline').read_bytes()
    except (OSError,ValueError): running=False
    wifi={}
    try:
        for line in subprocess.check_output(['wpa_cli','-i','wlan0','status'],timeout=3,text=True).splitlines():
            key,_,val=line.partition('=')
            if key in ('wpa_state','ip_address'):wifi[key]=val
    except Exception: pass
    with database() as c:
        counts=dict(c.execute('SELECT kind,count(*) FROM events WHERE at>=? AND at<=? GROUP BY kind',(now-86400,now)))
        latest=dict(c.execute("SELECT uid,max(at) FROM events WHERE kind IN ('checked','empty','collection_error') GROUP BY uid"))
        outcomes={uid:kind for uid,kind in c.execute("SELECT uid,kind FROM events WHERE rowid IN (SELECT max(rowid) FROM events WHERE kind IN ('checked','empty','collection_error') GROUP BY uid)")}
        queued=c.execute('SELECT count(*) FROM queue WHERE sent=0').fetchone()[0]
        reports=c.execute('SELECT day,sent FROM reports ORDER BY day DESC LIMIT 1').fetchone()
    users=[]
    for raw in config.get('up_users',[]):
        uid=str(raw.get('uid','')) if isinstance(raw,dict) else str(raw)
        name=str(raw.get('name') or uid) if isinstance(raw,dict) else uid
        users.append({'uid':uid,'name':name,'last_check':latest.get(uid),'outcome':outcomes.get(uid,'unknown')})
    mem={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        k,_,v=line.partition(':')
        if k in ('MemTotal','MemAvailable'):mem[k]=round(int(v.split()[0])/1024,1)
    stat=os.statvfs(BASE)
    return {'now':now,'running':running,'enabled':(BASE/'data/enabled').exists(),'clock':clock,'wifi':wifi,
            'queue':queued,'counts_24h':counts,'users':users,'uptime_hours':round(float(Path('/proc/uptime').read_text().split()[0])/3600,1),
            'memory_mb':mem,'disk_free_gb':round(stat.f_bavail*stat.f_frsize/1024**3,1),'last_report':reports,
            'report_hour':int(config.get('daily_report_hour',21)), 'report_enabled':config.get('daily_report_enabled',True)}

def report_body(s):
    c=s['counts_24h']; stamp=dt.datetime.fromtimestamp(s['now'],CST).strftime('%Y-%m-%d %H:%M')
    return '\n'.join([f'统计截至：{stamp}（北京时间，过去24小时；升级前不计入）',
        f'监听进程：{"运行中" if s["running"] else "未运行"}；自动启动：{"已启用" if s["enabled"] else "已停用"}',
        f'配置 UP：{len(s["users"])}；检查成功：{c.get("checked",0)}；空列表：{c.get("empty",0)}；检查失败：{c.get("collection_error",0)}',
        f'新增入队：{c.get("queued",0)}；推送成功：{c.get("sent",0)}；失败重试次数：{c.get("retry",0)}；当前待发送：{s["queue"]}',
        f'Wi-Fi：{s["wifi"].get("wpa_state","未知")}；校时：{"正常" if s["clock"]["trusted"] else "未通过"}',
        f'开机运行：{s["uptime_hours"]}小时；可用内存：{s["memory_mb"].get("MemAvailable","未知")}MB；剩余存储：{s["disk_free_gb"]}GB',
        '空列表不代表确认没有动态。日报不是失联告警：设备断电或彻底断网时无法发送。'])

def due_day(now,hour,activated):
    day=dt.datetime.fromtimestamp(now,CST).replace(hour=hour,minute=0,second=0,microsecond=0)
    if day.timestamp()>now:day-=dt.timedelta(days=1)
    return day.strftime('%Y-%m-%d') if day.timestamp()>activated else None

def reports_tick():
    from timeguard import require_trusted_time
    require_trusted_time()
    now=time.time();cfg=json.loads((BASE/'data/config.json').read_text(encoding='utf-8-sig'))
    if not cfg.get('daily_report_enabled',True):return
    hour=int(cfg.get('daily_report_hour',21))
    if not 0<=hour<=23:raise ValueError('daily_report_hour invalid')
    with database() as c:
        c.execute("INSERT OR IGNORE INTO monitor_meta VALUES('activated',?)",(now,))
        activated=c.execute("SELECT value FROM monitor_meta WHERE key='activated'").fetchone()[0]
    day=due_day(now,hour,activated)
    if day:
        s=snapshot(now)
        with database() as c:
            c.execute('INSERT OR IGNORE INTO reports(day,body) VALUES(?,?)',(day,report_body(s)))
    with database() as c:
        c.execute('DELETE FROM events WHERE at<?',(now-35*86400,))
    with database() as c:pending=c.execute('SELECT day,body,attempts FROM reports WHERE sent=0 AND due<=? ORDER BY day LIMIT 3',(now,)).fetchall()
    import bilibili_feishu_watcher as w
    for day,body,attempts in pending:
        try:
            require_trusted_time()
            keyword=cfg.get('feishu_keyword') or ''
            w.post_to_feishu(cfg['feishu_webhook'],f'{keyword} 监听运行日报 · {day}'.strip(),body,cfg.get('feishu_secret') or None)
        except Exception:
            with database() as c:c.execute('UPDATE reports SET attempts=attempts+1,due=? WHERE day=?',(now+min(3600,60*2**min(attempts,6)),day))
        else:
            with database() as c:c.execute('UPDATE reports SET sent=1 WHERE day=?',(day,))

HTML='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>开发板监听状态</title>
<style>body{background:#f0f4f8;color:#15283c;font:16px system-ui;margin:0}main{max-width:1000px;margin:40px auto;padding:20px}h1{font-size:32px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}.card,section{background:white;border-radius:16px;padding:22px;margin:14px 0}.value{font-size:28px;font-weight:700}small{color:#536577}table{width:100%;text-align:left;border-collapse:collapse}td,th{padding:12px;border-bottom:1px solid #eee}a{color:#1762b3}#error{color:#b23030}</style>
<main><small>H616 · 全天监听</small><h1>开发板运行状态</h1><p id="updated">正在读取…</p><p id="error"></p><div class="grid" id="cards"></div><section><h2>UP 主检查情况</h2><p>空列表表示接口未返回动态，不能据此确认没有更新。</p><table><thead><tr><th>UP 主</th><th>最近检查</th><th>结果</th></tr></thead><tbody id="users"></tbody></table></section><section><h2>配置维护</h2><p>更新 UP 名单、Cookie 和飞书设置，需要浏览器配对证书。</p><a id="admin">进入 HTTPS 配置更新页 →</a><p><small>配置页不显示现有 Cookie，不提供关闭时间保护或清空队列的操作。</small></p></section><section><h2>飞书日报</h2><p id="report"></p><small>统计自本次升级起记录，保留35天。断电或彻底断网时不能自行报告失联。</small></section></main>
<script>const fmt=t=>t?new Date(t*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚无记录';
async function load(){try{let r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error();let s=await r.json();document.getElementById('error').textContent='';document.getElementById('updated').textContent='更新于 '+fmt(s.now)+' · 每15秒刷新';let c=s.counts_24h;let cards=[['监听进程',s.running?'运行中':'未运行'],['时间校准',s.clock.trusted?'正常':'等待校时'],['Wi-Fi',s.wifi.wpa_state==='COMPLETED'?'已连接':'未连接'],['待发送消息',s.queue],['24小时推送成功',c.sent||0],['24小时失败重试',c.retry||0],['24小时检查失败',c.collection_error||0],['可用内存',s.memory_mb.MemAvailable+' MB']];let box=document.getElementById('cards');box.replaceChildren();for(let [k,v]of cards){let el=document.createElement('div');el.className='card';let label=document.createElement('small');label.textContent=k;let value=document.createElement('div');value.className='value';value.textContent=v;el.append(label,value);box.append(el)}let rows=document.getElementById('users');rows.replaceChildren();for(let u of s.users){let tr=document.createElement('tr');for(let v of [u.name,fmt(u.last_check),({checked:'成功',empty:'返回空列表',collection_error:'检查失败',unknown:'尚未检查'})[u.outcome]||'已有消息活动']){let td=document.createElement('td');td.textContent=v;tr.append(td)}rows.append(tr)}document.getElementById('report').textContent=s.report_enabled?'每天北京时间 '+s.report_hour+':00 发送过去24小时摘要；首次从启用后的下一个日报时刻开始。':'日报已停用';document.getElementById('admin').href='https://'+location.hostname+':8443/';}catch(e){document.getElementById('error').textContent='读取失败：下方信息可能已过期，请检查网络或稍后重试。'}}load();setInterval(load,15000);</script></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path not in ('/','/api/status'):self.send_error(404);return
        try:
            payload=HTML.encode() if self.path=='/' else json.dumps(snapshot(),ensure_ascii=False).encode()
        except Exception:self.send_error(503);return
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8' if self.path=='/' else 'application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)

if __name__=='__main__':
    os.umask(0o077)
    database().close()
    server=ThreadingHTTPServer(('0.0.0.0',8080),Handler);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    while True:
        try:reports_tick()
        except Exception:pass
        time.sleep(30)

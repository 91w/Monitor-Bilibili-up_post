"""Bilibili startup notification after a verified successful collection cycle."""
from datetime import datetime,timezone,timedelta
import logging
from pathlib import Path
import time


class StartupNotifier:
    def __init__(self,db,ensure_time):
        self.db,self.ensure_time=db,ensure_time
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS startup_notice(id INTEGER PRIMARY KEY,boot_id TEXT,sent_at REAL,attempted_at REAL)')

    def send(self,w,config,user_count,now=None,boot_id=None):
        now=time.time() if now is None else now
        if boot_id is None:boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        row=self.db.execute('SELECT boot_id,sent_at,attempted_at FROM startup_notice WHERE id=1').fetchone()
        if row and row[0]==boot_id:
            if row[1] is not None and now-row[1]<600:return True
            if row[2] is not None and now-row[2]<60:return False
        self.ensure_time()
        reason='启动提醒首次启用' if not row or row[1] is None else ('开发板重启后已恢复' if row[0]!=boot_id else '监控服务已重新启动')
        stamp=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S')
        pending=self.db.execute('SELECT count(*) FROM queue WHERE sent=0').fetchone()[0]
        body='\n'.join(x for x in (str(config.get('feishu_keyword') or '').strip(),reason,f'时间：{stamp}（北京时间）',f'网络、可信时间及全部 {user_count} 位UP的动态接口检查已通过。',f'待发送动态：{pending} 条') if x)
        with self.db:
            if row and row[0]==boot_id:
                self.db.execute('UPDATE startup_notice SET attempted_at=? WHERE id=1',(now,))
            else:
                self.db.execute('INSERT OR REPLACE INTO startup_notice VALUES(1,?,?,?)',(boot_id,None,now))
        w.post_to_feishu(config['feishu_webhook'],'B站动态监控启动成功',body,config.get('feishu_secret') or None)
        with self.db:self.db.execute('UPDATE startup_notice SET sent_at=? WHERE id=1',(now,))
        return True


def install_startup_notice(w,db,ensure_time):
    notifier=StartupNotifier(db,ensure_time)
    original=w.check_once
    pending,healthy=[True],[False]
    def check(config,state_path,dry_run=False):
        before=db.execute('SELECT coalesce(max(rowid),0) FROM events').fetchone()[0]
        result=original(config,state_path,dry_run=dry_run)
        if dry_run or not config.get('startup_notifications',True) or not pending[0]:return result
        expected={user.uid for user in w.normalize_up_users(config.get('up_users',[]))}
        events=db.execute('SELECT kind,uid FROM events WHERE rowid>?',(before,)).fetchall()
        checked={uid for kind,uid in events if kind in ('checked','empty')}
        failed={uid for kind,uid in events if kind=='collection_error'}
        if expected and expected<=checked and not failed:healthy[0]=True
        if failed:healthy[0]=False
        if healthy[0]:
            try:
                pending[0]=not notifier.send(w,config,len(expected))
            except Exception as exc:
                logging.warning('Bilibili startup notice deferred: %s',type(exc).__name__)
        return result
    w.check_once=check
    return notifier

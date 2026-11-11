import unittest, datetime as dt
import tempfile,json
from pathlib import Path
from unittest.mock import patch
import status_service as service
from status_service import due_day,CST,report_body
class Tests(unittest.TestCase):
    def test_schedule(self):
        start=dt.datetime(2026,11,10,22,tzinfo=CST).timestamp()
        self.assertIsNone(due_day(start,21,start))
        self.assertIsNone(due_day(start+22*3600,21,start))
        self.assertEqual(due_day(start+23*3600,21,start),'2026-11-11')
    def test_summary(self):
        s={'now':1794316000,'counts_24h':{'sent':4,'retry':2},'running':True,'enabled':True,'users':[{}], 'queue':3,'wifi':{'wpa_state':'COMPLETED'},'clock':{'trusted':True},'uptime_hours':4,'memory_mb':{'MemAvailable':500},'disk_free_gb':100}
        b=report_body(s)
        self.assertIn('推送成功：4',b);self.assertIn('当前待发送：3',b)
        self.assertNotIn('Cookie',b)
    def test_report_retry_and_restart_dedup(self):
        import bilibili_feishu_watcher as watcher
        s={'now':1794316000,'counts_24h':{},'running':True,'enabled':True,'users':[], 'queue':0,'wifi':{},'clock':{'trusted':True},'uptime_hours':4,'memory_mb':{},'disk_free_gb':100}
        start=dt.datetime(2026,11,10,20,tzinfo=CST).timestamp()
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'data').mkdir();(base/'data/config.json').write_text(json.dumps({'feishu_webhook':'https://unused.invalid'}))
            with patch.object(service,'BASE',base),patch('timeguard.require_trusted_time',return_value={}),patch.object(service,'snapshot',return_value=s),patch.object(watcher,'post_to_feishu') as send:
                with patch.object(service.time,'time',return_value=start):service.reports_tick()
                send.assert_not_called()
                send.side_effect=OSError('network')
                with patch.object(service.time,'time',return_value=start+3600):service.reports_tick()
                with service.database() as c:self.assertEqual(c.execute('select sent,attempts from reports').fetchone(),(0,1))
                send.side_effect=None
                with patch.object(service.time,'time',return_value=start+3661):service.reports_tick()
                with service.database() as c:self.assertEqual(c.execute('select sent from reports').fetchone()[0],1)
                count=send.call_count
                with patch.object(service.time,'time',return_value=start+4000):service.reports_tick()
                self.assertEqual(send.call_count,count)
if __name__=='__main__':unittest.main()

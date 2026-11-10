import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
import timeguard

class TimeGuardTests(unittest.TestCase):
    def test_recent_sync(self):
        m={"epoch":1794316000,"uptime":100,"stratum":2}
        self.assertTrue(timeguard.validate(m,1794316060,160)["trusted"])

    def test_rejects_bad_or_expired_sync(self):
        good={"epoch":1794316000,"uptime":100,"stratum":2}
        cases=[
            ({},1794316060,160),
            (dict(good,stratum=16),1794316060,160),
            (good,1794318001,2101),
            (good,1794316000,1),
            (good,1794316120,160),
            (good,1794316000,160),
            (dict(good,epoch=0),60,160),
        ]
        for m,now,up in cases:
            with self.subTest(marker=m,now=now,up=up):
                with self.assertRaises(timeguard.TimeTrustError):
                    timeguard.validate(m,now,up)

    def test_guard_blocks_send_and_uses_current_timestamp(self):
        import runner
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/"time.json"
            with mock.patch.object(timeguard,"MARKER",marker):
                with mock.patch.object(runner,"original_post_to_feishu") as send:
                    with self.assertRaises(runner.watcher.WatcherError):
                        runner.guarded_post_to_feishu("https://unused.invalid","test","test")
                    send.assert_not_called()
                marker.write_text(json.dumps({"epoch":time.time(),"uptime":timeguard.boot_time(),"stratum":2}))
                response=mock.Mock()
                response.json.return_value={"code":0}
                with mock.patch.object(runner.watcher.requests,"post",return_value=response) as send:
                    runner.guarded_post_to_feishu("https://unused.invalid","test","test",secret="test-secret")
                    payload=send.call_args.kwargs["json"]
                    self.assertLess(abs(int(payload["timestamp"])-time.time()),3)
                    self.assertTrue(payload["sign"])
                    self.assertIsInstance(payload["timestamp"],str)

if __name__=="__main__":
    unittest.main()

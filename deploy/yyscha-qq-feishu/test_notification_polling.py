import io
import json
import sqlite3
import unittest
from datetime import datetime
from email.message import Message
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import relay as r
import notification_polling as n

SCHEDULE={'start':'09:00','end':'16:30','interval_minutes':5,'collect_grace_minutes':1.5,'dense_window_minutes':2,'workday_dense_sec':25,'workday_sparse_sec':180,'off_hours_sec':240}


class NotificationTest(unittest.TestCase):
    def setUp(self):
        self.db=r.connect(':memory:')
        self.gate=n.NotificationPolling()
        self.config={'uids':['u_example'],'site_cookie':'session=synthetic'}
        self.now=datetime(2026,11,12,12,2,tzinfo=r.TZ).timestamp()
        self.probe=Mock(return_value={'fp':'fingerprint-1','schedule':SCHEDULE})
        self.poll=Mock()

    def tearDown(self): self.db.close()

    def tick(self,seconds=0):
        return self.gate.tick(self.db,object(),self.config,now=self.now+seconds,probe_fn=self.probe,poll_fn=self.poll)

    def test_server_time_windows_and_invalid_values(self):
        for stamp,expected in [('2026-11-12T09:02:00',25),('2026-11-12T09:00:00',180),('2026-11-12T17:00:00',240),('2026-11-14T09:02:00',240),('2026-11-12T16:32:00',25),('2026-11-12T16:37:00',240)]:
            self.assertEqual(n.delay_seconds(SCHEDULE,datetime.fromisoformat(stamp).replace(tzinfo=r.TZ)),expected)
        invalid={'start':'oops','workday_dense_sec':float('nan'),'interval_minutes':0,'dense_window_minutes':[]}
        self.assertEqual(n.delay_seconds(invalid,datetime(2026,11,12,9,2,tzinfo=r.TZ)),25)

    def test_first_then_unchanged_skips_messages_and_keeps_delivery_enabled(self):
        self.assertTrue(self.tick())
        self.assertFalse(self.tick(10))
        self.probe.assert_called_once()
        self.assertFalse(self.tick(25))
        self.assertEqual(self.probe.call_count,2)
        self.poll.assert_called_once()
        self.assertTrue(self.gate.allow_delivery)

    def test_changed_false_hit_still_fetches_and_ignores_preferences(self):
        self.tick()
        self.probe.return_value={'fp':'fingerprint-2','schedule':SCHEDULE,'hit':False}
        self.assertTrue(self.tick(25))
        self.assertEqual(self.poll.call_count,2)

    def test_hourly_day_change_and_restart_force_recheck(self):
        self.tick()
        self.assertTrue(self.tick(3601))
        self.assertTrue(self.tick(86400))
        self.gate=n.NotificationPolling()
        self.assertTrue(self.tick(86401))
        self.assertEqual(self.poll.call_count,4)

    def test_failed_message_scan_never_commits_new_fingerprint(self):
        self.tick()
        self.probe.return_value={'fp':'new-fp','schedule':SCHEDULE}
        self.poll.side_effect=r.Failure('test scan failure')
        with self.assertRaises(r.Failure): self.tick(25)
        state=json.loads(self.db.execute('SELECT value FROM meta WHERE key=?',(n.STATE,)).fetchone()[0])
        self.assertEqual(state['fp'],'fingerprint-1')
        self.assertFalse(self.gate.allow_delivery)
        self.assertFalse(self.tick(40))
        self.poll.side_effect=None
        self.assertTrue(self.tick(85))

    def test_cookie_change_bypasses_normal_delay_but_not_error_backoff(self):
        self.tick()
        self.config['site_cookie']='session=updated-synthetic'
        self.poll.side_effect=r.Failure('test failure')
        with self.assertRaises(r.Failure): self.tick(1)
        calls=self.probe.call_count
        self.assertFalse(self.tick(2))
        self.assertEqual(calls,self.probe.call_count)
        self.poll.side_effect=None
        # Last stored fingerprint is unchanged, but failed new-session validation
        # must still force a message scan before authentication is cleared.
        self.assertTrue(self.tick(61))

    def test_rate_limit_persists_and_blocks_restart(self):
        self.probe.side_effect=r.RateLimited(600)
        with self.assertRaises(r.RateLimited): self.tick()
        self.gate=n.NotificationPolling()
        self.assertFalse(self.tick(100))
        self.assertFalse(self.gate.allow_delivery)
        self.probe.side_effect=None
        self.assertTrue(self.tick(600))

    def test_services_disabled_pauses_without_fetching_messages(self):
        self.probe.side_effect=n.ServicesDisabled('test disabled')
        with self.assertRaises(n.ServicesDisabled): self.tick()
        self.poll.assert_not_called()
        self.assertFalse(self.tick(300))
        self.assertFalse(self.gate.allow_delivery)
        status=json.loads(self.db.execute('SELECT value FROM meta WHERE key=?',(n.STATUS,)).fetchone()[0])
        self.assertEqual(status['outcome'],'services_disabled')

    def test_probe_bounded_response_and_http_429(self):
        headers=Message();headers['Retry-After']='600'
        with patch('site_session.get_session') as session:
            session.return_value.open.side_effect=HTTPError('https://example.invalid',429,'rate limited',headers,None)
            with self.assertRaises(r.RateLimited) as caught: n.probe({},'')
            self.assertEqual(caught.exception.retry_after,600)
        with patch('site_session.get_session') as session:
            session.return_value.open.side_effect=HTTPError('https://example.invalid',503,'disabled',headers,io.BytesIO(b'{"services_disabled":true}'))
            with self.assertRaises(n.ServicesDisabled): n.probe({},'')
        with patch('site_session.get_session') as session:
            session.return_value.open.return_value=io.BytesIO(b'x'*(64*1024+1))
            with self.assertRaises(r.Failure): n.probe({},'')

    def test_retry_after_seconds_and_http_date(self):
        self.assertEqual(r.retry_delay('120'),120)
        self.assertEqual(r.retry_delay('bad'),60)
        self.assertEqual(r.retry_delay('Thu, 12 Nov 2026 01:10:00 GMT',datetime(2026,11,12,9,0,tzinfo=r.TZ).timestamp()),600)


if __name__=='__main__': unittest.main()

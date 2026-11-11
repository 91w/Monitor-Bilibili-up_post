# Resolve source modules after the delivery directory organization.
import sys
from pathlib import Path
_project=Path(__file__).resolve().parents[1]
for _source in (_project/'board/relay',_project/'board/maintenance'):
    if _source.is_dir():sys.path.insert(0,str(_source))

import unittest
from message_filter import should_forward
class Tests(unittest.TestCase):
    def event(self,role='admin',group=12345):return {'post_type':'message','message_type':'group','group_id':group,'sender':{'user_id':67890,'role':role},'message':[{'type':'text','data':{'text':'test'}}]}
    def test_only_allowed_group_admin(self):
        self.assertTrue(should_forward(self.event(),['12345']))
        self.assertFalse(should_forward(self.event(),[]))
        self.assertFalse(should_forward(self.event(group=22222),[12345]))
    def test_other_members_and_owner(self):
        self.assertFalse(should_forward(self.event('member'),[12345]))
        self.assertFalse(should_forward(self.event('owner'),[12345]))
        self.assertTrue(should_forward(self.event('owner'),[12345],True))
    def test_private_notice_and_missing_identity(self):
        e=self.event();e['message_type']='private';self.assertFalse(should_forward(e,[12345]))
        e=self.event();e['post_type']='notice';self.assertFalse(should_forward(e,[12345]))
        e=self.event();e['sender']={};self.assertFalse(should_forward(e,[12345]))
        e=self.event();e['anonymous']={'name':'admin'};self.assertFalse(should_forward(e,[12345]))
    def test_mentions_do_not_grant_admin_role(self):
        e=self.event('member');e['message']=[{'type':'text','data':{'text':'引用管理员发言 @管理员'}}]
        self.assertFalse(should_forward(e,[12345]))
if __name__=='__main__':unittest.main()

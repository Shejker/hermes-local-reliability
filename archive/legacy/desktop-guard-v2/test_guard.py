"""Deterministic tests of the v2 decision logic, without accessing a live Hermes."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('guard', Path(__file__).parent/'desktop_guard.py')
g=importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

class ContextGuardTests(unittest.TestCase):
    def setUp(self):
        self.clock=1000000.0
        self.calls=[]
        self.rows={'status':'working','id':'session-one','last_active':self.clock-3600,'message_count':15}
        self.used=55000
        self.st={}
        self.cfg=g.DEFAULT.copy()
        self.ctx=[]
        self.patches=[
            patch.object(g.time,'time', lambda:self.clock),
            patch.object(g,'log',lambda msg:self.ctx.append(msg)),
            patch.object(g,'notify', lambda msg:self.calls.append(('notify',msg))),
            patch.object(g,'backup_dbs',lambda:1),
            patch.object(g,'active_session',lambda port,token,sid:dict(self.rows)),
            patch.object(g,'state_write',lambda st:None),
            patch.object(g,'rpc',self.fake_rpc),
        ]
        for p in self.patches:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])
    def fake_rpc(self,port,token,method,params=None,timeout=None):
        self.calls.append((method,params))
        if method=='session.interrupt':
            return {'ok':True}
        if method=='session.compress':
            self.used=30000
            return {'status':'completed'}
        if method=='prompt.submit':
            return {'accepted':True}
        return {}
    def usage(self,pid,port,token,row):
        cap=65536
        return ('session-one',{'context_used':self.used,'context_max':cap,'active_subagents':0},self.used,cap,100*self.used/cap,0)
    def sample(self,delta=0):
        self.clock+=delta
        with patch.object(g,'usage_read',self.usage):
            g.handle(100,50001,'secret',dict(self.rows),self.st,self.cfg)
    def test_critical_working_interrupt_and_compact(self):
        self.used=56000  # 85.45%
        self.sample()
        self.assertFalse(any(c[0]=='session.interrupt' for c in self.calls))
        self.sample(30)
        self.assertEqual(sum(c[0]=='session.interrupt' for c in self.calls),1)
        self.sample(30)
        self.assertEqual(sum(c[0]=='session.interrupt' for c in self.calls),1)
        self.rows['status']='idle'
        self.sample(30)
        self.sample(30)
        self.assertEqual(sum(c[0]=='session.compress' for c in self.calls),1)
        self.sample(30)
        self.assertEqual(self.st['100:session-one']['phase'],'recovered')
        self.assertEqual(sum(c[0]=='prompt.submit' for c in self.calls),0)
    def test_auto_continue_one_prompt_only(self):
        self.cfg['auto_continue']=True
        self.used=61000
        self.sample()
        self.rows['status']='idle'
        self.sample(30)
        self.sample(30)
        self.sample(30)
        self.sample(30)
        self.sample(30)
        self.sample(30)
        self.assertEqual(sum(c[0]=='prompt.submit' for c in self.calls),1)
    def test_no_interrupt_below_threshold(self):
        self.used=40000
        self.sample()
        self.sample(40)
        self.assertEqual(sum(c[0]=='session.interrupt' for c in self.calls),0)
    def test_no_interrupt_when_subagents_active(self):
        self.used=61000
        def busy_usage(pid,port,token,row):
            s=list(self.usage(pid,port,token,row))
            s[-1]=2
            return tuple(s)
        with patch.object(g,'usage_read',busy_usage):
            g.handle(100,50001,'secret',dict(self.rows),self.st,self.cfg)
        self.assertEqual(sum(c[0]=='session.interrupt' for c in self.calls),0)
    def test_no_automatic_continuation_when_compression_ineffective(self):
        self.cfg['auto_continue']=True
        self.used=61000
        def no_compress(port,token,method,params=None,timeout=None):
            self.calls.append((method,params))
            return {'ok':True}
        with patch.object(g,'rpc',no_compress):
            self.sample()
            self.rows['status']='idle'
            self.sample(30)
            self.sample(30)
            self.sample(30)
            self.sample(700)
        self.assertEqual(sum(c[0]=='prompt.submit' for c in self.calls),0)
        self.assertEqual(self.st['100:session-one']['phase'],'failed')
    def test_idle_high_context_compaction_without_interrupt(self):
        self.used=59000
        self.rows['status']='idle'
        self.sample()
        self.sample(30)
        self.sample(30)
        self.assertEqual(sum(c[0]=='session.interrupt' for c in self.calls),0)
        self.assertEqual(sum(c[0]=='session.compress' for c in self.calls),1)

if __name__=='__main__':unittest.main()

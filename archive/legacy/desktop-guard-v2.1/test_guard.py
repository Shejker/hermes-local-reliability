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
        return ('session-one',{'context_used':self.used,'context_max':cap,'active_subagents':0,'_guard_high_confidence':True},self.used,cap,100*self.used/cap,0)
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

class ContextFallbackTests(unittest.TestCase):
    def test_usage_priority(self):
        def fake_rpc(port,token,method,params):
            if method == 'session.usage':
                return {'context_used': 40000, 'context_max': 65536, 'active_subagents': 0}
            raise AssertionError('Fallback should not run with valid usage')
        with patch.object(g,'rpc',fake_rpc):
            sid,usage,used,cap,pct,subs=g.usage_read(1,2,'token',{'id':'s','status':'working'})
        self.assertEqual(used,40000)
        self.assertTrue(usage['_guard_high_confidence'])
        self.assertEqual(subs,0)

    def test_fallback_breakdown_provider(self):
        def fake_rpc(port,token,method,params):
            if method == 'session.usage':
                return {'calls': 2, 'total': 45000}
            if method == 'session.context_breakdown':
                return {'context_used': 45000, 'context_max': 65536,
                        'context_source': 'provider_usage', 'context_estimated': False}
            raise AssertionError(method)
        with patch.object(g,'rpc',fake_rpc):
            _,u,used,cap,_,_=g.usage_read(1,2,'token',{'id':'s','status':'working'})
        self.assertEqual((used,cap),(45000,65536))
        self.assertEqual(u['_guard_evidence'],'session.context_breakdown')
        self.assertTrue(u['_guard_high_confidence'])

    def test_fallback_estimate_wont_interrupt(self):
        calls=[]
        def fake_rpc(port,token,method,params=None,timeout=None):
            calls.append(method)
            if method=='session.usage':
                return {'total': 1000}
            if method=='session.context_breakdown':
                return {'context_used': 62000,'context_max': 65536,
                        'context_source':'local_estimate','context_estimated':True}
            raise AssertionError(method)
        with patch.object(g,'rpc',fake_rpc),patch.object(g,'log',lambda *_:None),patch.object(g,'notify',lambda *_:None):
            st={}
            row={'id':'s','status':'working','message_count':50}
            g.handle(1,2,'token',row,st,g.DEFAULT.copy())
        self.assertNotIn('session.interrupt',calls)
        self.assertEqual(st['1:s'].get('phase','normal'),'normal')

    def test_unavailable_fails_closed(self):
        def fake_rpc(port,token,method,params):
            return {'calls':0} if method=='session.usage' else {'context_used':0,'context_max':0}
        with patch.object(g,'rpc',fake_rpc):
            with self.assertRaisesRegex(ValueError,'Context unavailable'):
                g.usage_read(1,2,'token',{'id':'s','status':'idle'})

if __name__=='__main__':unittest.main()

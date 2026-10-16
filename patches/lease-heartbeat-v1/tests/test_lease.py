#!/usr/bin/env python3
"""Isolated source-level + real SQLite regression tests; no Hermes imports or user DB."""
import ast
from contextlib import contextmanager
import copy
from pathlib import Path
import sqlite3
import threading
import time
import unittest
from typing import Any, Callable, Optional, TypeVar

ROOT=Path(__file__).resolve().parents[1]
T=TypeVar('T')

def isolated_method(path, cls, name, ns):
    tree=ast.parse(path.read_text())
    clas=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
    fn=copy.deepcopy(next(n for n in clas.body if isinstance(n,ast.FunctionDef) and n.name==name))
    fn.decorator_list=[]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(path),'exec'),ns)
    return ns[name]

ns={'contextmanager':contextmanager,'sqlite3':sqlite3,'Optional':Optional, 'time':time,
    'Callable':Callable,'T':T,'log_write_lock_holders':lambda *a:None,
    'is_sqlite_lock_error':lambda e: 'locked' in str(e).lower(),
    '_is_no_more_rows':lambda e: False,
    '_DISK_IO_ERROR_MARKER':'disk i/o error',
    'SessionCompressionInProgressError':type('SessionCompressionInProgressError',(Exception,),{}),
    'logger':type('Logger',(),{'warning':lambda *args,**kwargs:None})(),
}
tree=ast.parse((ROOT/'hermes_state.py').read_text())
helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_writer_mutex_with_optional_timeout')
exec(compile(ast.fix_missing_locations(ast.Module(body=[helper],type_ignores=[])), 'helper','exec'),ns)
write=isolated_method(ROOT/'hermes_state.py','SessionDB','_execute_write',ns)
refresh=isolated_method(ROOT/'hermes_state_compression.py','SessionCompressionMixin','refresh_session_turn_lease',ns)

class DB:
    _CHECKPOINT_EVERY_N_WRITES=100000
    _FTS_MERGE_EVERY_N_WRITES=100000
    _WRITE_PATIENCE_S=1.0
    _WRITE_RETRY_SLOW_AFTER_S=1
    def __init__(self):
        self._lock=threading.Lock()
        self._conn=sqlite3.connect(':memory:',check_same_thread=False)
        self._conn.execute('CREATE TABLE session_turn_leases(conversation_id TEXT PRIMARY KEY, holder TEXT NOT NULL, expires_at REAL NOT NULL)')
        self._conn.execute('INSERT INTO session_turn_leases VALUES(?,?,?)',('s','alice',time.time()+300))
        self._conn.commit()
        self._write_count=0
        self.db_path=Path('/nonexistent-diagnostic-test.db')
        self._db_corrupt=False
        self._conn_closed=False
        self._wal_active=False
    _execute_write=write
    refresh_session_turn_lease=refresh
    def _session_turn_lease_key_on_conn(self,con,sid): return sid
    def _raise_if_db_corrupt(self,storage): return
    def _raise_if_db_replaced(self): return
    def _try_wal_checkpoint(self): return
    def _try_incremental_merge_fts(self): return
    def _sleep_before_write_retry(self,deadline,patience):
        if time.monotonic()>=deadline:return False
        time.sleep(min(.004,max(0,deadline-time.monotonic())))
        return time.monotonic()<deadline
    def close(self): self._conn.close()

class LeaseTests(unittest.TestCase):
    def setUp(self):self.db=DB()
    def tearDown(self):self.db.close()
    def test_normal_writer_unchanged(self):
        self.assertEqual(self.db._execute_write(lambda c:c.execute('SELECT 42').fetchone()[0]),42)
    def test_owner_refresh_extends_lease(self):
        before=self.db._conn.execute('SELECT expires_at FROM session_turn_leases').fetchone()[0]
        self.assertTrue(self.db.refresh_session_turn_lease('s','alice',ttl_seconds=600,patience_s=.2))
        after=self.db._conn.execute('SELECT expires_at FROM session_turn_leases').fetchone()[0]
        self.assertGreater(after,before)
    def test_wrong_holder_cannot_renew_or_steal(self):
        self.assertFalse(self.db.refresh_session_turn_lease('s','bob',ttl_seconds=600,patience_s=.2))
        self.assertEqual(self.db._conn.execute('SELECT holder FROM session_turn_leases').fetchone()[0], 'alice')
    def test_missing_row_stays_missing(self):
        self.db._conn.execute('DELETE FROM session_turn_leases');self.db._conn.commit()
        self.assertFalse(self.db.refresh_session_turn_lease('s','alice',patience_s=.2))
        self.assertEqual(self.db._conn.execute('SELECT COUNT(*) FROM session_turn_leases').fetchone()[0],0)
    def test_python_mutex_contention_times_out_bounded(self):
        ready=threading.Event(); release=threading.Event()
        def hold():
            with self.db._lock:
                ready.set();release.wait(timeout=2)
        t=threading.Thread(target=hold,daemon=True);t.start();self.assertTrue(ready.wait(1))
        start=time.monotonic()
        try:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                self.db.refresh_session_turn_lease('s','alice',patience_s=.11)
            self.assertIn('database is locked',str(caught.exception))
            self.assertLess(time.monotonic()-start,.45)
        finally:release.set();t.join(timeout=1)
        self.assertTrue(self.db.refresh_session_turn_lease('s','alice',patience_s=.2))
    def test_mutex_does_not_get_bypassed(self):
        self.db._lock.acquire()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.db.refresh_session_turn_lease('s','alice',patience_s=.04)
        finally:self.db._lock.release()
        self.assertEqual(self.db._conn.execute('SELECT holder FROM session_turn_leases').fetchone()[0],'alice')
    def test_other_write_lock_remains_blocking_by_default(self):
        self.assertIsNone(self.db._execute_write.__kwdefaults__["thread_lock_timeout_s"])
    def test_refresh_uses_owner_fenced_sql(self):
        src=(ROOT/'hermes_state_compression.py').read_text()
        segment=src[src.index('    def refresh_session_turn_lease('):src.index('    def release_session_turn_lease(')]
        self.assertIn('WHERE conversation_id = ? AND holder = ?', segment)
        self.assertIn('thread_lock_timeout_s=2.0',segment)
    def test_agent_timer_uses_30_second_default(self):
        self.assertIn('"_session_turn_lease_refresh_interval", 30.0', (ROOT/'agent/turn_facade_lease.py').read_text())
    def test_no_new_db_connection_or_lease_reclaim(self):
        src=(ROOT/'hermes_state_compression.py').read_text()
        segment=src[src.index('    def refresh_session_turn_lease('):src.index('    def release_session_turn_lease(')]
        self.assertNotIn('sqlite3.connect',segment)
        self.assertNotIn('DELETE FROM session_turn_leases',segment)
    def test_holder_change_before_renewal_fails_closed(self):
        self.db._conn.execute("UPDATE session_turn_leases SET holder='bob'");self.db._conn.commit()
        self.assertFalse(self.db.refresh_session_turn_lease('s','alice',patience_s=.2))
    def test_sqlite_commit_atomic_after_owner_match(self):
        self.assertTrue(self.db.refresh_session_turn_lease('s','alice',patience_s=.2))
        self.assertEqual(self.db._conn.execute('SELECT COUNT(*) FROM session_turn_leases').fetchone()[0],1)

if __name__=='__main__':unittest.main(verbosity=2)

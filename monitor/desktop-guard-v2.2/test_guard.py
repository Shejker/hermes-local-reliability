#!/usr/bin/env python3
"""Regression tests for safe, non-mutating Hermes Desktop Guard v2.2."""
import ast
import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = load('guard_v22_under_test', 'desktop_guard_v22.py')
installer = load('guard_v22_installer_test', 'install_guard.py')


class ReadOnlyGuardTests(unittest.TestCase):
    def test_api_method_allowlist(self):
        self.assertEqual(guard.READ_ONLY_RPCS,
                         {'session.active_list', 'session.usage', 'session.context_breakdown'})
        for action in ('session.interrupt', 'session.compress', 'prompt.submit'):
            with self.assertRaises(PermissionError):
                guard.rpc(1234, 'dummy', action)

    def test_no_mutating_rpc_calls_in_source(self):
        tree = ast.parse((HERE / 'desktop_guard_v22.py').read_text())
        # The only rpc call sites should be for known read-only operations.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'rpc':
                if len(node.args) >= 3:
                    if isinstance(node.args[2], ast.Constant):
                        self.assertIn(node.args[2].value, guard.READ_ONLY_RPCS)
                    else:
                        self.fail('Dynamic RPC method call forbidden')

    def exercise(self, status, used, oldphase, check_only=False):
        st = {'99:s123': {'phase': oldphase, 'last_used': 50000,
                          'last_status': status, 'last_change_at': 1.0}}
        row = {'id': 's123', 'status': status, 'message_count': 22}
        policy = guard.DEFAULT.copy()
        # Deliberately unsafe legacy policy: must remain monitor-only.
        policy['auto_interrupt'] = True
        policy['auto_continue'] = True
        with mock.patch.object(guard, 'usage_read', return_value=(
                's123', used, 65536., used / 65536. * 100, True,
                'session.usage', 0)), mock.patch.object(
                guard, 'recent_compaction_evidence', return_value='started_recently'), mock.patch.object(
                guard, 'log'), mock.patch.object(guard, 'notify'), mock.patch.object(
                guard, 'rpc', side_effect=AssertionError('NO RPC mutations allowed')):
            guard.handle(99, 1234, 'token', row, st, policy, check_only=check_only)
        return st

    def test_working_92pct_does_not_interrupt_during_compaction(self):
        st = self.exercise('working', 60535, 'normal')
        self.assertEqual(st['99:s123']['phase'], 'monitor_only')

    def test_idle_high_context_never_starts_rescue(self):
        st = self.exercise('idle', 64000, 'ready_to_compress')
        self.assertEqual(st['99:s123']['phase'], 'monitor_only')

    def test_legacy_recovery_phase_cannot_resume_or_submit(self):
        for phase in ('interrupt_pending', 'compressing', 'verify_compression',
                      'ready_to_continue', 'continue_dispatched', 'failed'):
            st = self.exercise('idle', 38906, phase)
            self.assertEqual(st['99:s123']['phase'], 'monitor_only')

    def test_check_does_not_modify_state(self):
        st = self.exercise('working', 60535, 'ready_to_compress', check_only=True)
        self.assertEqual(st['99:s123']['phase'], 'ready_to_compress')

    def test_context_validation(self):
        self.assertIsNone(guard.valid_context({'context_used': True, 'context_max': 65536}))
        self.assertIsNone(guard.valid_context({'context_used': 600, 'context_max': 0}))
        self.assertEqual(guard.valid_context({'context_used': 500, 'context_max': 65536}), (500, 65536))

    def test_compaction_observation_not_authoritative(self):
        now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'agent.log'
            file.write_text(f'{now} INFO context compression started: session=abc messages=1\n')
            self.assertEqual(guard.recent_compaction_evidence('abc', file), 'started_recently')
            file.write_text(file.read_text() +
                            f'{now} INFO context compression done: session=abc messages=1\n')
            self.assertEqual(guard.recent_compaction_evidence('abc', file), 'not_seen_active')


class InstallTests(unittest.TestCase):
    def test_install_and_rollback_in_temp(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'desktop_guard.py'
            original = (HERE / 'desktop_guard_v21_original.py').read_bytes()
            target.write_bytes(original)
            self.assertEqual(installer.identify(target)[0], 'v2.1 source as supplied')
            self.assertIn('INSTALLED', installer.install(target))
            self.assertEqual(installer.identify(target)[0], 'v2.2 already installed')
            self.assertIn('ROLLED BACK', installer.rollback(target))
            self.assertEqual(target.read_bytes(), original)

    def test_refuses_unknown_local_modifications(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'desktop_guard.py'
            target.write_text('# local changes')
            with self.assertRaises(RuntimeError):
                installer.install(target)


if __name__ == '__main__':
    unittest.main(verbosity=2)

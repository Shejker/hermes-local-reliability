"""Isolated gate behavior tests; no Hermes modules or Ollama required."""
from __future__ import annotations
import importlib.util
import logging
import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

SOURCE = Path(__file__).resolve().parents[1] / 'agent' / 'turn_preflight_gate.py'

@dataclass
class Verdict:
    action: str = 'fallthrough'
    pending_moa_prepared_request: object = None
    messages: object = None
    active_system_prompt: object = None
    conversation_history: object = None
    api_call_count: int = 0
    compression_attempts: int = 0
    final_response: object = None
    failed: bool = False
    _turn_exit_reason: str = ''
    _compression_timeout_exhausted: bool = False
    _preflight_compression_blocked: bool = False
    _provider_overflow_recovery_pending: bool = False
    _last_preflight_pressure: object = None
    result: object = None


def load_gate():
    fake_agent = types.ModuleType('agent')
    fake_agent.__path__ = []
    metadata = types.ModuleType('agent.message_metadata')
    metadata.append_message = lambda messages, value: messages.append(value)
    context = types.ModuleType('agent.turn_context')
    context._compression_warrants_another_preflight_pass = lambda *args: True
    preflight = types.ModuleType('agent.turn_preflight')
    preflight.PreflightGateVerdict = Verdict
    preflight.run_preflight_compression = lambda agent, verdict, **kwargs: verdict
    loop = types.ModuleType('agent.conversation_loop')
    loop._ollama_context_limit_error = lambda *args: None
    sys.modules.update({
        'agent': fake_agent,
        'agent.message_metadata': metadata,
        'agent.turn_context': context,
        'agent.turn_preflight': preflight,
        'agent.conversation_loop': loop,
    })
    spec = importlib.util.spec_from_file_location('gate_under_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, preflight


class Tests(unittest.TestCase):
    def setUp(self):
        self.gate, self.preflight = load_gate()
        compressor = SimpleNamespace(
            threshold_tokens=40960, context_length=65536,
            get_active_compression_failure_cooldown=lambda: None,
            should_defer_preflight_to_real_usage=lambda _: False,
        )
        self.agent = SimpleNamespace(
            api_mode='chat_completions', base_url='http://127.0.0.1:11434/v1',
            context_compressor=compressor, _request_pressure_anchored=True,
            _api_call_count=3, iteration_budget=SimpleNamespace(refund=MagicMock()),
            _emit_diagnostic_status=MagicMock(),
        )
        self.messages = [{'role':'user','content':'continue'}]

    def call(self, tokens=55000, attempts=6):
        return self.gate.run_preflight_gate(
            self.agent, request_pressure_tokens=tokens, _moa_prepared_request=None,
            pending_moa_prepared_request=None, messages=self.messages,
            system_message={'role':'system','content':'x'}, user_message='continue',
            active_system_prompt='x', conversation_history=[], api_call_count=3,
            compression_attempts=attempts, max_compression_attempts=6,
            effective_task_id=None, final_response=None, failed=False,
            _turn_exit_reason='', _compression_timeout_exhausted=False,
            _preflight_compression_blocked=False,
            _provider_overflow_recovery_pending=False, _last_preflight_pressure=None,
        )

    def test_user_case_57565_blocked_before_ollama(self):
        with self.assertLogs('agent.conversation_loop', level='INFO') as logs:
            v = self.call(tokens=57565)
        self.assertEqual(v.action,'break')
        self.assertTrue(v.failed)
        self.assertEqual(v._turn_exit_reason,'context_runway_guard')
        self.assertEqual(v.api_call_count,2)
        self.assertEqual(self.agent._api_call_count,2)
        self.agent.iteration_budget.refund.assert_called_once()
        self.assertEqual(self.messages[-1]['role'],'assistant')
        self.assertIn('/compress',str(v.final_response))
        self.assertTrue(any('BLOCKED' in entry for entry in logs.output))
        self.assertTrue(any('attempts=6/6' in entry for entry in logs.output))

    def test_normal_call_not_blocked(self):
        self.assertEqual(self.call(tokens=55000).action,'fallthrough')
        self.assertEqual(len(self.messages),1)
        self.agent.iteration_budget.refund.assert_not_called()

    def test_expected_runway_boundary(self):
        self.assertEqual(self.gate._local_ollama_runway_limit(self.agent),57344)
        self.assertEqual(self.call(tokens=57343).action,'fallthrough')
        self.assertEqual(self.call(tokens=57344).action,'break')

    def test_unrelated_provider_unchanged(self):
        self.agent.base_url='https://api.openai.com/v1'
        self.assertIsNone(self.gate._local_ollama_runway_limit(self.agent))
        self.assertEqual(self.call(tokens=64500).action,'fallthrough')

    def test_other_api_mode_unchanged(self):
        self.agent.api_mode='responses'
        self.assertEqual(self.call(tokens=64500).action,'fallthrough')

    def test_no_valid_context_has_no_runway(self):
        self.agent.context_compressor.context_length=0
        self.assertEqual(self.call(tokens=65000).action,'fallthrough')

    def test_original_compression_continue_is_preserved(self):
        self.gate.run_preflight_compression=lambda agent, v, **kwargs: Verdict(**{**vars(v),'action':'continue'})
        self.assertEqual(self.call(tokens=64500,attempts=3).action,'continue')
        self.assertEqual(len(self.messages),1)

    def test_existing_provider_recovery_result_preserved(self):
        self.gate.run_preflight_compression=lambda agent, v, **kwargs: Verdict(**{**vars(v),'action':'return','result':{'status':'deferred'}})
        self.assertEqual(self.call(tokens=64500).action,'return')
        self.assertEqual(len(self.messages),1)

    def test_no_commands_dispatched(self):
        s=SOURCE.read_text()
        for prohibited in ('session.interrupt', 'session.compress', 'prompt.submit', 'sqlite3.connect'):
            self.assertNotIn(prohibited,s)

if __name__ == '__main__':
    unittest.main(verbosity=2)

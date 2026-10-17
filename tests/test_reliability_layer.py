from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch as mock_patch

from reliability_layer.rearm import (_make_upstream_fixture, classification, patch, function_under_test)
from reliability_layer.installer import active_processes, install, rollback, inspect, run_selftest
from reliability_layer.healthcheck import config_summary, compression_metrics, guard_health
from reliability_layer.config_reader import check_config
from datetime import datetime, timedelta


class RearmTests(unittest.TestCase):
    def test_semantic_source_detected(self):
        self.assertEqual(classification(_make_upstream_fixture()), "supported")

    def test_installed_detected(self):
        self.assertEqual(classification(patch(_make_upstream_fixture())), "installed")

    def test_incompatible_rejected(self):
        text = _make_upstream_fixture().replace("prompt_tokens < threshold_tokens", "prompt_tokens <= threshold_tokens")
        self.assertEqual(classification(text), "incompatible")
        with self.assertRaises(ValueError):
            patch(text)

    def test_refuse_double_apply(self):
        with self.assertRaises(ValueError):
            patch(patch(_make_upstream_fixture()))

    def test_missing_helper(self):
        self.assertEqual(classification("def x(): return 1\n"), "incompatible")

    def test_safe_predicate_matrix(self):
        fn = function_under_test(patch(_make_upstream_fixture()))
        for attempt in (0, 1, 6, 7):
            for pending in (False, True):
                for tokens in (0, 40959, 40960, 46979, 49152, 49153, 60000):
                    result = fn(attempt, completed_compaction_pending=pending,
                                prompt_tokens=tokens, threshold_tokens=40960)
                    self.assertEqual(result, bool(attempt and pending and 0 < tokens <= 49152))
        for threshold in (100, 20000, 50000):
            for used in (1, threshold-1, threshold, threshold+1):
                self.assertEqual(fn(3, completed_compaction_pending=True,
                                    prompt_tokens=used, threshold_tokens=threshold), used < threshold)

    def test_fake_marker_not_accepted(self):
        malicious = _make_upstream_fixture().replace("return bool(",
                     "# HERMES_RELIABILITY_VERIFIED_HEADROOM_V1\n    return bool(")
        self.assertEqual(classification(malicious), "incompatible")

    def test_cua_is_not_backend(self):
        process = '''15684 /Users/example/.hermes/tools/cua-driver-0.21.0-darwin-arm64/CuaDriver.app/Contents/MacOS/cua-driver serve --embedded
66058 /Users/example/.hermes/tools/cua-driver-0.21.0-darwin-arm64/CuaDriver.app/Contents/MacOS/cua-driver serve --embedded
'''
        self.assertEqual(active_processes(process), [])

    def test_hermes_processes_are_backend(self):
        process = '''7182 /Users/x/.hermes/hermes-agent/apps/desktop/release/mac-arm64/Hermes.app/Contents/MacOS/Hermes
7183 /Users/x/.hermes/bin/hermes serve --profile default
7184 /Users/x/.hermes/desktop-guard/venv/bin/python3 /Users/x/.hermes/desktop-guard/desktop_guard.py --once
'''
        self.assertEqual(active_processes(process), ["7182", "7183"])


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.root = d / "hermes-agent"
        (self.root / "agent").mkdir(parents=True)
        (self.root / "agent/conversation_loop.py").write_text(_make_upstream_fixture())
        (self.root / "agent/turn_context.py").write_text("""
from agent.conversation_loop import _should_rearm_compression_budget

def phase():
    return _should_rearm_compression_budget(7, completed_compaction_pending=True, prompt_tokens=46979, threshold_tokens=40960)
""")
        self.config = d / "config.yaml"
        self.config.write_text("""model:
  base_url: http://127.0.0.1:11434/v1
  context_length: 65536
  ollama_num_ctx: 65536
compression:
  enabled: true
  threshold_tokens: 40960
""")
        self.backups = d / "backups"
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "agent"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "fixture"], check=True)

    @mock_patch("reliability_layer.installer.live_processes", return_value=[])
    def test_install_and_rollback(self, _):
        self.assertTrue(inspect(self.root)["target_clean"])
        self.assertEqual(inspect(self.root)["state"], "supported")
        msg = install(self.root, self.backups, self.config)
        self.assertIn("INSTALLED", msg)
        self.assertEqual(inspect(self.root)["state"], "installed")
        run_selftest(self.root)
        self.assertIn("ROLLED BACK", rollback(self.root, self.backups))
        self.assertEqual(inspect(self.root)["state"], "supported")

    @mock_patch("reliability_layer.installer.live_processes", return_value=[])
    def test_refuse_config_mismatch(self, _):
        self.config.write_text(self.config.read_text().replace("40960", "50000"))
        with self.assertRaises(ValueError):
            install(self.root, self.backups, self.config)
        self.assertEqual(inspect(self.root)["state"], "supported")

    @mock_patch("reliability_layer.installer.live_processes", return_value=["9999"])
    def test_running_backend_prevents_install(self, _):
        with self.assertRaises(RuntimeError):
            install(self.root, self.backups, self.config)
        self.assertEqual(inspect(self.root)["state"], "supported")

    @mock_patch("reliability_layer.installer.live_processes", return_value=[])
    def test_changed_source_prevents_install(self, _):
        with (self.root / "agent/conversation_loop.py").open("a") as f:
            f.write("\n# another local modification\n")
        with self.assertRaises(RuntimeError):
            install(self.root, self.backups, self.config)

    @mock_patch("reliability_layer.installer.live_processes", return_value=[])
    def test_rollback_refuses_changed_source(self, _):
        install(self.root, self.backups, self.config)
        with (self.root / "agent/conversation_loop.py").open("a") as f:
            f.write("\n# unexpected edit\n")
        with self.assertRaises(RuntimeError):
            rollback(self.root, self.backups)

    @mock_patch("reliability_layer.installer.live_processes", return_value=[])
    def test_supports_new_release_when_semantics_unchanged(self, _):
        src = self.root / "agent/conversation_loop.py"
        src.write_text("# harmless upstream addition\n" + src.read_text())
        subprocess.run(["git", "-C", str(self.root), "add", "agent/conversation_loop.py"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "future fixture"], check=True)
        self.assertIn("INSTALLED", install(self.root, self.backups, self.config))

    @mock_patch("reliability_layer.installer.live_processes", return_value=[])
    def test_missing_call_site_refuses_install(self, _):
        (self.root / "agent/turn_context.py").write_text("def dummy(): return True\n")
        with self.assertRaisesRegex(RuntimeError, "No verified call site"):
            install(self.root, self.backups, self.config)


class MonitoringTests(unittest.TestCase):
    def test_config_reader_does_not_leak_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"config.yaml"
            p.write_text("model:\n  base_url: http://127.0.0.1:11434/v1\n  context_length: 65536\n  ollama_num_ctx: 65536\n  api_key: VERYSECRET\ncompression:\n  enabled: true\n  threshold_tokens: 40960\n")
            check_config(p)
            self.assertNotIn("VERYSECRET", str(config_summary(p)))

    def test_parse_telemetry_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            event = {"attempt_id": "abc", "commit_status": "committed", "tokens_reclaimed": 2657,
                     "trigger_source": "post_tool"}
            line = ts + " INFO context compression attempt telemetry: " + json.dumps(event) + "\n"
            (root / "agent.log").write_text(line)
            (root / "agent.log.1").write_text(line)
            stats = compression_metrics(root, datetime.now()-timedelta(hours=1))
            self.assertEqual(stats["attempts"], 1)
            self.assertEqual(stats["automatic"], 1)
            self.assertEqual(stats["reclaimed_tokens_sum"], 2657)

    def test_guard_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            h = Path(d)
            p = h / ".hermes/desktop-guard/desktop_guard.py"
            p.parent.mkdir(parents=True)
            p.write_text("pass\n")
            (p.parent / "guard.log").write_text(datetime.now().astimezone().isoformat() +
                " v2.2 mode=observe_only sid=123 status=working context=36372/65536 verified=False\n")
            data = guard_health(h, "")
            self.assertFalse(data["verified"])
            self.assertTrue(data["observe_only_seen"])


if __name__ == "__main__":
    unittest.main()

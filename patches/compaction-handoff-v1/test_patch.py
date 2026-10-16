#!/usr/bin/env python3
"""Tests for the exact one-line handoff fix; do not import Hermes dependencies."""
from __future__ import annotations

import ast
from pathlib import Path
import tempfile
import unittest
from patch_hermes import OLD, NEW, SIBLING_GUARD, validate_source, hash_bytes, atomic_write


def testable_source(condition: str) -> str:
    return f'''
_SUMMARY_END_MARKER = "[END OF CONTEXT]"
def _restated_request_text(content):
    return content
class Compressor:
    @classmethod
    def _has_merged_inflight_replay(cls, message):
        text = str(message)
        {SIBLING_GUARD}
        return bool(boundary)
    def _reappend_inflight_user_task(self, compressed, inflight):
        carrier_text = compressed[0]
        if _SUMMARY_END_MARKER not in carrier_text:
            return compressed
        {condition}
            return compressed
        compressed.append(inflight)
        return compressed
'''


class HandoffTests(unittest.TestCase):
    def test_regression_cases(self):
        source = testable_source(NEW)
        validate_source(source, expect_patch=True)
        ns = {}
        exec(compile(source, "<mock>", "exec"), ns)
        Compressor = ns["Compressor"]
        marker = ns["_SUMMARY_END_MARKER"]
        c = Compressor()
        cases = [
            ("summary " + marker, True),
            ("summary " + marker + "\n[STILL IN PROGRESS] task", False),
            ("old " + marker + " old task \n new " + marker, True),
            ("old " + marker + " old task \n new " + marker + " newest task", False),
            ("a " + marker + " b " + marker + " c " + marker, True),
        ]
        for carrier, should_append in cases:
            with self.subTest(carrier=carrier):
                result = c._reappend_inflight_user_task([carrier], "TASK")
                self.assertEqual("TASK" in result, should_append)

    def test_previous_bug(self):
        ns = {}
        exec(compile(testable_source(OLD), "<buggy>", "exec"), ns)
        marker = ns["_SUMMARY_END_MARKER"]
        carrier = "old " + marker + " old task \n new " + marker
        result = ns["Compressor"]()._reappend_inflight_user_task([carrier], "TASK")
        self.assertNotIn("TASK", result)

    def test_only_new_predicate(self):
        original = testable_source(OLD)
        updated = original.replace(OLD, NEW)
        validate_source(original, expect_patch=False)
        validate_source(updated, expect_patch=True)
        self.assertEqual(original.replace(OLD, NEW), updated)
        self.assertEqual(original.count(OLD), 1)

    def test_atomic_write_and_hash(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "source.py"
            p.write_bytes(b"old\n")
            old = hash_bytes(p.read_bytes())
            atomic_write(p, b"new\n")
            self.assertNotEqual(old, hash_bytes(p.read_bytes()))
            self.assertEqual(p.read_bytes(), b"new\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Offline isolated tests for cleanup_macos; no use of real user home."""
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / 'cleanup_macos.py'


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / 'Downloads' / 'BE').mkdir(parents=True)
        (self.home / 'Downloads' / 'BE' / 'valuable.txt').write_text('Do not touch')
        (self.home / '.hermes' / 'maintenance').mkdir(parents=True)

    def call(self, mode):
        p = subprocess.run([sys.executable, str(SCRIPT), mode, '--home', str(self.home)],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_dry_run_changes_nothing(self):
        item = self.home / 'Downloads/hermes-lease-heartbeat-v1'
        item.mkdir()
        (item / 'test.txt').write_text('alpha')
        self.call('--plan')
        self.assertTrue(item.exists())
        self.assertFalse((self.home / '.hermes/maintenance/hermes-lease-heartbeat-v1').exists())
        self.assertTrue((self.home / 'Downloads/BE/valuable.txt').exists())

    def test_apply_moves_known_dirs_and_zips_only(self):
        (self.home / 'Downloads/hermes-preflight-recovery-v2').mkdir()
        (self.home / 'Downloads/hermes-preflight-recovery-v2.zip').write_text('zip')
        (self.home / 'Downloads/other.txt').write_text('other')
        self.call('--apply')
        self.assertTrue((self.home / '.hermes/maintenance/hermes-preflight-recovery-v2').exists())
        self.assertTrue((self.home / '.hermes/maintenance/packages/hermes-preflight-recovery-v2.zip').exists())
        self.assertTrue((self.home / 'Downloads/BE/valuable.txt').exists())
        self.assertTrue((self.home / 'Downloads/other.txt').exists())

    def test_same_file_moves_to_trash(self):
        p = self.home / 'Downloads/hermes-lease-heartbeat-v1.zip'
        p.write_text('same')
        q = self.home / '.hermes/maintenance/packages/hermes-lease-heartbeat-v1.zip'
        q.parent.mkdir()
        q.write_text('same')
        self.call('--apply')
        self.assertFalse(p.exists())
        self.assertEqual(q.read_text(), 'same')
        self.assertEqual(len(list((self.home / '.Trash').rglob('hermes-lease-heartbeat-v1.zip'))), 1)

    def test_conflicting_file_is_preserved(self):
        p = self.home / 'Downloads/hermes-lease-heartbeat-v1.zip'
        p.write_text('new')
        q = self.home / '.hermes/maintenance/packages/hermes-lease-heartbeat-v1.zip'
        q.parent.mkdir()
        q.write_text('old')
        self.call('--apply')
        self.assertEqual(q.read_text(), 'old')
        self.assertFalse(p.exists())
        self.assertTrue((self.home / '.hermes/maintenance/incoming-conflicts/hermes-lease-heartbeat-v1.zip').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)

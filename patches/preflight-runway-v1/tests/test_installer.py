from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PKG = Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('installer_under_test', PKG/'install.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class InstallerTests(unittest.TestCase):
    def test_installs_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            target=root/'hermes-agent'/'agent'/'turn_preflight_gate.py'
            target.parent.mkdir(parents=True)
            target.write_bytes((PKG/'tests'/'fixtures'/'turn_preflight_gate.py.original').read_bytes())
            with patch.object(m,'TARGET',target), patch.object(m,'STATE',root/'backups'), patch.object(m,'MANIFEST',root/'backups'/'installed.json'),patch.object(m,'hermes_is_running',return_value=False):
                self.assertTrue(m.check())
                m.install()
                self.assertEqual(m.digest(target),m.digest(m.PAYLOAD))
                m.rollback()
                self.assertEqual(m.digest(target),m.EXPECTED_SHA)

    def test_unknown_local_source_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            target=root/'turn_preflight_gate.py'
            target.write_text('# changed')
            with patch.object(m,'TARGET',target), patch.object(m,'STATE',root/'backups'),patch.object(m,'MANIFEST',root/'backups'/'installed.json'),patch.object(m,'hermes_is_running',return_value=False):
                with self.assertRaisesRegex(RuntimeError,'differs'):
                    m.install()
                self.assertFalse((root/'backups').exists())

    def test_running_desktop_refused(self):
        with patch.object(m,'hermes_is_running',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'still running'):
                m.install()

if __name__=='__main__':
    unittest.main(verbosity=2)

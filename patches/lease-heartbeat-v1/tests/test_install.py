#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('lease_heartbeat_installer',ROOT/'install.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'hermes-agent';self.backups=Path(self.tmp.name)/'backups'
        self.root.mkdir()
        source={'hermes_state.py':ROOT/'tests/fixtures/hermes_state.py',
                'hermes_state_compression.py':ROOT/'tests/fixtures/hermes_state_compression.py',
                'agent/turn_facade_lease.py':ROOT/'tests/fixtures/agent/turn_facade_lease.py'}
        # Fixture uses the unpatched source recorded by the supplied local archives.
        for rel,path in source.items():
            dst=self.root/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dst)
            self.assertEqual(installer.digest(dst),installer.ORIGINAL[rel])
    def test_installs_and_rolls_back(self):
        with patch.object(installer,'running_desktop',return_value=False):
            installer.transact('install',self.root,self.backups)
            for rel in installer.REL:self.assertEqual(installer.digest(self.root/rel),installer.PATCH[rel])
            self.assertTrue((self.backups/'installed.json').exists())
            installer.transact('rollback',self.root,self.backups)
            for rel in installer.REL:self.assertEqual(installer.digest(self.root/rel),installer.ORIGINAL[rel])
            self.assertFalse((self.backups/'installed.json').exists())
    def test_refuses_changed_file(self):
        (self.root/'hermes_state_compression.py').write_text('custom modifications')
        with patch.object(installer,'running_desktop',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'checksum mismatch'):
                installer.transact('install',self.root,self.backups)
    def test_refuses_active_hermes(self):
        with patch.object(installer,'running_desktop',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'still running'):
                installer.transact('install',self.root,self.backups)
    def test_refuses_double_install(self):
        with patch.object(installer,'running_desktop',return_value=False):
            installer.transact('install',self.root,self.backups)
            with self.assertRaisesRegex(RuntimeError,'already exists'):
                installer.transact('install',self.root,self.backups)
    def test_preflight_v2_files_not_touched(self):
        sentinel=self.root/'agent'/'turn_preflight_gate.py'
        sentinel.write_text('user patch!')
        with patch.object(installer,'running_desktop',return_value=False):
            installer.transact('install',self.root,self.backups)
        self.assertEqual(sentinel.read_text(),'user patch!')
if __name__=='__main__':unittest.main(verbosity=2)

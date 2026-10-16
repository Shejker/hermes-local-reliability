"""Installer validation in a temporary tree; never modifies a real Hermes home."""
import importlib.util
from pathlib import Path
from unittest import mock
import tempfile
import shutil
import sys
import unittest

INSTALL=Path(__file__).resolve().parents[1]/'install.py'
spec=importlib.util.spec_from_file_location('patch_installer',INSTALL)
inst=importlib.util.module_from_spec(spec)
spec.loader.exec_module(inst)
FIXTURES=Path(__file__).resolve().parent/'fixtures'
V1=FIXTURES/'turn_preflight_gate.py.original'
CONV=FIXTURES/'conversation_loop.py.original'

class InstallerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)/'hermes-agent'
  self.backups=Path(self.tmp.name)/'backups'
  (self.root/'agent').mkdir(parents=True)
  shutil.copy2(V1,self.root/'agent/turn_preflight_gate.py')
  shutil.copy2(CONV,self.root/'agent/conversation_loop.py')

 @mock.patch.object(inst,'running_desktop',return_value=False)
 def test_install_then_rollback(self,*_):
  inst.transact('check',self.root,self.backups)
  inst.transact('install',self.root,self.backups)
  self.assertTrue((self.backups/'installed.json').exists())
  for rel in inst.REL:self.assertEqual(inst.digest(self.root/rel),inst.PATCH[rel])
  inst.transact('rollback',self.root,self.backups)
  for rel in inst.REL:self.assertEqual(inst.digest(self.root/rel),inst.ORIGINAL[rel])
  self.assertFalse((self.backups/'installed.json').exists())

 @mock.patch.object(inst,'running_desktop',return_value=True)
 def test_running_desktop_refused(self,*_):
  with self.assertRaisesRegex(RuntimeError,'running'):
   inst.transact('install',self.root,self.backups)

 @mock.patch.object(inst,'running_desktop',return_value=False)
 def test_refuses_unknown_modification(self,*_):
  (self.root/'agent/conversation_loop.py').write_text('modified')
  with self.assertRaisesRegex(RuntimeError,'checksum mismatch'):
   inst.transact('install',self.root,self.backups)

 @mock.patch.object(inst,'running_desktop',return_value=False)
 def test_refuses_double_install(self,*_):
  inst.transact('install',self.root,self.backups)
  with self.assertRaisesRegex(RuntimeError,'manifest'):
   inst.transact('install',self.root,self.backups)

if __name__=='__main__':unittest.main(verbosity=2)

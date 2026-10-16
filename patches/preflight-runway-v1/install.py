#!/usr/bin/env python3
"""Exact-source, opt-in installer/rollback for the local Hermes pre-API runway guard.

Never touches config.yaml, Hermes state.db, desktop guard, launchd, or running processes.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path.home() / '.hermes'
PACKAGE = Path(__file__).resolve().parent
TARGET = ROOT / 'hermes-agent' / 'agent' / 'turn_preflight_gate.py'
PAYLOAD = PACKAGE / 'agent' / 'turn_preflight_gate.py'
STATE = ROOT / 'patch-backups' / 'preflight-runway-v1'
MANIFEST = STATE / 'installed.json'
EXPECTED_SHA = '027d73522e0a5a645d2f0a7b9e9161f5adec3c26d1c23650ea84166d1e3337da'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hermes_is_running():
    try:
        out = subprocess.run(['/bin/ps', '-axo', 'pid=,command='], capture_output=True,
                             text=True, check=True, timeout=7).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # If we cannot determine it, do not risk a live patch.
        return True
    return any(re.search(r'^\s*\d+\s+\S*/Hermes\.app/Contents/MacOS/Hermes(?:\s|$)', l)
               for l in out.splitlines())


def replace_file(src, dest):
    """Atomic same-directory replacement, preserving existing file permissions."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix='.preflight-v1-', suffix='.tmp', dir=dest.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(src.read_bytes())
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_name, dest.stat().st_mode & 0o777)
        os.replace(temp_name, dest)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def check():
    if not TARGET.is_file():
        raise RuntimeError('Target not found: '+str(TARGET))
    if not PAYLOAD.is_file():
        raise RuntimeError('Patch payload not found')
    current, patch = digest(TARGET), digest(PAYLOAD)
    print('Hermes source:', TARGET)
    print('Original SHA256:', EXPECTED_SHA)
    print('Installed SHA256:', current)
    print('Patch SHA256:', patch)
    print('Hermes Desktop running:', hermes_is_running())
    print('Source state:', 'unpatched / exact match' if current == EXPECTED_SHA
          else 'patch already installed' if current == patch else 'UNKNOWN: NOT SAFE TO INSTALL')
    if MANIFEST.is_file():
        print('Rollback metadata:', MANIFEST)
    return current in (EXPECTED_SHA, patch)


def install():
    if hermes_is_running():
        raise RuntimeError('Hermes Desktop is still running. Close it after saving your work, then retry.')
    if not TARGET.is_file() or digest(TARGET) != EXPECTED_SHA:
        raise RuntimeError('Local source SHA256 differs from supplied source; refusing to overwrite.')
    if not PAYLOAD.is_file():
        raise RuntimeError('Patch payload missing.')
    compile(PAYLOAD.read_text(encoding='utf8'), str(PAYLOAD), 'exec')
    if MANIFEST.exists():
        raise RuntimeError('Installation record already exists; check/rollback first.')
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = STATE / stamp / 'turn_preflight_gate.py.original'
    backup.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(TARGET, backup)
    replace_file(PAYLOAD, TARGET)
    manifest = {
        'backup': str(backup), 'target': str(TARGET),
        'original_sha256': EXPECTED_SHA, 'patched_sha256': digest(PAYLOAD),
        'installed_at': dt.datetime.now().isoformat(timespec='seconds'),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf8')
    os.chmod(MANIFEST, 0o600)
    print('INSTALLED local pre-API runway guard v1')
    print('Backup:', backup)
    print('No processes restarted and no session data modified.')


def rollback():
    if hermes_is_running():
        raise RuntimeError('Hermes Desktop is running. Stop it before rollback.')
    if not MANIFEST.is_file():
        raise RuntimeError('No installation metadata found; refusing blind rollback.')
    metadata = json.loads(MANIFEST.read_text(encoding='utf8'))
    backup = Path(metadata['backup'])
    if metadata.get('target') != str(TARGET):
        raise RuntimeError('Manifest target mismatch')
    if not backup.is_file() or digest(backup) != EXPECTED_SHA:
        raise RuntimeError('Original backup missing or changed; refusing rollback.')
    if not TARGET.is_file() or digest(TARGET) != metadata['patched_sha256']:
        raise RuntimeError('Current file was modified since install; refusing to discard changes.')
    replace_file(backup, TARGET)
    MANIFEST.unlink()
    print('ROLLED BACK to the original file:', TARGET)


def main():
    a=argparse.ArgumentParser()
    g=a.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true')
    g.add_argument('--install', action='store_true')
    g.add_argument('--rollback', action='store_true')
    ns=a.parse_args()
    try:
        if ns.check:
            if not check():
                raise SystemExit(2)
        elif ns.install:
            install()
        elif ns.rollback:
            rollback()
    except (OSError, ValueError, RuntimeError) as e:
        raise SystemExit('REFUSED: '+str(e)) from e


if __name__ == '__main__':
    main()

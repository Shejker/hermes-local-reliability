#!/usr/bin/env python3
"""Checked and reversible deployment of Hermes Desktop Guard v2.2."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import py_compile
import shutil
import stat
import tempfile
import time

BASE_DIR = Path(__file__).resolve().parent
SOURCE = BASE_DIR / 'desktop_guard_v22.py'
TARGET = Path.home() / '.hermes/desktop-guard/desktop_guard.py'
ORIGINAL_SHA = 'c14482e7adbaea850328fa7f87cb0e4da94ce1880a7c8aa463406027e208b794'
BACKUP_SUBDIR = 'source-backups-v22'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_replace(path, data, perms):
    fd, temp = tempfile.mkstemp(prefix='.desktop_guard.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, perms)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def lock_guard(target, timeout=20):
    path = target.parent / 'guard.lock'
    path.touch(exist_ok=True)
    f = path.open('a+')
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Guard cycle still active; retry when it finishes')
                time.sleep(0.2)
        return f
    except Exception:
        f.close()
        raise


def identify(target):
    if not target.is_file() or target.is_symlink():
        raise RuntimeError(f'Expected regular file (not symlink): {target}')
    current = sha(target.read_bytes())
    patched = sha(SOURCE.read_bytes())
    if current == ORIGINAL_SHA:
        return 'v2.1 source as supplied', current
    if current == patched:
        return 'v2.2 already installed', current
    raise RuntimeError('Unknown local modifications; refusing to overwrite: sha256=' + current)


def install(target=TARGET):
    status, _ = identify(target)
    if status == 'v2.2 already installed':
        return 'Already installed; no changes'
    py_compile.compile(str(SOURCE), doraise=True)
    new = SOURCE.read_bytes()
    old = target.read_bytes()
    perms = stat.S_IMODE(target.stat().st_mode)
    lock = lock_guard(target)
    try:
        # Recheck under lock. Old background process can still exist in memory;
        # we only replace the script for future LaunchAgent invocations.
        status, _ = identify(target)
        if status == 'v2.2 already installed':
            return 'Already installed; no changes'
        folder = target.parent / BACKUP_SUBDIR / dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        folder.mkdir(parents=True, mode=0o700, exist_ok=False)
        backup = folder / 'desktop_guard.py.original'
        backup.write_bytes(old)
        backup.chmod(0o600)
        manifest = folder / 'manifest.json'
        manifest.write_text(json.dumps({
            'target': str(target), 'original_sha256': sha(old),
            'installed_sha256': sha(new), 'backup': str(backup),
        }, indent=2) + '\n', encoding='utf8')
        manifest.chmod(0o600)
        atomic_replace(target, new, perms)
        if sha(target.read_bytes()) != sha(new):
            raise RuntimeError('Verification failed after installation; original backup at ' + str(backup))
        return ('INSTALLED v2.2 observe-only\nBACKUP: ' + str(backup) +
                '\nPolicy and Hermes configuration unchanged. Next LaunchAgent invocation loads new code.')
    finally:
        lock.close()


def rollback(target=TARGET):
    if not target.is_file() or target.is_symlink():
        raise RuntimeError('Target is missing or a symlink')
    folder = target.parent / BACKUP_SUBDIR
    manifests = sorted(folder.glob('*/manifest.json'), reverse=True)
    if not manifests:
        raise RuntimeError('No verified backup manifest for rollback')
    data = json.loads(manifests[0].read_text('utf8'))
    backup = Path(data['backup'])
    if data.get('target') != str(target) or not backup.is_file():
        raise RuntimeError('Backup manifest not valid for target')
    original = backup.read_bytes()
    if sha(original) != ORIGINAL_SHA or sha(original) != data['original_sha256']:
        raise RuntimeError('Backup checksum mismatch')
    if sha(target.read_bytes()) != data['installed_sha256']:
        raise RuntimeError('Target was changed after patch; manual review required')
    perms = stat.S_IMODE(target.stat().st_mode)
    lock = lock_guard(target)
    try:
        if sha(target.read_bytes()) != data['installed_sha256']:
            raise RuntimeError('Target changed while waiting for lock')
        atomic_replace(target, original, perms)
        return 'ROLLED BACK to v2.1 source; policy was not changed.'
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--check', action='store_true')
    selection.add_argument('--install', action='store_true')
    selection.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    try:
        if args.check:
            state, digest = identify(TARGET)
            print('OK:', state, '| sha256:', digest)
            print('v2.2 is observe-only: no auto interrupt, compression, or continuation')
        elif args.install:
            print(install())
        else:
            print(rollback())
    except Exception as exc:
        parser.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()

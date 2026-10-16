#!/usr/bin/env python3
"""Read-only snapshot of known Hermes source paths before an upstream update.

Archives code and a Git diff only; never copies credentials or live runtime data.
Treat the output as potentially private until manually inspected.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

H = Path.home()
CHECKOUT = H / '.hermes/hermes-agent'
FILES = (
    'agent/context_compressor.py',
    'agent/turn_preflight_gate.py',
    'agent/conversation_loop.py',
    'agent/turn_facade_lease.py',
    'hermes_state.py',
    'hermes_state_compression.py',
)


def git(*args):
    try:
        proc = subprocess.run(['git', *args], cwd=CHECKOUT, capture_output=True,
                              timeout=20, check=False)
        if proc.returncode == 0:
            return proc.stdout.decode('utf8', errors='replace')
    except (OSError, subprocess.TimeoutExpired):
        pass
    return '(unavailable)\n'


def main():
    if not CHECKOUT.is_dir():
        raise SystemExit(f'Hermes checkout not found: {CHECKOUT}')
    now = dt.datetime.now().astimezone()
    desktop = H / 'Desktop'
    if not desktop.is_dir():
        desktop = H
    target = desktop / f'hermes-source-before-update-{now:%Y%m%d-%H%M%S}.zip'
    status = {'timestamp': now.isoformat(), 'checkout': str(CHECKOUT),
              'git_head': git('rev-parse', 'HEAD').strip(), 'source_sha256': {}}
    with ZipFile(target, 'x', ZIP_DEFLATED) as z:
        for relative in FILES:
            p = CHECKOUT / relative
            if p.is_symlink():
                raise SystemExit(f'Refusing symlink: {p}')
            if not p.is_file():
                print(f'SKIP missing: {relative}')
                continue
            data = p.read_bytes()
            status['source_sha256'][relative] = hashlib.sha256(data).hexdigest()
            z.writestr(f'files/{relative}', data)
        z.writestr('git/status.txt', git('status', '--short'))
        z.writestr('git/diff.patch', git('diff', '--binary'))
        z.writestr('git/stash-list.txt', git('stash', 'list'))
        z.writestr('manifest.json', json.dumps(status, indent=2))
    print('Source-only snapshot:', target)
    print('IMPORTANT: review the snapshot before sharing; it contains local source code and Git diff.')
    print('It is NOT a backup of session data, sqlite databases, installed manifest files or secrets.')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Read-only local Hermes install inspection for the pinned v0.21.7 patch set."""
from __future__ import annotations
import hashlib
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
CHECKOUT = HOME / '.hermes' / 'hermes-agent'

SOURCES = {
    'Preflight Recovery v2 / gate': (
        ROOT / 'patches/preflight-recovery-v2/agent/turn_preflight_gate.py',
        CHECKOUT / 'agent/turn_preflight_gate.py',
    ),
    'Preflight Recovery v2 / conversation loop': (
        ROOT / 'patches/preflight-recovery-v2/agent/conversation_loop.py',
        CHECKOUT / 'agent/conversation_loop.py',
    ),
    'Lease Heartbeat v1 / state': (
        ROOT / 'patches/lease-heartbeat-v1/hermes_state.py',
        CHECKOUT / 'hermes_state.py',
    ),
    'Lease Heartbeat v1 / state compression': (
        ROOT / 'patches/lease-heartbeat-v1/hermes_state_compression.py',
        CHECKOUT / 'hermes_state_compression.py',
    ),
    'Lease Heartbeat v1 / lease facade': (
        ROOT / 'patches/lease-heartbeat-v1/agent/turn_facade_lease.py',
        CHECKOUT / 'agent/turn_facade_lease.py',
    ),
    'Desktop Guard v2.2 (observe only)': (
        ROOT / 'monitor/desktop-guard-v2.2/desktop_guard_v22.py',
        HOME / '.hermes/desktop-guard/desktop_guard.py',
    ),
}


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def cmd(*args: str):
    try:
        p = subprocess.run(args, cwd=CHECKOUT, capture_output=True, text=True,
                           timeout=8, check=False)
        return p.stdout.strip() if p.returncode == 0 else '(unavailable)'
    except (OSError, subprocess.TimeoutExpired):
        return '(unavailable)'


def main():
    print('Hermes local patch audit — READ ONLY')
    print('Checkout:', CHECKOUT)
    if (CHECKOUT / '.git').exists():
        print('Git HEAD:', cmd('git', 'rev-parse', 'HEAD'))
        print('Git status:')
        print(cmd('git', 'status', '--short') or '(clean)')
        print()
    for label, (source, installed) in SOURCES.items():
        expected, got = sha(source), sha(installed)
        status = ('MATCH' if expected and got == expected else
                  'MISSING' if got is None else 'DIFFERENT / needs inspection')
        print(f'{label}: {status}')
        print(f'  installed: {installed}')
        if got: print(f'  actual SHA256:   {got}')
        if expected: print(f'  expected SHA256: {expected}')
    comp = CHECKOUT / 'agent/context_compressor.py'
    if comp.is_file():
        source = comp.read_text(errors='replace')
        old = 'carrier_text.split(_SUMMARY_END_MARKER, 1)[1].strip()'
        new = 'carrier_text.rsplit(_SUMMARY_END_MARKER, 1)[1].strip()'
        print('\nCompaction handoff:')
        print('  rsplit marker:', 'PRESENT' if new in source else 'not found')
        print('  old split marker:', 'PRESENT' if old in source and new not in source else 'not found')
        print('  Note: text check is NOT proof of patch semantic compatibility.')
    print('\nNo live session, database, config, or processes were changed.')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Safely organize known Hermes Downloads and preserve Hermes patch sources.

Default is preview only; --apply moves identified items, never deletes anything.
No live installation, backend, database, user projects or patch backups touched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
from pathlib import Path
import shutil
import sys

PACKAGES = (
    'hermes-compaction-handoff-fix-v1',
    'hermes-desktop-guard-v2.2',
    'hermes-preflight-runway-v1',
    'hermes-preflight-recovery-v2',
    'hermes-lease-heartbeat-v1',
    'hermes-config-clean.yaml',
    'hermes-morning-report.py',
)
ZIPS = (
    'hermes-compaction-handoff-fix-v1-macos.zip',
    'hermes-desktop-guard-v2.2-macos.zip',
    'hermes-preflight-runway-v1.zip',
    'hermes-preflight-recovery-v2.zip',
    'hermes-lease-heartbeat-v1.zip',
)


def hash_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def digest(p: Path) -> tuple:
    """Use content hashes; ignore Finder/cache-only files for directory comparison."""
    if p.is_file():
        return ('file', hash_file(p))
    if p.is_dir():
        entries = []
        for q in sorted(p.rglob('*')):
            rel = q.relative_to(p)
            if '__pycache__' in rel.parts or q.name in ('.DS_Store', '.localized'):
                continue
            if q.is_symlink():
                raise ValueError(f'Symlink inside folder; cannot compare safely: {q}')
            if q.is_file():
                entries.append((str(rel), hash_file(q)))
        return ('folder', tuple(entries))
    raise ValueError(f'Not a regular file/directory: {p}')


def safe_destination(root: Path, name: str) -> Path:
    candidate = root / name
    if not candidate.exists():
        return candidate
    number = 2
    while True:
        candidate = root / f'{name}.conflict-{number}'
        if not candidate.exists():
            return candidate
        number += 1


def act(src: Path, dst: Path, *, apply: bool, trash: Path, incoming: Path) -> None:
    if not src.exists():
        return
    if src.is_symlink() or dst.is_symlink() or src == dst:
        raise ValueError(f'Unsupported or symlinked source/destination: {src} -> {dst}')
    if not dst.exists():
        print(f'KEEP (move)   {src}  ->  {dst}')
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        return
    if digest(src) == digest(dst):
        candidate = safe_destination(trash, src.name)
        print(f'DUPLICATE     {src}  ->  Trash/{candidate.name} (same content already preserved)')
        if apply:
            trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(candidate))
    else:
        candidate = safe_destination(incoming, src.name)
        print(f'CONFLICT      {src}  ->  {candidate} (both versions kept)')
        if apply:
            incoming.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(candidate))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--plan', action='store_true', help='Show a read-only preview (default)')
    mode.add_argument('--apply', action='store_true', help='Perform only the listed moves')
    ap.add_argument('--home', type=Path, default=Path.home(),
                    help='Override home for isolated tests; defaults to current user')
    args = ap.parse_args()
    home = args.home.resolve()
    downloads = home / 'Downloads'
    base = home / '.hermes' / 'maintenance'
    trash = home / '.Trash' / f'hermes-duplicates-{dt.datetime.now():%Y%m%d-%H%M%S}'
    incoming = base / 'incoming-conflicts'
    if not downloads.is_dir() or downloads.is_symlink():
        sys.exit(f'Refusing: Downloads missing or symlinked: {downloads}')
    if base.is_symlink() or (base.exists() and not base.is_dir()):
        sys.exit(f'Refusing: unsafe maintenance directory: {base}')
    print('MODE:', 'APPLY' if args.apply else 'PLAN ONLY (no modifications)')
    print('Maintenance:', base)
    print('Excluded: Downloads/BE, live Hermes checkout, database, patch-backups, logs')
    print()
    for name in PACKAGES:
        act(downloads / name, base / name, apply=args.apply, trash=trash, incoming=incoming)
    for name in ZIPS:
        act(downloads / name, base / 'packages' / name,
            apply=args.apply, trash=trash, incoming=incoming)
    print('\nDONE. The BE folder was not accessed.')
    if args.apply:
        print('Original source directories now live at: ', base)
        print('Review any incoming-conflicts or Trash files before permanently deleting.')


if __name__ == '__main__':
    main()

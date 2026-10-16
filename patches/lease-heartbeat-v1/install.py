#!/usr/bin/env python3
"""Version-fenced reversible install; never writes to a running Hermes backend."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

PACKAGE = Path(__file__).resolve().parent
REL = ('hermes_state.py', 'hermes_state_compression.py', 'agent/turn_facade_lease.py')
ORIGINAL = {'hermes_state.py': 'b76fc6af3b7f37ee1de1be62379acb08d56069796eaf3bdf253a480be704e578', 'hermes_state_compression.py': 'c87bc6345599025e60527428cfddabf9dea8b4584c06e833cb0a2463b954b05c', 'agent/turn_facade_lease.py': '2e898cc9a529107ec3ce3d30be56eb5e0ad1a0d7fb3869c4208d38341cb2a9fa'}

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

PATCH = {rel: digest(PACKAGE/rel) for rel in REL}

def running_desktop() -> bool:
    cp=subprocess.run(['/bin/ps','-axo','pid=,command=','-ww'],capture_output=True,
                      text=True,timeout=8,check=True)
    me=os.getpid()
    for line in cp.stdout.splitlines():
        parts=line.strip().split(None,1)
        if len(parts)<2 or not parts[0].isdigit() or int(parts[0])==me:
            continue
        cmd=parts[1]
        if ('Hermes.app/Contents/MacOS/Hermes' in cmd
            or ('python' in cmd and ' serve --host ' in cmd and '.hermes' in cmd)):
            return True
    return False

def atomic_copy(src:Path,dst:Path) -> None:
    temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=dst.parent,prefix='.lease-update-',delete=False) as f:
            temp=Path(f.name)
            f.write(src.read_bytes())
            f.flush();os.fsync(f.fileno())
        os.chmod(temp,dst.stat().st_mode & 0o777)
        os.replace(temp,dst)
    finally:
        if temp is not None and temp.exists():temp.unlink()

def transact(mode:str,root:Path,backups:Path) -> None:
    targets={rel:root/rel for rel in REL}
    for rel,p in targets.items():
        if not p.is_file() or p.is_symlink():
            raise RuntimeError(f'Missing or symlink source: {p}')
    values={rel:digest(p) for rel,p in targets.items()}
    manifest=backups/'installed.json'
    if mode=='check':
        for rel in REL:
            s='original' if values[rel]==ORIGINAL[rel] else 'patched' if values[rel]==PATCH[rel] else 'UNKNOWN'
            print(f'{rel}: {s} sha256={values[rel]}')
        print('Manifest:', 'present' if manifest.exists() else 'absent')
        return
    print('Checking Hermes processes...',flush=True)
    if running_desktop():
        raise RuntimeError('Hermes Desktop/backend still running. Quit fully before updating source.')
    if mode=='install':
        if manifest.exists():
            raise RuntimeError('Install manifest already exists; inspect/rollback first')
        for rel in REL:
            if values[rel]!=ORIGINAL[rel]:
                raise RuntimeError(f'Source checksum mismatch: {rel} is {values[rel]}. No changes made.')
            py_compile.compile(str(PACKAGE/rel),doraise=True)
        stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        folder=backups/stamp
        folder.mkdir(parents=True,exist_ok=False)
        for rel in REL:
            dest=folder/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(targets[rel],dest)
            if digest(dest)!=ORIGINAL[rel]:raise RuntimeError(f'Backup mismatch: {rel}')
        modified=[]
        try:
            for rel in REL:
                atomic_copy(PACKAGE/rel,targets[rel]);modified.append(rel)
                if digest(targets[rel])!=PATCH[rel]:raise RuntimeError(f'Patch verification failed: {rel}')
            obj={'backup_dir':str(folder),'target_dir':str(root),
                 'installed_at':datetime.now().isoformat(),
                 'original_sha256':ORIGINAL,'patched_sha256':PATCH}
            with tempfile.NamedTemporaryFile('w',dir=backups,prefix='.manifest-',delete=False) as f:
                tm=Path(f.name)
                json.dump(obj,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tm,manifest)
        except BaseException:
            for rel in reversed(modified):atomic_copy(folder/rel,targets[rel])
            raise
        print('INSTALLED: Hermes Lease Heartbeat v1 (bounded local lock wait, 30s renewal)')
        print('BACKUP:',folder)
        print('Rollback:',f'python3 {PACKAGE/"install.py"} --rollback')
    elif mode=='rollback':
        if not manifest.exists():raise RuntimeError('No install manifest')
        obj=json.loads(manifest.read_text())
        if obj.get('target_dir')!=str(root):raise RuntimeError('Install root mismatch')
        folder=Path(obj['backup_dir'])
        for rel in REL:
            if values[rel]!=PATCH[rel]:raise RuntimeError(f'Unknown local edits: {rel}; refusing rollback')
            if digest(folder/rel)!=ORIGINAL[rel]:raise RuntimeError(f'Backup is missing or modified: {rel}')
        changed=[]
        try:
            for rel in REL:
                atomic_copy(folder/rel,targets[rel]);changed.append(rel)
                if digest(targets[rel])!=ORIGINAL[rel]:raise RuntimeError(f'Rollback checksum failed: {rel}')
        except BaseException:
            for rel in reversed(changed):atomic_copy(PACKAGE/rel,targets[rel])
            raise
        manifest.unlink()
        print('ROLLED BACK: the three original lease-related source files restored')

def main():
    parser=argparse.ArgumentParser()
    g=parser.add_mutually_exclusive_group(required=True)
    for k in ('check','install','rollback'):g.add_argument('--'+k,action='store_true')
    parser.add_argument('--root',type=Path,default=Path.home()/'.hermes/hermes-agent',help=argparse.SUPPRESS)
    parser.add_argument('--backups',type=Path,default=Path.home()/'.hermes/patch-backups/lease-heartbeat-v1',help=argparse.SUPPRESS)
    args=parser.parse_args()
    try:transact(next(x for x in ('check','install','rollback') if getattr(args,x)),args.root,args.backups)
    except Exception as e:print(f'ERROR: {e}',file=sys.stderr);sys.exit(2)

if __name__=='__main__':main()

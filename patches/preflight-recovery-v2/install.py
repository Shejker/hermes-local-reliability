#!/usr/bin/env python3
"""Atomic-ish two-file installer: refuses unknown source or running Hermes Desktop.

All replacements use os.replace within the same filesystem. The installer stores
originals before editing and rolls both files back on a caught installation error.
"""
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
REL = ('agent/turn_preflight_gate.py','agent/conversation_loop.py')
ORIGINAL = {
    REL[0]: '50237c4bd84b283a096e7a1e0c681d6b45d5869f8474c151d2137250635fc5cf',
    REL[1]: '926435d563ef1b7de1049f5c36897f1cda7376508b98164a68a0cb5192656270',
}

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

PATCH = {rel:digest(PACKAGE / rel) for rel in REL}

def running_desktop() -> bool:
    cp = subprocess.run(['/bin/ps','-axo','pid=,command=','-ww'], capture_output=True,
                        text=True,timeout=8,check=True)
    me = os.getpid()
    for row in cp.stdout.splitlines():
        parts = row.strip().split(None,1)
        if len(parts)<2 or not parts[0].isdigit() or int(parts[0])==me:continue
        cmd=parts[1]
        if ('Hermes.app/Contents/MacOS/Hermes' in cmd
            or ('python' in cmd and ' serve --host ' in cmd and '.hermes' in cmd)):
            return True
    return False

def atomic_copy(src: Path,dest: Path):
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=dest.parent,prefix='.hermes-install-',delete=False) as f:
            temp=Path(f.name)
            f.write(src.read_bytes())
            f.flush();os.fsync(f.fileno())
        os.chmod(temp,dest.stat().st_mode & 0o777)
        os.replace(temp,dest)
    finally:
        if temp and temp.exists():temp.unlink()

def transact(mode, root: Path, backups: Path):
    targets={rel: root/rel for rel in REL}
    for rel, target in targets.items():
        if not target.is_file() or target.is_symlink():
            raise RuntimeError(f'Missing or symlink source: {target}')
    values={rel:digest(targets[rel]) for rel in REL}
    manifest=backups/'installed.json'
    if mode=='check':
        print('Source files:')
        for rel in REL:
            stat='original' if values[rel]==ORIGINAL[rel] else ('patched' if values[rel]==PATCH[rel] else 'UNKNOWN')
            print(f'  {rel}: {stat} sha256={values[rel]}')
        print(f'Manifest: {"present" if manifest.exists() else "absent"}')
        return
    if running_desktop():
        raise RuntimeError('Hermes Desktop/backend still running. Close it before patching.')
    if mode=='install':
        if manifest.exists():raise RuntimeError('Install manifest already exists; inspect/rollback before reinstall.')
        for rel in REL:
            if values[rel]!=ORIGINAL[rel]:
                raise RuntimeError(f'Source checksum mismatch: {rel}: {values[rel]}')
            py_compile.compile(str(PACKAGE/rel),doraise=True)
        stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        folder=backups/stamp
        folder.mkdir(parents=True,exist_ok=False)
        for rel in REL:
            original=folder/rel
            original.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(targets[rel], original)
            if digest(original)!=ORIGINAL[rel]:raise RuntimeError(f'Backup mismatch: {rel}')
        changed=[]
        try:
            for rel in REL:
                atomic_copy(PACKAGE/rel, targets[rel]);changed.append(rel)
                if digest(targets[rel])!=PATCH[rel]:raise RuntimeError(f'Verification failed: {rel}')
            obj={'backup_dir':str(folder),'target_dir':str(root),'installed_at':datetime.now().isoformat(),
                 'original_sha256':ORIGINAL,'patched_sha256':PATCH}
            backups.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile('w',dir=backups,prefix='.manifest-',delete=False) as f:
                tm=Path(f.name)
                json.dump(obj,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tm,manifest)
        except BaseException:
            for rel in reversed(changed):atomic_copy(folder/rel,targets[rel])
            raise
        print('INSTALLED: Hermes preflight recovery v2 (single extra rescue per turn)')
        print('BACKUP:',folder)
        print('Rollback:',f'python3 {PACKAGE/"install.py"} --rollback')
    elif mode=='rollback':
        if not manifest.exists():raise RuntimeError('No install manifest; refusing rollback')
        obj=json.loads(manifest.read_text())
        if obj.get('target_dir')!=str(root):raise RuntimeError('Install root mismatch')
        folder=Path(obj['backup_dir'])
        for rel in REL:
            if values[rel]!=PATCH[rel]:raise RuntimeError(f'Patch file changed: {rel}, refusing rollback')
            if digest(folder/rel)!=ORIGINAL[rel]:raise RuntimeError(f'Corrupt backup: {rel}')
        for rel in REL:atomic_copy(folder/rel,targets[rel])
        manifest.unlink()
        print('ROLLED BACK: Preflight Runway v1 restored; original conversation_loop restored')

def main():
    parser=argparse.ArgumentParser()
    g=parser.add_mutually_exclusive_group(required=True)
    for op in ('check','install','rollback'):g.add_argument('--'+op,action='store_true')
    parser.add_argument('--root',type=Path,default=Path.home()/'.hermes/hermes-agent',help=argparse.SUPPRESS)
    parser.add_argument('--backups',type=Path,default=Path.home()/'.hermes/patch-backups/preflight-recovery-v2',help=argparse.SUPPRESS)
    args=parser.parse_args()
    try:transact(next(k for k in ('check','install','rollback') if getattr(args,k)),args.root,args.backups)
    except Exception as exc:print(f'ERROR: {exc}',file=sys.stderr);sys.exit(2)

if __name__=='__main__':main()

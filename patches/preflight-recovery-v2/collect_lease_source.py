#!/usr/bin/env python3
"""Read-only local Hermes lease source collector. Does not include configuration, logs, or SQLite."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json
from datetime import datetime

root=Path.home()/'.hermes/hermes-agent'
want=[
 'agent/turn_facade_lease.py',
 'agent/periodic_scheduler.py',
 'agent/turn_liveness.py',
 'agent/turn_facade.py',
 'agent/turn_response_intake.py',
 'agent/turn_preflight.py',
 'agent/turn_preflight_gate.py',
 'agent/conversation_loop.py',
 'hermes_state.py',
 'hermes_state_errors.py',
 'gateway/turn_lease.py',
]
out=Path.home()/ 'Desktop' / ('hermes-lease-source-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
out.parent.mkdir(exist_ok=True)
manifest={}
with ZipFile(out,'w',ZIP_DEFLATED,compresslevel=7) as z:
 for rel in want:
  p=root/rel
  if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root.resolve()):
   b=p.read_bytes()
   z.writestr(rel,b)
   manifest[rel]={'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)}
 z.writestr('manifest.json',json.dumps(manifest,indent=2)+'\n')
print('Saved:',out)
print('Included source files:',len(manifest))
for k in manifest:print(' -',k)
print('No state.db, logs, .env, or config.yaml copied.')

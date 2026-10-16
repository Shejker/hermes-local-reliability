#!/usr/bin/env python3
"""Fail-closed context compactor for Hermes Desktop local backends on macOS.

Uses supported live JSON-RPC calls. Never sends prompts, interrupts sessions,
kills processes, or writes to Hermes databases. Compression is opt-in via
--once / launchd installer, guarded by idle checks and SQLite backups.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path.home() / '.hermes'
STATE = ROOT / 'desktop-guard'
LOG = STATE / 'guard.log'
OBS = STATE / 'observations.json'
LOCK = STATE / 'guard.lock'
BACKUPS = STATE / 'backups'
THRESHOLD = 68
MIN_IDLE = 120
COOLDOWN = 30 * 60
OBS_AGE = 40  # launchd every 60s: only the next run can confirm pressure
VALID_STATUSES = {'idle'}


def log(msg):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = f'{dt.datetime.now().astimezone().isoformat(timespec="seconds")} {msg}'
    print(line)
    with LOG.open('a', encoding='utf8') as fp:
        fp.write(line+'\n')


def cmd(args, timeout=6):
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       text=True, timeout=timeout, check=False)
    return r.stdout if r.returncode == 0 else ''


def backends():
    """Strictly only this user's Desktop-owned loopback --port 0 backends."""
    ps = cmd(['/bin/ps', '-axo', 'pid=,command='])
    pids = set()
    for row in ps.splitlines():
        matched = re.match(r'\s*(\d+)\s+(.*)', row)
        if not matched:
            continue
        pid, argv = int(matched.group(1)), matched.group(2)
        if ' serve --host 127.0.0.1 --port 0' in argv and '.hermes' in argv:
            pids.add(pid)
    found = []
    for pid in sorted(pids):
        lines = cmd(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid), '-iTCP', '-sTCP:LISTEN'])
        for line in lines.splitlines()[1:]:
            m = re.search(r'TCP\s+(?:127\.0\.0\.1|localhost):(\d+)\s+\(LISTEN\)', line)
            if m:
                port = int(m.group(1))
                if 1 <= port <= 65535:
                    found.append((pid, port))
    return list(dict.fromkeys(found))


def get(url, timeout=3):
    req = urllib.request.Request(url, headers={'Accept':'application/json,text/html'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(512_000).decode('utf8','replace')


def token_for(port):
    base = f'http://127.0.0.1:{port}'
    try:
        _, s = get(base+'/api/session-token')
        d = json.loads(s)
        if isinstance(d, dict):
            for key in ('token', 'session_token', 'sessionToken'):
                val = d.get(key)
                if isinstance(val, str) and len(val) >= 20:
                    return val
    except (urllib.error.URLError, ValueError, KeyError):
        pass
    try:
        _, s = get(base+'/')
        m = re.search(r'__HERMES_SESSION_TOKEN__\s*=\s*(["\'])(.*?)\1', s)
        if m and len(m.group(2)) >= 20:
            return m.group(2)
    except (urllib.error.URLError, ValueError):
        pass
    return None


def rpc(port, token, method, params=None, timeout=8):
    """A fresh authenticated, loopback-only WebSocket connection per request."""
    import websocket
    path = '/api/ws?token='+urllib.parse.quote(token, safe='')
    ws = websocket.create_connection(f'ws://127.0.0.1:{port}'+path, timeout=timeout,
                                     origin=f'http://127.0.0.1:{port}')
    try:
        ident = 'desktop-guard-1'
        ws.send(json.dumps({'jsonrpc':'2.0','id':ident,'method':method,'params':params or {}}))
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            ws.settimeout(max(0.2, deadline-time.monotonic()))
            frame = json.loads(ws.recv())
            if not isinstance(frame,dict) or frame.get('id') != ident:
                continue
            if 'error' in frame:
                raise RuntimeError(f'{method}: RPC error {frame["error"]}')
            return frame.get('result') or {}
        raise TimeoutError(f'{method}: timeout')
    finally:
        ws.close()


def db_paths():
    paths = [ROOT / 'state.db']
    # Installed Hermes profiles may have separate DBs; backup every discovered state.db.
    proot = ROOT / 'profiles'
    if proot.exists():
        paths.extend(proot.glob('*/state.db'))
    return [p for p in paths if p.is_file() and p.resolve().is_relative_to(ROOT.resolve())]


def backup_dbs():
    sources = db_paths()
    if not sources:
        raise RuntimeError('no Hermes state.db found; refusing mutating compression')
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    parent = BACKUPS / stamp
    parent.mkdir(parents=True, mode=0o700)
    count = 0
    for src in sources:
        dst = parent / src.relative_to(ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f'file:{src}?mode=ro', uri=True, timeout=10) as original:
            with sqlite3.connect(dst, timeout=15) as copy:
                original.backup(copy, pages=256, sleep=0.15)
        os.chmod(dst, 0o600)
        count += 1
    return count


def load_state():
    try:
        data = json.loads(OBS.read_text('utf8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(st):
    tmp = OBS.with_suffix('.tmp')
    with tmp.open('w', encoding='utf8') as fp:
        json.dump(st, fp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, OBS)


def maybe_compress(pid, port, token, session, st, *, check_only):
    sid = str(session.get('id') or '')
    key = f'{pid}:{sid}'
    status = session.get('status')
    if not sid or status not in VALID_STATUSES or int(session.get('message_count') or 0) < 4:
        st.pop(key, None)
        return
    now = time.time()
    last_active = session.get('last_active')
    if not isinstance(last_active, (float,int)) or now-last_active < MIN_IDLE:
        return
    usage = rpc(port, token, 'session.usage', {'session_id':sid})
    if int(usage.get('active_subagents') or 0) > 0:
        log(f'pid={pid} sid={sid} background subagents active: skipped')
        return
    used = usage.get('context_used')
    maximum = usage.get('context_max')
    if not isinstance(used,(float,int)) or not isinstance(maximum,(float,int)) or maximum <= 0:
        log(f'pid={pid} sid={sid} no reliable context data: skipped')
        return
    pct = 100.0 * used / maximum
    log(f'pid={pid} sid={sid} status={status} context={used}/{maximum} ({pct:.1f}%)')
    old = st.get(key, {})
    if pct < THRESHOLD:
        st[key] = {'last_below':now, 'cooldown_until':old.get('cooldown_until',0)}
        return
    if check_only:
        log(f'pid={pid} sid={sid} high usage (check-only mode)')
        return
    # Need 2 separate checks across >= 40s at high pressure, and 30-min cooldown.
    if now < old.get('cooldown_until',0):
        return
    if not old.get('high_since'):
        st[key] = {'high_since':now, 'cooldown_until':old.get('cooldown_until',0)}
        return
    if now - old['high_since'] < OBS_AGE:
        return
    # Prevent a turn starting between the first read and the write. Server also refuses busy.
    active = rpc(port, token, 'session.active_list', {})
    rows = active.get('sessions', []) if isinstance(active,dict) else []
    matching = [s for s in rows if s.get('id') == sid]
    if len(matching) != 1 or matching[0].get('status') != 'idle':
        st.pop(key,None)
        return
    if time.time()-float(matching[0].get('last_active') or time.time()) < MIN_IDLE:
        return
    count = backup_dbs()
    log(f'pid={pid} sid={sid} backed up {count} state DB(s), attempting compression')
    # Set cooldown and save BEFORE making a potentially long RPC so process crashes do not thrash.
    st[key] = {'cooldown_until':now+COOLDOWN}
    save_state(st)
    result = rpc(port, token, 'session.compress', {'session_id':sid}, timeout=360)
    flag = str(result.get('status') or '')
    log(f'pid={pid} sid={sid} compression RPC returned status={flag or "unknown"}')


def one_cycle(check_only=False):
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Previous check/compression still running; skipping')
            return
        st = load_state()
        hosts = backends()
        if not hosts:
            log('No Hermes Desktop backend; nothing to do')
            return
        for pid, port in hosts:
            token = token_for(port)
            if not token:
                log(f'pid={pid} port={port}: API token not available; fail closed')
                continue
            try:
                payload = rpc(port, token, 'session.active_list', {})
                sessions = payload.get('sessions',[]) if isinstance(payload,dict) else []
                log(f'pid={pid} port={port}: connected, active sessions={len(sessions)}')
                for session in sessions:
                    try:
                        maybe_compress(pid,port,token,session,st,check_only=check_only)
                    except Exception as exc:
                        log(f'pid={pid} session error: {type(exc).__name__}: {str(exc)[:180]}')
            except Exception as exc:
                log(f'pid={pid} rpc unavailable: {type(exc).__name__}: {str(exc)[:180]}')
        save_state(st)


def main():
    p = argparse.ArgumentParser(description='Hermes Desktop context safeguard (macOS)')
    p.add_argument('--check', action='store_true', help='Read only: verify backend connection and usage')
    p.add_argument('--once', action='store_true', help='Perform one safe monitoring / compression cycle')
    args = p.parse_args()
    if not args.check and not args.once:
        p.print_help()
        return
    one_cycle(check_only=args.check)

if __name__ == '__main__':
    main()

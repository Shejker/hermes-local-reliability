#!/usr/bin/env python3
"""Hermes Desktop Context Guard v2 — safety-limited, per-session watchdog.

Supports an explicitly configured critical policy which interrupts at a confirmed
high context watermark. It does not edit Hermes SQLite, restart processes, or
silently repeat failed tool executions. The optional continuation is a new user
prompt after verified compaction and may have side effects; see README.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path.home() / '.hermes'
STATE = ROOT / 'desktop-guard'
LOG = STATE / 'guard.log'
OBS = STATE / 'observations-v2.json'
CONF = STATE / 'policy.json'
LOCK = STATE / 'guard.lock'
BACKUPS = STATE / 'backups'
DEFAULT = {
    'warn_percent': 75.0,
    'critical_percent': 85.0,
    'emergency_percent': 90.0,
    'verify_seconds': 25,
    'min_idle_seconds': 90,
    'cooldown_seconds': 600,
    'max_recoveries_per_hour': 2,
    'auto_interrupt': True,
    'auto_continue': False,
}


def log(msg):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = dt.datetime.now().astimezone().isoformat(timespec='seconds') + ' ' + msg
    print(line, flush=True)
    with LOG.open('a', encoding='utf8') as out:
        out.write(line + '\n')


def notify(msg):
    # No shell interpolation; notification is advisory only.
    try:
        subprocess.run(['/usr/bin/osascript', '-e', 'on run argv', '-e',
                        'display notification (item 1 of argv) with title "Hermes Context Guard"',
                        '-e', 'end run', msg], timeout=3, capture_output=True, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run(args, timeout=6):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout, text=True, check=False)
        return p.stdout if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def backends():
    pids = set()
    for row in run(['/bin/ps', '-axo', 'pid=,command=']).splitlines():
        m = re.match(r'\s*(\d+)\s+(.*)', row)
        if m and ' serve --host 127.0.0.1 --port 0' in m.group(2) and '.hermes' in m.group(2):
            pids.add(int(m.group(1)))
    out = []
    for pid in sorted(pids):
        for line in run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid), '-iTCP', '-sTCP:LISTEN']).splitlines()[1:]:
            m = re.search(r'TCP\s+(?:127\.0\.0\.1|localhost):(\d+)\s+\(LISTEN\)', line)
            if m and 1 <= int(m.group(1)) <= 65535:
                out.append((pid, int(m.group(1))))
    return list(dict.fromkeys(out))


def token_for(port):
    base = f'http://127.0.0.1:{port}'
    for endpoint in ('/api/session-token', '/'):
        try:
            req = urllib.request.Request(base + endpoint, headers={'Accept':'application/json,text/html'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                body = resp.read(512_000).decode('utf8', 'replace')
            if endpoint == '/':
                match = re.search(r'__HERMES_SESSION_TOKEN__\s*=\s*(["\'])(.*?)\1', body)
                if match and len(match.group(2)) >= 20:
                    return match.group(2)
            else:
                value = json.loads(body)
                for name in ('token','session_token','sessionToken'):
                    val = value.get(name) if isinstance(value, dict) else None
                    if isinstance(val, str) and len(val) >= 20:
                        return val
        except (OSError, ValueError, urllib.error.URLError):
            pass
    return None


def rpc(port, token, method, params=None, timeout=12):
    import websocket
    ws = websocket.create_connection(
        f'ws://127.0.0.1:{port}/api/ws?token=' + urllib.parse.quote(token, safe=''),
        timeout=timeout, origin=f'http://127.0.0.1:{port}')
    try:
        ident = 'desktop-guard-v2'
        ws.send(json.dumps({'jsonrpc':'2.0','id':ident,'method':method,'params':params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ws.settimeout(max(0.1, deadline - time.monotonic()))
            msg = json.loads(ws.recv())
            if isinstance(msg, dict) and msg.get('id') == ident:
                if 'error' in msg:
                    raise RuntimeError(f'{method}: {str(msg["error"])[:250]}')
                return msg.get('result') or {}
        raise TimeoutError(method + ': timed out')
    finally:
        ws.close()


def config():
    result = DEFAULT.copy()
    if CONF.is_file():
        obj = json.loads(CONF.read_text('utf8'))
        if not isinstance(obj, dict):
            raise ValueError('policy.json must be a JSON object')
        unknown = set(obj) - set(DEFAULT)
        if unknown:
            raise ValueError(f'unknown policy keys: {sorted(unknown)}')
        result.update(obj)
    for key in ('warn_percent','critical_percent','emergency_percent'):
        val = result[key]
        if isinstance(val, bool) or not isinstance(val, (float,int)):
            raise ValueError(key + ' must be a number')
    if not 40 <= result['warn_percent'] < result['critical_percent'] < result['emergency_percent'] <= 97:
        raise ValueError('Require 40 <= warn < critical < emergency <= 97')
    if result['verify_seconds'] < 0 or result['verify_seconds'] > 120:
        raise ValueError('bad verify_seconds')
    if result['min_idle_seconds'] < 0 or result['cooldown_seconds'] < 0:
        raise ValueError('negative timeout')
    if result['max_recoveries_per_hour'] < 1 or result['max_recoveries_per_hour'] > 5:
        raise ValueError('bad max_recoveries_per_hour')
    if result['auto_interrupt'] not in (True,False) or result['auto_continue'] not in (True,False):
        raise ValueError('invalid bool')
    return result


def db_paths():
    paths = [ROOT / 'state.db']
    if (ROOT / 'profiles').exists():
        paths.extend((ROOT / 'profiles').glob('*/state.db'))
    return [p for p in paths if p.is_file() and p.resolve().is_relative_to(ROOT.resolve())]


def backup_dbs():
    sources = db_paths()
    if not sources:
        raise RuntimeError('No Hermes SQLite state.db: refusing interruption')
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    folder = BACKUPS / stamp
    folder.mkdir(parents=True, mode=0o700)
    for src in sources:
        dest = folder / src.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f'file:{src}?mode=ro', uri=True, timeout=15) as con:
            with sqlite3.connect(dest, timeout=30) as copy:
                con.backup(copy, pages=512, sleep=0.10)
        dest.chmod(0o600)
    return len(sources)


def state_read():
    try:
        data = json.loads(OBS.read_text('utf8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def state_write(st):
    target = OBS.with_suffix('.tmp')
    target.write_text(json.dumps(st, indent=2), encoding='utf8')
    target.chmod(0o600)
    os.replace(target, OBS)


def usage_read(pid, port, token, row):
    sid = str(row.get('id') or '')
    if not sid:
        raise ValueError('Session id absent')
    usage = rpc(port, token, 'session.usage', {'session_id':sid})
    used, cap = usage.get('context_used'), usage.get('context_max')
    if isinstance(used, bool) or isinstance(cap, bool) or not isinstance(used,(int,float)) or not isinstance(cap,(int,float)) or cap <= 0 or used < 0:
        raise ValueError('Missing reliable context usage for ' + sid)
    pct = 100 * used / cap
    subagents = usage.get('active_subagents', row.get('active_subagents'))
    if subagents is not None:
        try:
            subagents = int(subagents)
        except (ValueError, TypeError):
            subagents = None
    return sid, usage, used, cap, pct, subagents


def active_session(port, token, sid):
    data = rpc(port, token, 'session.active_list')
    sessions = data.get('sessions', []) if isinstance(data, dict) else []
    rows = [x for x in sessions if isinstance(x, dict) and x.get('id') == sid]
    return rows[0] if len(rows) == 1 else None


def nonbusy(row):
    return isinstance(row, dict) and row.get('status') == 'idle'


def notify_once(st, sid, pct, now, kind):
    if st.get('last_warn_kind') == kind and now - st.get('last_warn_at',0) < 1800:
        return
    st['last_warn_kind'] = kind
    st['last_warn_at'] = now
    notify(f'Sesja {sid[:10]}: {pct:.1f}% kontekstu ({kind})')


def handle(pid, port, token, row, st, policy, check_only=False):
    sid, usage, used, maximum, pct, subs = usage_read(pid,port,token,row)
    key = f'{pid}:{sid}'
    now = time.time()
    session = st.setdefault(key, {})
    phase = session.get('phase','normal')
    status = row.get('status')
    log(f'pid={pid} sid={sid} status={status} context={int(used)}/{int(maximum)} ({pct:.1f}%) subagents={subs if subs is not None else "unknown"} phase={phase}')
    if check_only:
        return
    if pct >= policy['warn_percent']:
        kind = 'emergency' if pct >= policy['emergency_percent'] else ('critical' if pct >= policy['critical_percent'] else 'warning')
        notify_once(session, sid, pct, now, kind)
    elif phase == 'normal':
        session.pop('high_since',None)
        session.pop('last_warn_kind',None)

    # Complete an already requested rescue on following polls (never send two interrupts).
    if phase == 'interrupt_pending':
        if not nonbusy(row):
            if now - session.get('interrupt_at',now) >= 240:
                log(f'sid={sid} interruption did not yield idle after 240s; alert, no repeated interrupt')
                session['phase'] = 'failed'
                session['cooldown_until'] = now + policy['cooldown_seconds']
                notify('Przerwanie sesji nie doprowadziło do bezczynności. Wymaga uwagi.')
            return
        session['phase'] = 'ready_to_compress'
        session['idle_seen'] = now
        return
    if phase == 'ready_to_compress':
        if not nonbusy(row):
            session['phase'] = 'failed'
            log(f'sid={sid} became busy before rescue compression; stopped safely')
            return
        # Confirm idle on a second observation to reduce accidental concurrent mutations.
        if now - session.get('idle_seen',now) < policy['verify_seconds']:
            return
        repeat = active_session(port,token,sid)
        if not nonbusy(repeat):
            log(f'sid={sid} idle recheck failed; stopping compression')
            session['phase'] = 'failed'
            return
        _,_,u2,c2,p2,sub2 = usage_read(pid,port,token,repeat)
        if sub2 is not None and sub2 > 0:
            log(f'sid={sid} subagents active; waiting')
            return
        count = backup_dbs()
        log(f'sid={sid} rescue compression starting; {count} SQLite copies saved')
        session['phase'] = 'compressing'
        session['precompress_context'] = u2
        state_write(st)
        try:
            response = rpc(port, token, 'session.compress', {'session_id':sid}, timeout=360)
            log(f'sid={sid} session.compress RPC responded; result_type={type(response).__name__}, status={str(response.get("status", "unknown"))[:40] if isinstance(response,dict) else "unknown"}')
        except Exception as exc:
            log(f'sid={sid} compression RPC failed: {type(exc).__name__}: {str(exc)[:150]}')
            session['phase'] = 'failed'
            session['cooldown_until'] = time.time() + policy['cooldown_seconds']
            return
        session['phase'] = 'verify_compression'
        return
    if phase == 'verify_compression':
        if not nonbusy(row):
            return
        before = session.get('precompress_context',used)
        saved = before - used
        if pct < policy['warn_percent'] and saved >= 2048:
            log(f'sid={sid} RECOVERED after interrupt+compress: reclaimed ~{int(saved)} tokens, context {pct:.1f}%')
            session['phase'] = 'recovered'
            session['cooldown_until'] = now + policy['cooldown_seconds']
            if policy['auto_continue']:
                session['phase'] = 'ready_to_continue'
            else:
                notify('Kompresja awaryjna zakończona. Sesja zatrzymana; kontynuuj zadanie ręcznie.')
        elif now - session.get('interrupt_at',now) > 540:
            log(f'sid={sid} compression has NOT produced adequate headroom: {pct:.1f}% delta={int(saved)}; no continuation')
            session['phase'] = 'failed'
            session['cooldown_until'] = now + policy['cooldown_seconds']
            notify('Nie udało się bezpiecznie zwolnić kontekstu; sesja wymaga uwagi.')
        return
    if phase == 'ready_to_continue':
        if not nonbusy(row):
            log(f'sid={sid} became active before auto-continuation; leaving it alone')
            session['phase'] = 'recovered'
            return
        # No replay of the last prompt. One non-idempotent *new* instruction only.
        followup = ('Przerwano poprzedni przebieg z powodu ryzyka przepełnienia kontekstu. '
                    'Kontynuuj bieżące zadanie od aktualnego stanu projektu. '
                    'Najpierw sprawdź istniejące pliki i rezultaty. '
                    'Nie powtarzaj bez weryfikacji zapisów, wysyłek ani innych operacji o skutkach ubocznych. '
                    'Jeżeli nie da się bezpiecznie ustalić, co już wykonano, zatrzymaj się i opisz ryzyko.')
        session['phase'] = 'continue_dispatched'
        state_write(st)
        try:
            result = rpc(port, token, 'prompt.submit', {'session_id':sid, 'text':followup}, timeout=20)
            log(f'sid={sid} continuation prompt submitted; response_type={type(result).__name__}')
        except Exception as exc:
            log(f'sid={sid} continuation rejected/uncertain: {type(exc).__name__}: {str(exc)[:150]}; will NOT retry to avoid duplication')
            session['phase'] = 'failed'
        return
    if phase in ('continue_dispatched','recovered'):
        if pct < policy['warn_percent']:
            session['phase'] = 'normal'
        return
    if phase == 'failed':
        if pct < policy['warn_percent']:
            session['phase'] = 'normal'
        elif now < session.get('cooldown_until',0):
            return
        else:
            session['phase'] = 'normal'
    # Separate idle compaction from in-flight emergency interruption.
    if nonbusy(row):
        if pct < policy['critical_percent']:
            return
        last = row.get('last_active')
        if not isinstance(last,(float,int)) or now-last < policy['min_idle_seconds']:
            return
        if now < session.get('cooldown_until',0):
            return
        if subs is not None and subs > 0:
            return
        first = session.setdefault('high_since',now)
        if now-first < policy['verify_seconds']:
            return
        session['phase'] = 'ready_to_compress'
        session['idle_seen'] = now
        session['interrupt_at'] = now
        return
    if status != 'working' or not policy['auto_interrupt'] or now < session.get('cooldown_until',0):
        return
    if subs is not None and subs > 0:
        log(f'sid={sid} critical context but subagents active: no interrupt')
        return
    if pct < policy['critical_percent']:
        session.pop('high_since',None)
        return
    first = session.setdefault('high_since',now)
    if pct < policy['emergency_percent'] and now-first < policy['verify_seconds']:
        return
    recent = [v for v in session.get('recoveries',[]) if now-v < 3600]
    if len(recent) >= policy['max_recoveries_per_hour']:
        log(f'sid={sid} high context {pct:.1f}% but recovery rate limit hit; manual intervention needed')
        notify('Limit automatycznych odzyskiwań przekroczony; sprawdź Hermesa.')
        return
    latest = active_session(port,token,sid)
    if not latest or latest.get('status') != 'working':
        log(f'sid={sid} recheck changed to nonworking; no interrupt')
        return
    _,_,u3,c3,p3,s3 = usage_read(pid,port,token,latest)
    if s3 is not None and s3 > 0:
        log(f'sid={sid} became subagent-busy; no interrupt')
        return
    if p3 < policy['critical_percent']:
        return
    copied = backup_dbs()
    log(f'sid={sid} CRITICAL {p3:.1f}%: {copied} SQLite backups; requesting controlled session.interrupt')
    session['phase'] = 'interrupt_pending'
    session['interrupt_at'] = now
    session['recoveries'] = recent + [now]
    session['high_since'] = now
    state_write(st)
    try:
        response = rpc(port,token,'session.interrupt',{'session_id':sid},timeout=12)
        log(f'sid={sid} interrupt RPC returned; response_type={type(response).__name__}')
        notify(f'Sesja {sid[:10]} przerwana zapobiegawczo przy {p3:.1f}% kontekstu.')
    except Exception as exc:
        session['phase'] = 'failed'
        session['cooldown_until'] = now + policy['cooldown_seconds']
        log(f'sid={sid} interrupt uncertain/failed: {type(exc).__name__}: {str(exc)[:150]}')
        notify('Nie udało się przerwać sesji o krytycznym kontekście; sprawdź Hermesa.')


def once(*, check_only=False):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('Another guard cycle running; skipping')
            return
        policy = config()
        st = state_read()
        servers = backends()
        if not servers:
            log('No Hermes Desktop backend found')
            return
        for pid, port in servers:
            tok = token_for(port)
            if not tok:
                log(f'pid={pid} port={port} no API token: skip')
                continue
            try:
                resp = rpc(port,tok,'session.active_list')
                rows = resp.get('sessions',[]) if isinstance(resp,dict) else []
                log(f'pid={pid} port={port} connected; sessions={len(rows)}')
                for row in rows:
                    try:
                        handle(pid,port,tok,row,st,policy,check_only=check_only)
                    except Exception as exc:
                        log(f'pid={pid} sid={row.get("id")} error {type(exc).__name__}: {str(exc)[:220]}')
            except Exception as exc:
                log(f'pid={pid} API error {type(exc).__name__}: {str(exc)[:220]}')
        if not check_only:
            state_write(st)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--check',action='store_true',help='read-only check')
    p.add_argument('--once',action='store_true',help='single guard iteration')
    args = p.parse_args()
    if not (args.check or args.once):
        p.print_help()
        return
    once(check_only=args.check)

if __name__ == '__main__':
    main()

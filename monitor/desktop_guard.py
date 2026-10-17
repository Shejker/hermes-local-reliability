#!/usr/bin/env python3
"""Hermes Desktop Guard v2.2 — strictly observational release.

Design: until Hermes exposes an authoritative 'compaction in progress' / turn
lease state, there is no reliable way for an external process to know when a
working turn can be interrupted safely. This version never interrupts,
compresses, submits prompts, mutates Hermes SQLite, or restarts processes.
It only reads supported session RPCs, writes its own monitoring state/log,
and can display macOS notifications. Old rescue phases are never resumed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = '2.2-observe-only'
ROOT = Path.home() / '.hermes'
STATE = ROOT / 'desktop-guard'
LOG = STATE / 'guard.log'
OBS = STATE / 'observations-v2.json'
CONF = STATE / 'policy.json'
LOCK = STATE / 'guard.lock'
AGENT_LOG = ROOT / 'logs' / 'agent.log'
READ_ONLY_RPCS = frozenset((
    'session.active_list', 'session.usage', 'session.context_breakdown',
))

DEFAULT = {
    'warn_percent': 75.0,
    'critical_percent': 85.0,
    'emergency_percent': 90.0,
    'verify_seconds': 25,
    'min_idle_seconds': 90,
    'cooldown_seconds': 600,
    'max_recoveries_per_hour': 2,
    'auto_interrupt': False,
    'auto_continue': False,
}


def log(msg):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = dt.datetime.now().astimezone().isoformat(timespec='seconds') + ' ' + msg
    print(line, flush=True)
    with LOG.open('a', encoding='utf8') as out:
        out.write(line + '\n')


def notify(msg):
    try:
        subprocess.run([
            '/usr/bin/osascript', '-e', 'on run argv', '-e',
            'display notification (item 1 of argv) with title "Hermes Context Guard"',
            '-e', 'end run', msg,
        ], timeout=3, capture_output=True, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run(args, timeout=6):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=timeout,
                           text=True, check=False)
        return p.stdout if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def backends():
    pids = set()
    for row in run(['/bin/ps', '-axo', 'pid=,command=']).splitlines():
        m = re.match(r'\s*(\d+)\s+(.*)', row)
        if m and ' serve --host 127.0.0.1 --port 0' in m.group(2) and '.hermes' in m.group(2):
            pids.add(int(m.group(1)))
    result = []
    for pid in sorted(pids):
        lines = run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid),
                     '-iTCP', '-sTCP:LISTEN']).splitlines()[1:]
        for line in lines:
            m = re.search(r'TCP\s+(?:127\.0\.0\.1|localhost):(\d+)\s+\(LISTEN\)', line)
            if m and 1 <= int(m.group(1)) <= 65535:
                result.append((pid, int(m.group(1))))
    return list(dict.fromkeys(result))


def token_for(port):
    base = f'http://127.0.0.1:{port}'
    for endpoint in ('/api/session-token', '/'):
        try:
            req = urllib.request.Request(
                base + endpoint, headers={'Accept': 'application/json,text/html'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                body = resp.read(512_000).decode('utf8', 'replace')
            if endpoint == '/':
                match = re.search(r'__HERMES_SESSION_TOKEN__\s*=\s*(["\'])(.*?)\1', body)
                if match and len(match.group(2)) >= 20:
                    return match.group(2)
            else:
                value = json.loads(body)
                if isinstance(value, dict):
                    for name in ('token', 'session_token', 'sessionToken'):
                        val = value.get(name)
                        if isinstance(val, str) and len(val) >= 20:
                            return val
        except (OSError, ValueError, urllib.error.URLError):
            pass
    return None


def rpc(port, token, method, params=None, timeout=12):
    """Hard allowlist: even a mistaken caller cannot mutate a Hermes session."""
    if method not in READ_ONLY_RPCS:
        raise PermissionError(f'Guard v2.2 blocks non-read-only RPC: {method}')
    import websocket
    ws = websocket.create_connection(
        f'ws://127.0.0.1:{port}/api/ws?token=' + urllib.parse.quote(token, safe=''),
        timeout=timeout, origin=f'http://127.0.0.1:{port}')
    try:
        ident = 'desktop-guard-v22'
        ws.send(json.dumps({'jsonrpc': '2.0', 'id': ident,
                            'method': method, 'params': params or {}}))
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
    for key in ('warn_percent', 'critical_percent', 'emergency_percent'):
        val = result[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(key + ' must be numeric')
    if not 40 <= result['warn_percent'] < result['critical_percent'] < result['emergency_percent'] <= 97:
        raise ValueError('Require 40 <= warn < critical < emergency <= 97')
    if not 0 <= result['verify_seconds'] <= 120:
        raise ValueError('bad verify_seconds')
    if result['min_idle_seconds'] < 0 or result['cooldown_seconds'] < 0:
        raise ValueError('negative timeout')
    if not 1 <= result['max_recoveries_per_hour'] <= 5:
        raise ValueError('bad max_recoveries_per_hour')
    for key in ('auto_interrupt', 'auto_continue'):
        if type(result[key]) is not bool:
            raise ValueError(key + ' must be boolean')
    # Legacy keys are preserved for config-file compatibility, NOT acted upon.
    return result


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


def valid_context(payload):
    if not isinstance(payload, dict):
        return None
    used, cap = payload.get('context_used'), payload.get('context_max')
    if (isinstance(used, bool) or isinstance(cap, bool)
            or not isinstance(used, (int, float)) or not isinstance(cap, (int, float))
            or not 0 < cap <= 10_000_000 or not 0 <= used <= cap * 1.25):
        return None
    return used, cap


def usage_read(pid, port, token, row):
    sid = str(row.get('id') or '')
    if not sid:
        raise ValueError('Session id absent')
    usage = rpc(port, token, 'session.usage', {'session_id': sid})
    validated = valid_context(usage)
    if validated and validated[0] == 0 and int(row.get('message_count') or 0) > 0:
        validated = None
    evidence = 'session.usage'
    verified = bool(validated) and not bool(usage.get('context_estimated', False)) and (
        usage.get('context_source') not in ('local_estimate', 'provider_usage_plus_estimate'))
    if validated is None:
        breakdown = rpc(port, token, 'session.context_breakdown', {'session_id': sid})
        validated = valid_context(breakdown)
        if validated and validated[0] == 0 and int(row.get('message_count') or 0) > 0:
            validated = None
        if validated is None:
            raise ValueError('No context usage from either supported read-only RPC')
        evidence = 'session.context_breakdown'
        verified = (breakdown.get('context_estimated') is False and
                    breakdown.get('context_source') == 'provider_usage')
        usage = {**usage, **breakdown}
    used, cap = validated
    subagents = usage.get('active_subagents', row.get('active_subagents'))
    try:
        subagents = int(subagents) if subagents is not None else None
    except (ValueError, TypeError):
        subagents = None
    return sid, float(used), float(cap), 100 * float(used) / float(cap), verified, evidence, subagents


def recent_compaction_evidence(sid, path=None):
    """Advisory only: logs cannot authoritatively describe current agent state."""
    path = AGENT_LOG if path is None else Path(path)
    try:
        with path.open('rb') as fp:
            fp.seek(0, os.SEEK_END)
            end = fp.tell()
            fp.seek(max(0, end - 2_000_000))
            raw = fp.read().decode('utf8', 'replace')
    except OSError:
        return 'unknown'
    started = None
    finished = None
    for line in raw.splitlines():
        if len(line) < 23 or not line.startswith(('2026-', '2027-')):
            continue
        if ('context compression started:' in line and f'session={sid}' in line):
            started = line[:23]
        if (('context compression done:' in line and f'session={sid}' in line)
                or ('compression attempt telemetry:' in line and f'"session_id":"{sid}"' in line)):
            finished = line[:23]
    if not started or (finished and finished >= started):
        return 'not_seen_active'
    try:
        ts = dt.datetime.strptime(started, '%Y-%m-%d %H:%M:%S,%f')
    except ValueError:
        return 'unknown'
    age = (dt.datetime.now() - ts).total_seconds()
    return 'started_recently' if 0 <= age <= 20 * 60 else 'uncertain_old_start'


def notify_once(st, sid, pct, now, kind):
    if st.get('last_warn_kind') == kind and now - st.get('last_warn_at', 0) < 1800:
        return
    st['last_warn_kind'] = kind
    st['last_warn_at'] = now
    notify(f'Hermes {sid[:10]}: {pct:.1f}% kontekstu ({kind}). Guard tylko monitoruje.')


def handle(pid, port, token, row, st, policy, check_only=False):
    sid, used, maximum, pct, verified, evidence, subs = usage_read(pid, port, token, row)
    now = time.time()
    key = f'{pid}:{sid}'
    session = st.get(key, {}) if check_only else st.setdefault(key, {})
    last_used = session.get('last_used')
    last_change = session.get('last_change_at', now)
    if last_used != used or row.get('status') != session.get('last_status'):
        last_change = now
    stale_s = int(max(0, now - last_change))
    compaction = recent_compaction_evidence(sid)
    log(f'v2.2 mode=observe_only pid={pid} sid={sid} status={row.get("status")} '
        f'context={int(used)}/{int(maximum)} ({pct:.1f}%) '
        f'source={evidence} verified={verified} '
        f'subagents={subs if subs is not None else "unknown"} '
        f'usage_unchanged_s={stale_s} compression_log={compaction} '
        f'phase=monitor_only')
    if check_only:
        return
    # One-way migration: any v2.1 interrupt/compress/continue phase is abandoned.
    session['phase'] = 'monitor_only'
    session['last_used'] = used
    session['last_status'] = row.get('status')
    session['last_change_at'] = last_change
    if pct >= policy['warn_percent']:
        kind = ('emergency' if pct >= policy['emergency_percent'] else
                'critical' if pct >= policy['critical_percent'] else 'warning')
        notify_once(session, sid, pct, now, kind)
    else:
        session.pop('last_warn_kind', None)
    if (stale_s >= 600 and row.get('status') == 'working' and
            now - session.get('last_stale_warning', 0) > 900):
        log(f'v2.2 sid={sid} INFO usage unchanged for {stale_s}s while working; '
            'this is NOT proof of a hang or confirmed live usage')
        session['last_stale_warning'] = now
    # No other actions: no session.interrupt, session.compress, prompt.submit,
    # SQLite access, process management, or auto-resumption.


def once(*, check_only=False):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('Another guard cycle running; skipping')
            return
        policy = config()
        if policy['auto_interrupt'] or policy['auto_continue']:
            log('v2.2 WARNING legacy auto_interrupt/auto_continue requested in policy; '
                'IGNORED. This release is observe-only.')
        st = state_read()
        servers = backends()
        if not servers:
            log('v2.2 No Hermes Desktop backend found')
            return
        for pid, port in servers:
            tok = token_for(port)
            if not tok:
                log(f'v2.2 pid={pid} port={port} no API token: skip')
                continue
            try:
                resp = rpc(port, tok, 'session.active_list')
                rows = resp.get('sessions', []) if isinstance(resp, dict) else []
                log(f'v2.2 pid={pid} port={port} connected; sessions={len(rows)}')
                for row in rows:
                    try:
                        handle(pid, port, tok, row, st, policy, check_only=check_only)
                    except Exception as exc:
                        log(f'v2.2 pid={pid} sid={row.get("id")} MONITOR ERROR '
                            f'{type(exc).__name__}: {str(exc)[:220]}')
            except Exception as exc:
                log(f'v2.2 pid={pid} API error {type(exc).__name__}: {str(exc)[:220]}')
        if not check_only:
            state_write(st)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--check', action='store_true', help='read-only status check')
    p.add_argument('--once', action='store_true', help='one monitoring pass')
    p.add_argument('--version', action='store_true', help='show version')
    args = p.parse_args()
    if args.version:
        print('Hermes Desktop Guard', VERSION)
        return
    if not (args.check or args.once):
        p.print_help()
        return
    once(check_only=args.check)


if __name__ == '__main__':
    main()

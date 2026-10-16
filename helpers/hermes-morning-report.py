#!/usr/bin/env python3
"""Read-only, standard-library morning diagnostics for Hermes Desktop + Ollama on macOS."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

H = Path.home()
AGENT = H / '.hermes/logs/agent.log'
GUARD = H / '.hermes/desktop-guard/guard.log'
POLICY = H / '.hermes/desktop-guard/policy.json'
OLLAMA_CANDIDATES = [
    H / '.ollama/logs/server.log',
    H / 'Library/Logs/Ollama/server.log',
    H / 'Library/Logs/Ollama/ollama.log',
    H / 'Library/Logs/Ollama.log',
]
OLLAMA_CANDIDATES += sorted((H / 'Library/Logs/Ollama').glob('*.log'))

TIME_PREFIX = re.compile(r'^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)')
USAGE = re.compile(r'context=(\d+)/(\d+)\s+\(([\d.]+)%\)')
EVENT_AGENT = re.compile(
    r'compress|compact|prun|summary|overflow|out.of.context|threshold|'
    r'cooldown|stall|timeout|token|preflight', re.IGNORECASE)
EVENT_OLLAMA = re.compile(
    r'out.of.memory|evict|memory|model load|load_model|n_ctx_slot|'
    r'n_ctx\s*=|kv buffer|prompt eval time|eval time|total time|'
    r'prompt cache|context.shift|truncat|error|warn|fail|'
    r'no space|swap|system_limited', re.IGNORECASE)
EVENT_GUARD = re.compile(
    r'UNPROTECTED|interrupt|critical|recover|resume|continue|compress|'
    r'phase=(?!normal)|warn|error|backup|cooldown|threshold', re.IGNORECASE)


def run(args: list[str], timeout=12) -> str:
    if shutil.which(args[0]) is None:
        return f'[command unavailable: {args[0]}]\n'
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           errors='replace', check=False)
        result = (p.stdout or '') + (('\n[stderr] ' + p.stderr) if p.stderr else '')
        return result[:50000] + (f'\n[exit status: {p.returncode}]\n' if p.returncode else '')
    except Exception as e:
        return f'[failed to run {args[0]}: {type(e).__name__}: {e}]\n'


def safe_recent(path: Path, since: dt.datetime, max_bytes: int) -> tuple[str, int]:
    """Recent timestamped records; deliberately cap size without changing source."""
    parts = collections.deque()
    size = 0
    current_in_range = False
    count = 0
    try:
        with path.open('r', errors='replace') as f:
            for line in f:
                match = TIME_PREFIX.match(line)
                if match:
                    stamp = f'{match.group(1)}T{match.group(2)}'
                    try:
                        current_in_range = dt.datetime.fromisoformat(stamp) >= since
                    except ValueError:
                        current_in_range = False
                if not current_in_range:
                    continue
                parts.append(line)
                count += 1
                size += len(line.encode('utf-8', errors='replace'))
                while parts and size > max_bytes:
                    old = parts.popleft()
                    size -= len(old.encode('utf-8', errors='replace'))
    except (OSError, UnicodeError) as e:
        return f'[Could not read: {path}: {e}]\n', 0
    return ''.join(parts), count


def last_lines(path: Path, pattern: re.Pattern | None = None, limit: int = 3000) -> str:
    records = collections.deque(maxlen=limit)
    try:
        with path.open('r', errors='replace') as f:
            for line in f:
                if pattern is None or pattern.search(line):
                    records.append(line)
    except OSError as e:
        return f'[Could not read: {path}: {e}]\n'
    return ''.join(records)


def write(p: Path, s: str) -> None:
    p.write_text(s, encoding='utf-8', errors='replace')


def main():
    parser = argparse.ArgumentParser(description='Create read-only Hermes/Ollama diagnostics ZIP')
    parser.add_argument('--hours', type=int, default=16, help='Time window for Hermes logs (default 16h)')
    opts = parser.parse_args()
    if not 1 <= opts.hours <= 168:
        parser.error('--hours must be between 1 and 168')
    now = dt.datetime.now().astimezone()
    since = dt.datetime.now() - dt.timedelta(hours=opts.hours)
    desktop = H / 'Desktop'
    if not desktop.is_dir():
        desktop = H
    archive = desktop / f'hermes-overnight-{now:%Y%m%d-%H%M%S}.zip'
    notes = []

    with tempfile.TemporaryDirectory(prefix='hermes-diagnostics-') as tmp:
        p = Path(tmp)
        output = []
        output.append(f'COLLECTED AT: {now.isoformat()}\n')
        output.append(f'LOG WINDOW: last {opts.hours} hours, since {since.isoformat()}\n')
        output.append('READ-ONLY COLLECTION: no service/session changes performed\n')
        for title, command in [
            ('System date', ['date']),
            ('System uptime', ['uptime']),
            ('Ollama process/model status', ['ollama', 'ps']),
            ('Ollama models', ['ollama', 'list']),
            ('Memory pressure', ['memory_pressure', '-Q']),
            ('Virtual memory', ['vm_stat']),
            ('System memory bytes', ['sysctl', '-n', 'hw.memsize']),
            ('Guard LaunchAgent state', ['launchctl', 'print', f'gui/{os.getuid()}/com.local.hermes-desktop-guard']),
            ('Compression threshold', ['hermes', 'config', 'get', 'compression.threshold_tokens']),
            ('Compression tail mode', ['hermes', 'config', 'get', 'compression.tail_mode']),
            ('Protected tail messages', ['hermes', 'config', 'get', 'compression.protect_last_n']),
            ('Auxiliary compression model', ['hermes', 'config', 'get', 'auxiliary.compression.model']),
            ('Auxiliary compression provider', ['hermes', 'config', 'get', 'auxiliary.compression.provider']),
        ]:
            data = run(command)
            if title == 'Guard LaunchAgent state':
                data = ''.join(line for line in data.splitlines(keepends=True)
                               if re.search(r'run interval|runs =|exit code|state =|job state|pid =|error', line, re.I))
            output.append(f'\n=== {title} ===\n{data}\n')
        write(p / '01-status.txt', ''.join(output))

        if POLICY.is_file():
            try:
                cfg = json.loads(POLICY.read_text())
                keys = ('interrupt', 'continue', 'threshold', 'interval', 'cooldown',
                        'warn', 'critical', 'percent', 'backup', 'dry_run')
                def scrub(obj):
                    if isinstance(obj, dict):
                        return {k: scrub(v) for k, v in obj.items() if any(x in k.lower() for x in keys)}
                    if isinstance(obj, list):
                        return [scrub(v) for v in obj]
                    return obj if isinstance(obj, (bool, int, float)) else '[non-numeric value omitted]'
                write(p / '02-guard-policy-safe.json', json.dumps(scrub(cfg), indent=2, ensure_ascii=False))
            except (ValueError, OSError) as e:
                notes.append(f'Policy unreadable: {e}')
        else:
            notes.append('Guard policy.json not found')

        if GUARD.is_file():
            text, count = safe_recent(GUARD, since, 15_000_000)
            write(p / '03-guard-overnight.log', text)
            write(p / '04-guard-events.log', '\n'.join(x for x in text.splitlines()
                                                        if EVENT_GUARD.search(x)) + '\n')
            peaks = []
            for line in text.splitlines():
                u = USAGE.search(line)
                if u:
                    peaks.append((float(u.group(3)), line))
            highest = max(peaks, default=None, key=lambda x: x[0])
            write(p / '05-guard-summary.txt',
                  f'Lines during selected time window: {count}\n'
                  f'Lines retained in bounded copy: {len(text.splitlines())}\n'
                  f'Peak logged context: {highest[0]:.1f}%\n{highest[1]}\n' if highest else
                  f'Lines during selected time window: {count}\nNo valid context readings in selected period\n')
        else:
            notes.append('Guard log missing: ~/.hermes/desktop-guard/guard.log')

        if AGENT.is_file():
            recent, count = safe_recent(AGENT, since, 40_000_000)
            write(p / '06-hermes-agent-overnight.log', recent)
            events = [line for line in recent.splitlines() if EVENT_AGENT.search(line)]
            write(p / '07-hermes-agent-events.log', '\n'.join(events[-3500:]) + '\n')
            totals = collections.Counter()
            attempts = []
            for line in recent.splitlines():
                marker = 'compression attempt telemetry:'
                if marker not in line:
                    continue
                try:
                    item = json.loads(line.split(marker, 1)[1].strip())
                    status = str(item.get('commit_status', 'unknown'))
                    cause = str(item.get('failure_class', '') or '')
                    totals['status:' + status] += 1
                    if cause:
                        totals['reason:' + cause] += 1
                    attempts.append({
                        'timestamp': line[:23],
                        'status': status,
                        'reason': cause,
                        'trigger': item.get('trigger_source'),
                        'aux_model': item.get('aux_model'),
                        'tokens_reclaimed': item.get('tokens_reclaimed'),
                        'duration_ms': item.get('total_duration_ms'),
                    })
                except (ValueError, TypeError):
                    totals['malformed telemetry'] += 1
            report = [f'Agent lines in period: {count}',
                      f'Bounded lines kept: {len(recent.splitlines())}',
                      'Compression telemetry counts:']
            report += [f'  {k}: {v}' for k, v in totals.most_common()]
            report += ['\nLast 40 compression attempts:']
            report += [json.dumps(a, ensure_ascii=False) for a in attempts[-40:]]
            write(p / '08-compression-summary.txt', '\n'.join(report) + '\n')
        else:
            notes.append('Hermes agent log missing: ~/.hermes/logs/agent.log')

        seen = set()
        number = 0
        for log in OLLAMA_CANDIDATES:
            if not log.is_file():
                continue
            try:
                real = log.resolve()
                if real in seen:
                    continue
                seen.add(real)
                number += 1
                prefix = f'{9 + number:02d}-ollama-{number}'
                # Ollama debug lines often lack timestamps: bounded raw tail + important events.
                write(p / f'{prefix}-recent.log', last_lines(log, limit=20000))
                write(p / f'{prefix}-events.log', last_lines(log, EVENT_OLLAMA, limit=3500))
                notes.append(f'Ollama source {number}: {log} ({log.stat().st_size:,} bytes)')
            except OSError as e:
                notes.append(f'Cannot inspect Ollama candidate: {log}: {e}')
        if not seen:
            notes.append('No Ollama server.log found at standard macOS locations; paste a terminal log excerpt separately if available')

        write(p / '00-README.txt',
              'HERMES DESKTOP / OLLAMA OVERNIGHT DIAGNOSTICS\n\n'
              f'Collected: {now.isoformat()}\n'
              f'Window: last {opts.hours} hours (Hermes logs)\n\n'
              'Contents: status and system memory, guard policy subset, guard and agent overnight logs,\n'
              'compression attempts, recent Ollama logs and relevant memory/errors.\n'
              'Ollama raw tail may be shorter than the overnight period because its debug log can be enormous.\n'
              'This script did NOT change sessions, models, configuration or LaunchAgents.\n\n'
              'PRIVACY: Raw logs may contain file paths, user-provided prompts, project names, and tokens.\n'
              'Review or redact before sharing. Never upload state.db, config.yaml or API credentials.\n\n'
              + '\n'.join(notes) + '\n')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for entry in sorted(p.iterdir()):
                z.write(entry, arcname=entry.name)

    print('DONE. Morning diagnostics ZIP:')
    print(archive)
    print(f'Archive size: {archive.stat().st_size/1024/1024:.1f} MiB')
    print('Review for sensitive information before attaching to ChatGPT.')
    print('Also share a screenshot or text of /context and /usage from Hermes Desktop.')

if __name__ == '__main__':
    main()

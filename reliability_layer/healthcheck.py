"""Read-only compatibility + module health audit (no Gateway, no secrets displayed)."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import urlopen

from .installer import inspect, live_processes
from .rearm import sha

ROOT = Path.home() / ".hermes/hermes-agent"

def command(args, timeout=7):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def config_summary(path: Path) -> dict:
    """Strictly whitelisted non-secret keys; no interpolation or shell expansion."""
    keys = {"model.default", "model.provider", "model.base_url", "model.context_length", "model.ollama_num_ctx",
            "compression.enabled", "compression.threshold_tokens", "compression.max_attempts", "compression.tail_mode",
            "compression.protect_last_n", "compression.in_place", "auxiliary.compression.model",
            "auxiliary.compression.provider", "auxiliary.compression.timeout"}
    if not path.is_file():
        return {}
    parts, output = [], {}
    for line in path.read_text(errors="replace").splitlines():
        line = line.split("#", 1)[0].rstrip()
        m = re.match(r"^(\s*)([\w.-]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        indent, key, value = len(m[1]), m[2], m[3].strip()
        while parts and parts[-1][0] >= indent:
            parts.pop()
        full = ".".join([s for _, s in parts] + [key])
        if full in keys:
            # Do not echo unexpected/malicious contents, including URLs with credentials.
            if re.fullmatch(r"[\w.:-]{1,128}", value) or value == "http://127.0.0.1:11434/v1":
                output[full] = value
            else:
                output[full] = "[nonstandard/redacted]"
        if not value:
            parts.append((indent, key))
    return output


def compression_metrics(logdir: Path, after: datetime) -> dict:
    seen, records, errors = set(), [], Counter()
    error_types = ("context_runway_guard", "compression made no progress", "max compression attempts reached",
                   "pre-api runway guard blocked", "session turn lease lost")
    for path in sorted(logdir.glob("agent.log*")):
        if not path.is_file() or path.suffix == ".gz":
            continue
        try:
            f = path.open(errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                ts = line[:19]
                try:
                    when = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if when < after:
                    continue
                low = line.lower()
                for phrase in error_types:
                    if phrase in low:
                        errors[phrase] += 1
                prefix = "context compression attempt telemetry: "
                if prefix not in line:
                    continue
                try:
                    event = json.loads(line.split(prefix, 1)[1])
                except (ValueError, IndexError):
                    continue
                event_id = event.get("attempt_id")
                key = event_id or (ts, event.get("trigger_source"), event.get("tokens_reclaimed"))
                if key in seen:
                    continue
                seen.add(key)
                records.append((ts, event))
    records.sort(key=lambda x: x[0])
    by = Counter(e.get("trigger_source", "unknown") for _, e in records)
    return {
        "attempts": len(records),
        "committed": sum(e.get("commit_status") == "committed" for _, e in records),
        "automatic": sum(e.get("trigger_source") != "manual" for _, e in records),
        "reclaimed_tokens_sum": sum(max(int(e.get("tokens_reclaimed") or 0), 0) for _, e in records),
        "triggers": dict(by),
        "errors": dict(errors),
        "recent_events": [{"at": ts, "status": e.get("commit_status"), "trigger": e.get("trigger_source"),
                           "reclaimed": e.get("tokens_reclaimed"), "failure": e.get("failure_class")}
                          for ts, e in records[-5:]],
    }


def guard_health(home: Path, expected_sha: str) -> dict:
    target = home / ".hermes/desktop-guard/desktop_guard.py"
    log = home / ".hermes/desktop-guard/guard.log"
    plist = home / "Library/LaunchAgents/com.local.hermes-desktop-guard.plist"
    if not target.is_file():
        return {"state": "absent", "launchagent_exists": plist.exists()}
    digest = sha(target.read_bytes())
    state = "recognized-v2.2" if digest == expected_sha else "modified-or-newer"
    lines = []
    if log.is_file():
        # Bounded backward tail from the end, no entire log in memory.
        try:
            with log.open(errors="replace") as file:
                from collections import deque
                lines = list(deque(file, maxlen=80))
        except OSError:
            pass
    latest = next((line for line in reversed(lines) if "mode=observe_only" in line), "")
    result = {"state": state, "sha256": digest, "launchagent_exists": plist.is_file(),
              "observe_only_seen": "mode=observe_only" in latest,
              "verified": ("verified=True" in latest) if latest else None,
              "last_status": "working" if "status=working" in latest else "idle" if "status=idle" in latest else "unknown",
              "last_context": None, "last_timestamp": latest.split(" ", 1)[0] if latest else None}
    match = re.search(r"context=(\d+)/(\d+)", latest)
    if match:
        result["last_context"] = [int(match[1]), int(match[2])]
    if latest:
        try:
            last = datetime.fromisoformat(result["last_timestamp"])
            result["stale_seconds"] = max(int((datetime.now(last.tzinfo) - last).total_seconds()), 0)
        except ValueError:
            pass
    return result


def evaluate(home: Path, repo: Path, hours: int) -> dict:
    root = home / ".hermes/hermes-agent"
    data = {"audit_at": datetime.now().isoformat(timespec="seconds"), "read_only": True, "checks": {}}
    checks = data["checks"]
    if root.is_dir():
        try:
            info = inspect(root)
            checks["backend"] = info
            checks["backend"]["verdict"] = "PASS" if info["state"] in ("supported", "installed") else "REVIEW"
        except (RuntimeError, OSError) as exc:
            checks["backend"] = {"verdict": "REVIEW", "reason": str(exc)}
    else:
        checks["backend"] = {"verdict": "FAIL", "reason": "Hermes source absent"}
    checks["processes"] = {"hermes_pids": live_processes()}
    checks["config"] = config_summary(home / ".hermes/config.yaml")
    try:
        with urlopen("http://127.0.0.1:11434/api/version", timeout=2) as response:
            value = json.load(response)
        checks["ollama"] = {"verdict": "PASS", "version": str(value.get("version", "unknown"))[:40]}
    except (OSError, TimeoutError, ValueError) as exc:
        checks["ollama"] = {"verdict": "FAIL", "reason": type(exc).__name__}
    ps = command(["ollama", "ps"])
    available_models = command(["ollama", "list"])
    checks["model"] = {"sec27_running": "sec-27b:latest" in ps, "gpu_100pct": "100% GPU" in ps,
                       "compressor_model_installed": "qwen3-coder-compress:64k" in available_models}
    checks["components"] = {
        "desktop_app_exists": (root / "apps/desktop/release/mac-arm64/Hermes.app").is_dir(),
        "preflight_gate_exists": (root / "agent/turn_preflight_gate.py").is_file(),
        "compression_module_exists": (root / "agent/context_compressor.py").is_file(),
        "lease_facade_exists": (root / "agent/turn_facade_lease.py").is_file(),
        "state_lease_modules_exist": (root / "hermes_state.py").is_file() and (root / "hermes_state_compression.py").is_file(),
    }
    # Syntax-only source checks: compile in memory without importing Hermes or
    # creating bytecode caches in the upstream checkout. This is NOT an E2E test.
    sources = {
        "preflight": root / "agent/turn_preflight_gate.py",
        "conversation": root / "agent/conversation_loop.py",
        "compression": root / "agent/context_compressor.py",
        "compression_controller": root / "agent/conversation_compression.py",
        "lease_facade": root / "agent/turn_facade_lease.py",
        "lease_state": root / "hermes_state.py",
        "compression_state": root / "hermes_state_compression.py",
    }
    checks["source_syntax"] = {}
    for label, source in sources.items():
        if not source.is_file():
            checks["source_syntax"][label] = "MISSING (may have moved upstream)"
            continue
        try:
            compile(source.read_bytes(), str(source), "exec")
            checks["source_syntax"][label] = "OK"
        except (SyntaxError, OSError, ValueError):
            checks["source_syntax"][label] = "INVALID"
    pmset = command(["pmset", "-g", "assertions"])
    checks["power"] = {"amphetaminesleep_assertion_seen": "Amphetamine" in pmset and "PreventUserIdleSystemSleep" in pmset,
                        "status": "Not enough to prove lid-close immunity; test after reboot while safely ventilated"}
    reference = repo / "monitor/desktop_guard.py"
    expected = sha(reference.read_bytes()) if reference.is_file() else ""
    checks["guard"] = guard_health(home, expected)
    checks["compression"] = compression_metrics(home / ".hermes/logs", datetime.now() - timedelta(hours=hours))
    checks["legacy"] = {"stash_present": bool(command(["git", "-C", str(root), "stash", "list"])),
                        "legacy_patches_active": bool(checks["backend"].get("state") == "incompatible" and not checks["backend"].get("target_clean", True)),
                        "readme": "Historical patch installers must not run on newer upstream releases"}
    launchctl = command(["launchctl", "list"])
    checks["autostart"] = {"guard_loaded": "com.local.hermes-desktop-guard" in launchctl,
                           "desktop_login_item": "manual (by user choice)",
                           "note": "Ollama/Amphetamine login items require macOS GUI or osascript verification"}
    guard = checks["guard"]
    data["observations"] = [
        "Guard telemetry is UNVERIFIED until independently matched with Desktop /context" if guard.get("verified") is False else "Guard telemetry freshness needs comparison with Desktop /context",
        "A passing compression event does not prove unattended recovery under exhausted budgets",
        "Observe-only monitoring never initiates compression, continuation, or remote Desktop control",
    ]
    return data


def cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--home", type=Path, default=Path.home())
    args = ap.parse_args(argv)
    if not 1 <= args.hours <= 168:
        ap.error("--hours must be 1..168")
    repo = Path(__file__).resolve().parent.parent
    data = evaluate(args.home, repo, args.hours)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print("Hermes Reliability Layer — read-only health audit")
        print("Checked:", data["audit_at"])
        for name, value in data["checks"].items():
            print("\n=== " + name.upper() + " ===")
            print(json.dumps(value, indent=2, ensure_ascii=False))
        print("\n=== NOTES ===")
        for line in data["observations"]:
            print("-", line)
    return 0

if __name__ == "__main__":
    raise SystemExit(cli())

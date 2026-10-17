"""Guarded install/verify/rollback for the verified-headroom rearm helper.

No automated/background modification. Install/rollback require Hermes Desktop/serve
stopped, a clean tracked target, and a structurally recognized upstream function.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

from .rearm import classification, function_under_test, patch, sha

REL = Path("agent/conversation_loop.py")
DEFAULT_ROOT = Path.home() / ".hermes/hermes-agent"
DEFAULT_BACKUPS = Path.home() / ".hermes/patch-backups/reliability-layer"
PROCESS_RE = re.compile(r"(?:^|[\s/])hermes\s+serve(?:\s|$)", re.I)


def active_processes(ps_output: str) -> list[str]:
    matches = []
    for line in ps_output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit() or int(fields[0]) == os.getpid():
            continue
        cmd = fields[1]
        exe = cmd.split(None, 1)[0]
        if "Hermes.app/Contents/" in cmd or (PROCESS_RE.search(cmd) and "cua-driver" not in exe.lower()):
            matches.append(fields[0])
    return matches


def live_processes() -> list[str]:
    ps = subprocess.run(["/bin/ps", "-axo", "pid=,command=", "-ww"], capture_output=True, text=True, check=True, timeout=10)
    return active_processes(ps.stdout)


def callers(root: Path) -> list[str]:
    """Conservative source check: a real AST Call to this helper somewhere in agent/."""
    import ast
    results = []
    folder = root / "agent"
    if not folder.is_dir():
        return results
    for path in folder.rglob("*.py"):
        if path == root / REL:
            continue
        try:
            t = ast.parse(path.read_text(encoding="utf-8"))
        except (UnicodeError, SyntaxError, OSError):
            continue
        for node in ast.walk(t):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                if name == "_should_rearm_compression_budget":
                    results.append(str(path.relative_to(root)) + ":" + str(node.lineno))
                    break
    return results


def git_target_clean(root: Path) -> bool:
    p = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", str(REL)],
                       text=True, capture_output=True, timeout=10)
    if p.returncode:
        raise RuntimeError("Git repository unavailable: " + p.stderr.strip())
    return not p.stdout.strip()


def git_head(root: Path) -> str:
    p = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    if p.returncode:
        raise RuntimeError("Cannot read current Git HEAD")
    return p.stdout.strip()


def atomic_write(dst: Path, contents: bytes) -> None:
    mode = stat.S_IMODE(dst.stat().st_mode)
    fd, name = tempfile.mkstemp(prefix=".reliability-", dir=dst.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(name, mode)
        os.replace(name, dst)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def inspect(root: Path) -> dict:
    target = root / REL
    if not target.is_file() or target.is_symlink():
        raise RuntimeError("Missing, nonregular or symlinked backend source: " + str(target))
    data = target.read_bytes()
    if len(data) > 3_000_000:
        raise RuntimeError("Suspiciously large backend module")
    return {"state": classification(data.decode("utf-8")), "sha256": sha(data), "head": git_head(root),
            "target_clean": git_target_clean(root), "callers": callers(root)}


def run_selftest(root: Path) -> None:
    text = (root / REL).read_text(encoding="utf8")
    before = classification(text)
    if before == "supported":
        text = patch(text)
    if classification(text) != "installed":
        raise RuntimeError("No compatible installed or upstream helper for self-test")
    fn = function_under_test(text)
    cases = [
        (7, True, 46979, 40960, True),  # Oct 17 verified headroom
        (7, True, 49152, 40960, True),
        (7, True, 49153, 40960, False),
        (7, True, 55000, 40960, False),
        (0, True, 46979, 40960, False),
        (7, False, 46979, 40960, False),
        (7, True, 0, 40960, False),
        (7, True, 39000, 40960, True),
        (7, True, 46000, 50000, True),
        (7, True, 55000, 50000, False),
    ]
    for attempts, done, used, threshold, expected in cases:
        actual = fn(attempts, completed_compaction_pending=done, prompt_tokens=used, threshold_tokens=threshold)
        if actual is not expected:
            raise RuntimeError(f"Self-test failed for {attempts}, {done}, {used}, {threshold}: {actual}")


def validate_user_config(config_file: Path) -> None:
    """Validate exact known Ollama parameters, never echo secrets."""
    from .config_reader import check_config
    check_config(config_file)


def install(root: Path, backup_root: Path, config: Path) -> str:
    info = inspect(root)
    if info["state"] == "installed":
        return "ALREADY INSTALLED — no change"
    if info["state"] != "supported":
        raise RuntimeError("Upstream rearm function changed; refusing. No source modified")
    if not info["target_clean"]:
        raise RuntimeError("Target has uncommitted changes; review before installing")
    if not info["callers"]:
        raise RuntimeError("No verified call site for rearm helper; refusing to install unproven patch")
    validate_user_config(config)
    run_selftest(root)
    if live_processes():
        raise RuntimeError("Hermes Desktop/serve still running (PIDs " + ", ".join(live_processes()) + "). Close before installing")
    target = root / REL
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = backup_root / ".lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if live_processes():
            raise RuntimeError("Hermes restarted while waiting for lock; no change")
        if sha(target.read_bytes()) != info["sha256"] or git_head(root) != info["head"]:
            raise RuntimeError("Source changed while checking; no change")
        old = target.read_bytes()
        new = patch(old.decode("utf8")).encode("utf8")
        folder = backup_root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        folder.mkdir(mode=0o700)
        backup = folder / "conversation_loop.py.original"
        backup.write_bytes(old)
        backup.chmod(0o600)
        meta = {"format": 1, "head": info["head"], "target": str(target), "original_sha256": sha(old),
                "patched_sha256": sha(new), "backup": str(backup), "installed_at": datetime.now().isoformat()}
        manifest = folder / "manifest.json"
        manifest.write_text(json.dumps(meta, indent=2) + "\n")
        manifest.chmod(0o600)
        try:
            atomic_write(target, new)
            if sha(target.read_bytes()) != sha(new):
                raise RuntimeError("Post-install SHA mismatch")
        except BaseException:
            atomic_write(target, old)
            raise
    return f"INSTALLED verified-headroom v1\nBackup: {backup}\nSHA256: {sha(new)}\nRestart Hermes manually."


def rollback(root: Path, backups: Path) -> str:
    target = root / REL
    manifests = sorted(backups.glob("*/manifest.json"), reverse=True)
    if not manifests:
        raise RuntimeError("No backup manifests")
    data = json.loads(manifests[0].read_text())
    backup = Path(data["backup"])
    if data.get("target") != str(target) or not backup.is_file():
        raise RuntimeError("Invalid manifest or missing backup")
    if not target.is_file() or target.is_symlink():
        raise RuntimeError("Target is missing or a symlink")
    if sha(target.read_bytes()) != data["patched_sha256"] or sha(backup.read_bytes()) != data["original_sha256"]:
        raise RuntimeError("Current backend changed; refuse to overwrite or roll back across updates")
    if live_processes():
        raise RuntimeError("Close Hermes Desktop/serve before rollback")
    with (backups / ".lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if live_processes() or sha(target.read_bytes()) != data["patched_sha256"]:
            raise RuntimeError("Runtime/source changed while waiting")
        atomic_write(target, backup.read_bytes())
        if sha(target.read_bytes()) != data["original_sha256"]:
            raise RuntimeError("Rollback verification failed")
    return "ROLLED BACK to exact original upstream source: " + str(backup)


def cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    for name in ("check", "plan", "install", "rollback", "self-test"):
        group.add_argument("--" + name, action="store_true")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--backups", type=Path, default=DEFAULT_BACKUPS)
    ap.add_argument("--config", type=Path, default=Path.home()/".hermes/config.yaml")
    args = ap.parse_args(argv)
    try:
        if args.check or args.plan:
            info = inspect(args.root)
            print("Git HEAD:", info["head"])
            print("Backend SHA256:", info["sha256"])
            print("Rearm helper:", info["state"])
            print("Target clean in Git:", info["target_clean"])
            print("Known call sites:", ", ".join(info["callers"]) or "NONE — needs investigation")
            print("Hermes running PIDs:", ", ".join(live_processes()) or "none")
            if args.plan:
                if info["state"] == "supported":
                    validate_user_config(args.config)
                    run_selftest(args.root)
                print("Action:", "Install eligible after stopping Hermes" if info["state"] == "supported" and info["target_clean"] and info["callers"] else "No safe installation planned")
        elif getattr(args, "self_test"):
            run_selftest(args.root)
            print("PASS: verified-headroom behavior (10 cases)")
        elif args.install:
            print(install(args.root, args.backups, args.config))
        else:
            print(rollback(args.root, args.backups))
        return 0
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(cli())

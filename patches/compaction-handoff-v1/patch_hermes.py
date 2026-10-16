#!/usr/bin/env python3
"""Minimal, reversible Hermes v0.21.7 context-compaction handoff fix.

One-line change: use the *last* summary boundary, not the first, when
checking whether a replay is present on a compressed carrier.

Safety: strict commit/source fingerprint checks, no DB changes, external
backups, compile check, targeted regressions, hash-checked rollback.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

EXPECTED_COMMIT_PREFIX = "71d34bad10"
FILENAME = "agent/context_compressor.py"
OLD = "if carrier_text.split(_SUMMARY_END_MARKER, 1)[1].strip():"
NEW = "if carrier_text.rsplit(_SUMMARY_END_MARKER, 1)[1].strip():"
SIBLING_GUARD = "_, boundary, remainder = text.rpartition(_SUMMARY_END_MARKER)"
REGISTRY_NAME = "hermes-compaction-handoff-v1"


def hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run_git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    if p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def roots() -> tuple[Path, Path, Path]:
    repo = Path.home() / ".hermes" / "hermes-agent"
    src = repo / FILENAME
    backup_home = Path.home() / ".hermes" / "patch-backups" / REGISTRY_NAME
    if not src.is_file() or not (repo / ".git").exists():
        raise RuntimeError("Brak instalacji Git: ~/.hermes/hermes-agent")
    return repo, src, backup_home


def function_names(source: str) -> set[str]:
    tree = ast.parse(source)
    return {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def validate_source(source: str, *, expect_patch: bool) -> None:
    ast.parse(source)
    if SIBLING_GUARD not in source:
        raise RuntimeError("Brak kontrolnego rpartition w _has_merged_inflight_replay. Odmowa zmiany.")
    names = function_names(source)
    if not {"_has_merged_inflight_replay", "_reappend_inflight_user_task", "_restated_request_text"}.issubset(names):
        raise RuntimeError("Nie zgadzają się funkcje kompresora. Odmowa zmiany.")
    token = NEW if expect_patch else OLD
    if source.count(token) != 1:
        raise RuntimeError(f"Oczekiwano dokładnie jednego wystąpienia {token!r}; znaleziono {source.count(token)}.")
    if not expect_patch and NEW in source:
        raise RuntimeError("Znaleziono częściową/mieszaną poprawkę. Odmowa zmiany.")
    if expect_patch and OLD in source:
        raise RuntimeError("Poprawka nie zastąpiła starego warunku.")


def atomic_write(path: Path, data: bytes) -> None:
    mode = path.stat().st_mode & 0o777
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def check(repo: Path, src: Path, source: str) -> None:
    commit = run_git(repo, "rev-parse", "HEAD")
    if not commit.startswith(EXPECTED_COMMIT_PREFIX):
        raise RuntimeError(f"Nieoczekiwany commit: {commit[:12]}; wymagany {EXPECTED_COMMIT_PREFIX}.")
    status = run_git(repo, "status", "--porcelain", "--untracked-files=no")
    if status:
        raise RuntimeError(f"Repozytorium ma własne zmiany; niczego nie nadpisuję:\n{status}")
    validate_source(source, expect_patch=False)
    compile(source, str(src), "exec")
    print(f"OK — commit {commit[:12]}, czysty Git, zgodny kod, poprawka jeszcze niezałożona")


def apply(repo: Path, src: Path, backup_home: Path, source: str) -> None:
    check(repo, src, source)
    before = src.read_bytes()
    updated = source.replace(OLD, NEW)
    validate_source(updated, expect_patch=True)
    compile(updated, str(src), "exec")
    base_hash, changed_hash = hash_bytes(before), hash_bytes(updated.encode("utf-8"))
    dirname = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_home / dirname
    if dest.exists():
        raise RuntimeError("Kolizja folderu kopii. Spróbuj ponownie za sekundę.")
    dest.mkdir(parents=True, mode=0o700)
    backup = dest / "context_compressor.py.original"
    backup.write_bytes(before)
    os.chmod(backup, 0o600)
    meta = {
        "kind": REGISTRY_NAME,
        "commit": run_git(repo, "rev-parse", "HEAD"),
        "target": str(src),
        "backup": str(backup),
        "original_sha256": base_hash,
        "patched_sha256": changed_hash,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    (dest / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(dest / "manifest.json", 0o600)
    atomic_write(src, updated.encode("utf-8"))
    if hash_bytes(src.read_bytes()) != changed_hash:
        atomic_write(src, before)
        raise RuntimeError("Błąd weryfikacji zapisu: oryginał przywrócony.")
    print(f"ZASTOSOWANO — {src}")
    print(f"KOPIA — {backup}")
    print("Zmiana aktywuje się w nowym procesie Hermes Desktop. Nie restartuję aplikacji.")


def rollback(repo: Path, src: Path, backup_home: Path) -> None:
    if not backup_home.exists():
        raise RuntimeError("Brak folderu kopii zapasowych. Nic nie zmieniono.")
    manifests = sorted(backup_home.glob("*/manifest.json"), reverse=True)
    if not manifests:
        raise RuntimeError("Brak manifestu kopii. Nic nie zmieniono.")
    meta = json.loads(manifests[0].read_text(encoding="utf-8"))
    if meta.get("kind") != REGISTRY_NAME or meta.get("target") != str(src):
        raise RuntimeError("Manifest nie pasuje do instalacji. Nic nie zmieniono.")
    actual = src.read_bytes()
    if hash_bytes(actual) != meta["patched_sha256"]:
        if hash_bytes(actual) == meta["original_sha256"]:
            print("Poprawka jest już cofnięta.")
            return
        raise RuntimeError("Plik zmienił się od instalacji poprawki. Nie nadpisuję nowych zmian.")
    original = Path(meta["backup"]).read_bytes()
    if hash_bytes(original) != meta["original_sha256"]:
        raise RuntimeError("Błąd integralności kopii zapasowej. Nic nie zmieniono.")
    atomic_write(src, original)
    print("COFNIĘTO — oryginalny plik przywrócony, bez zmian w state.db")
    print("Zmiana aktywuje się po ponownym uruchomieniu Hermes Desktop.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Tylko sprawdź czy plik pasuje")
    group.add_argument("--apply", action="store_true", help="Zrób backup i popraw jedną linię")
    group.add_argument("--rollback", action="store_true", help="Cofnij z kopii gdy hash się zgadza")
    args = p.parse_args()
    try:
        repo, src, backup_home = roots()
        source = src.read_text(encoding="utf-8")
        if args.check:
            check(repo, src, source)
        elif args.apply:
            apply(repo, src, backup_home, source)
        else:
            rollback(repo, src, backup_home)
    except Exception as ex:
        print(f"STOP — {ex}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

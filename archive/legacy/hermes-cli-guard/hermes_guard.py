#!/usr/bin/env python3
"""Hermes CLI Guard: terminal-only, conservative context-failure recovery.

Python standard library only. Does not modify Hermes's database or config.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import pathlib
import pty
import re
import select
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import termios
import time
import tty
import urllib.request
from urllib.parse import quote

APP_NAME = "Hermes CLI Guard"
HOME = pathlib.Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
GUARD_DIR = HOME / "cli-guard"
BACKUP_DIR = GUARD_DIR / "backups"
NOTE_DIR = GUARD_DIR / "handoffs"
LOG_FILE = GUARD_DIR / "guard.log"
MIN_IDLE = int(os.environ.get("HERMES_GUARD_IDLE_SECONDS", "180"))
MAX_RECOVERS = int(os.environ.get("HERMES_GUARD_MAX_RECOVERS", "2"))
WINDOW_SECONDS = 3600
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
FATAL = re.compile(
    r"context_length_exceeded|(?:maximum|configured|model|maximum model)\s+context\s+length\s+(?:is\s+)?exceed(?:ed|s)?"
    r"|(?:context|prompt|input)\s+(?:window|length|size|tokens?)\s+(?:was\s+)?(?:exceeded|overflow|too\s+(?:long|large))"
    r"|(?:exceeded|exceeds)\s+(?:the\s+)?(?:context\s+window|context\s+length|maximum\s+tokens)"
    r"|(?:prompt|input)\s+(?:is\s+)?too\s+long\s+(?:for\s+)?(?:the\s+)?(?:context|model)"
    r"|max\s+compression\s+attempts\s+reached",
    re.I,
)
FAIL_COMPRESS = re.compile(
    r"compression\s+(?:skipped|blocked|aborted)|compress(?:ion)?\s+failed|failed\s+to\s+compress",
    re.I,
)
SESSION_ID = re.compile(r"\b20\d{6}_\d{6}_[a-fA-F0-9]{6,12}\b")


def ensure_dirs() -> None:
    for directory in (GUARD_DIR, BACKUP_DIR, NOTE_DIR):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)


def say(message: str) -> None:
    msg = f"[{APP_NAME}] {message}"
    print("\r\n" + msg + "\r\n", file=sys.stderr, flush=True)
    try:
        ensure_dirs()
        with LOG_FILE.open("a", encoding="utf-8") as log:
            log.write(dt.datetime.now().isoformat(timespec="seconds") + " " + message + "\n")
        os.chmod(LOG_FILE, 0o600)
    except OSError:
        pass


def ollama_ok() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def hermes_binary() -> str:
    env_path = os.environ.get("HERMES_GUARD_HERMES")
    binary = env_path or shutil.which("hermes")
    if not binary:
        raise RuntimeError("Nie znaleziono wykonywalnego programu 'hermes' w PATH")
    path = pathlib.Path(binary).resolve()
    if path == pathlib.Path(__file__).resolve():
        raise RuntimeError("Wykryto zapętlenie: ścieżka 'hermes' wskazuje na watchdoga")
    return str(path)


def database() -> pathlib.Path:
    return HOME / "state.db"


def open_db(path: pathlib.Path):
    # SQLite read-only mode; safely reads a WAL database without changing rows.
    return sqlite3.connect("file:" + quote(str(path)) + "?mode=ro", uri=True, timeout=3)


def snapshot_database() -> pathlib.Path | None:
    path = database()
    if not path.exists():
        say("Brak state.db — przygotuję nową sesję bez kopii bazy.")
        return None
    ensure_dirs()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = BACKUP_DIR / f"state-{stamp}-{os.getpid()}.db"
    try:
        needed = max(path.stat().st_size * 2, 32 * 1024 * 1024)
        if shutil.disk_usage(BACKUP_DIR).free < needed:
            say("Za mało miejsca na spójną kopię bazy; NIE przeprowadzam automatycznej rotacji.")
            return None
        with open_db(path) as source:
            with sqlite3.connect(str(destination)) as target:
                source.backup(target, pages=256, sleep=0.1)
        os.chmod(destination, 0o600)
        say("Zachowano spójną kopię historii: " + str(destination))
        return destination
    except Exception as exc:
        try:
            destination.unlink(missing_ok=True)
        except Exception:
            pass
        say("Błąd kopii SQLite: " + str(exc))
        return None


def fetch_handoff(snapshot: pathlib.Path, preferred_id: str | None, started_at: float) -> tuple[str | None, str]:
    """Extract bounded non-tool transcript. Does not attempt to re-run commands."""
    with open_db(snapshot) as connection:
        connection.row_factory = sqlite3.Row
        columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
        msg_cols = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
        if not {"id", "source"}.issubset(columns) or not {"session_id", "role", "content"}.issubset(msg_cols):
            raise RuntimeError("Nieznany schemat bazy sesji")
        # A detected session ID is more trustworthy than a global 'latest' session.
        selected = None
        if preferred_id:
            selected = connection.execute(
                "SELECT id FROM sessions WHERE id = ? AND source IN ('cli','tui','oneshot')",
                (preferred_id,),
            ).fetchone()
        if selected is None:
            cwd_clause = "AND s.cwd = ?" if "cwd" in columns else ""
            params = [os.getcwd()] if cwd_clause else []
            sql = ("SELECT s.id, MAX(m.timestamp) AS activity FROM sessions s "
                   "JOIN messages m ON m.session_id=s.id "
                   "WHERE s.source IN ('cli','tui','oneshot') " + cwd_clause + " "
                   "GROUP BY s.id ORDER BY activity DESC LIMIT 2")
            rows = connection.execute(sql, params).fetchall()
            # Without a confident match, fail closed instead of importing unrelated work.
            if rows and float(rows[0]["activity"] or 0) >= started_at - 60:
                if len(rows) == 1 or (float(rows[0]["activity"] or 0) - float(rows[1]["activity"] or 0) >= 30):
                    selected = rows[0]
        if selected is None:
            return None, "Nie udało się jednoznacznie rozpoznać sesji. Historia pozostaje w state.db."
        sid = selected["id"]
        condition = "AND (active = 1 OR active IS NULL)" if "active" in msg_cols else ""
        rows = connection.execute(
            "SELECT role, content FROM messages WHERE session_id=? AND role IN ('user','assistant') "
            + condition + " ORDER BY id DESC LIMIT 18", (sid,)
        ).fetchall()
        parts = [f"Poprzednia sesja: {sid}", "Tylko skrócone ostatnie wiadomości — nie zawierają pełnego przebiegu narzędzi."]
        budget = 13000
        for item in reversed(rows):
            val = item["content"] or ""
            if not isinstance(val, str):
                continue
            val = val.replace("\x00", "")[:1800]
            chunk = f"\n[{item['role']}] {val}\n"
            if len(chunk) > budget:
                chunk = chunk[:budget]
            if not chunk:
                break
            parts.append(chunk)
            budget -= len(chunk)
            if budget < 100:
                break
        return sid, "\n".join(parts)


def write_handoff(snapshot: pathlib.Path | None, preferred_id: str | None, started_at: float) -> tuple[str | None, pathlib.Path | None]:
    if snapshot is None:
        return None, None
    try:
        session_id, text = fetch_handoff(snapshot, preferred_id, started_at)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        filepath = NOTE_DIR / f"handoff-{stamp}-{os.getpid()}.txt"
        filepath.write_text(text + "\n", encoding="utf-8")
        os.chmod(filepath, 0o600)
        return session_id, filepath
    except Exception as exc:
        say("Nie udało się utworzyć notatki kontynuacji: " + str(exc))
        return None, None


def latest_new_session(since: float) -> str | None:
    try:
        with open_db(database()) as connection:
            rows = connection.execute(
                "SELECT id,started_at FROM sessions WHERE source IN ('oneshot','cli','tui') "
                "AND started_at >= ? ORDER BY started_at DESC LIMIT 3", (since - 1,)
            ).fetchall()
        return rows[0][0] if rows and float(rows[0][1]) >= since - 1 else None
    except Exception:
        return None


def preseed_recovered_session(binary: str, handoff_path: pathlib.Path | None) -> str | None:
    if handoff_path is None:
        return None
    prompt = (
        "To nowa sesja po awaryjnym przepełnieniu kontekstu poprzedniej sesji. "
        "Przeczytaj lokalny plik kontynuacji (tylko odczyt): " + str(handoff_path) + ". "
        "Napisz krótką notatkę o aktualnym stanie rozmowy. Nie wykonuj wcześniejszych zadań, "
        "nie powtarzaj poleceń terminala ani operacji ze skutkami ubocznymi. "
        "Poczekaj na następne polecenie użytkownika."
    )
    say("Odtwarzam krótki kontekst w NOWEJ sesji. Nie powtarzam poleceń terminala.")
    since = time.time()
    try:
        run = subprocess.run(
            [binary, "chat", "-q", prompt], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
            timeout=240, check=False,
        )
        if run.returncode != 0:
            say("Nie udało się przygotować sesji startowej. Zapisano plik kontynuacji.")
            return None
        return latest_new_session(since)
    except (OSError, subprocess.TimeoutExpired) as exc:
        say("Błąd sesji startowej: " + str(exc))
        return None


def process_exit_code(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 1


def copy_winsize(master: int):
    if sys.stdin.isatty():
        try:
            packed = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
            fcntl.ioctl(master, termios.TIOCSWINSZ, packed)
        except OSError:
            pass


def run_pty(cmd: list[str], start: float) -> tuple[int, bool, str | None]:
    """Run child with a real PTY. Only recover for observed fatal error + prolonged silence."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        say("Uruchom w interaktywnym terminalu (TTY).")
        return 2, False, None
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execv(cmd[0], cmd)
        except Exception as exc:
            print("Hermes CLI Guard exec failed:", str(exc), file=sys.stderr)
            os._exit(127)
    copy_winsize(master)
    old_attr = termios.tcgetattr(sys.stdin.fileno())
    old_winch = signal.getsignal(signal.SIGWINCH)
    signal.signal(signal.SIGWINCH, lambda *_: copy_winsize(master))
    tty.setraw(sys.stdin.fileno())
    latest_output = time.monotonic()
    latest_input = latest_output
    fatal_seen = False
    terminal_context_failure = False
    failure_hits = 0
    tail = ""
    preferred_sid = None
    last_notice = 0.0
    last_compress_warn = 0.0
    stop_stage = 0
    stop_at = 0.0
    recovered = False
    status = 0
    try:
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            try:
                readable, _, _ = select.select([sys.stdin.fileno(), master], [], [], 0.5)
            except (OSError, ValueError):
                break
            now = time.monotonic()
            if master in readable:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    chunk = b""
                if chunk:
                    latest_output = now
                    try:
                        os.write(sys.stdout.fileno(), chunk)
                    except OSError:
                        pass
                    cleaned = ANSI.sub("", chunk.decode("utf-8", errors="replace"))
                    tail = (tail + cleaned)[-5000:]
                    ids = SESSION_ID.findall(cleaned)
                    if ids:
                        preferred_sid = ids[-1]
                    if FATAL.search(cleaned):
                        fatal_seen = True
                        terminal_context_failure = True
                    if FAIL_COMPRESS.search(cleaned) and now - last_compress_warn >= 20:
                        failure_hits += 1
                        last_compress_warn = now
                        # A single warning is not enough evidence that the conversation is dead.
                        if failure_hits >= 3:
                            fatal_seen = True
                    if terminal_context_failure and len(cleaned) >= 4000 and not FATAL.search(cleaned):
                        # Substantial successful subsequent output weakens evidence of a stall.
                        terminal_context_failure = False
                        fatal_seen = False
                        failure_hits = 0
                else:
                    # PTY closed: allow waitpid to collect the child.
                    time.sleep(0.05)
            if sys.stdin.fileno() in readable:
                try:
                    typed = os.read(sys.stdin.fileno(), 65536)
                except OSError:
                    typed = b""
                if not typed:
                    # Closed terminal: exit supervision, but do not kill a busy child.
                    break
                latest_input = now
                try:
                    os.write(master, typed)
                except OSError:
                    break
            idle = min(now - latest_input, now - latest_output)
            if stop_stage == 0 and fatal_seen and idle >= MIN_IDLE:
                if not ollama_ok():
                    if now - last_notice > 90:
                        say("Błąd kontekstu, ale Ollama nie odpowiada. Nie restartuję sesji w ciemno.")
                        last_notice = now
                else:
                    say("Potwierdzony komunikat awarii kontekstu i długa cisza. Próbuję łagodnie przerwać Hermesa.")
                    try:
                        os.killpg(pid, signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    stop_stage, stop_at = 1, now
            elif stop_stage == 1 and now - stop_at >= 15:
                # Terminal is still stuck after graceful interrupt. SIGTERM only after 15s.
                say("Hermes nie zakończył pracy po SIGINT. Wysyłam SIGTERM (bez SIGKILL).")
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                stop_stage, stop_at = 2, now
            elif stop_stage == 2 and now - stop_at >= 15:
                say("Proces nie zakończył się; nie wymuszam SIGKILL, aby nie uszkodzić zadań.")
                stop_stage = 3
        recovered = stop_stage in (1, 2) or (terminal_context_failure and process_exit_code(status) != 0)
        return process_exit_code(status), recovered, preferred_sid
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attr)
        signal.signal(signal.SIGWINCH, old_winch)
        try:
            os.close(master)
        except OSError:
            pass


def doctor() -> int:
    print("Hermes CLI Guard — diagnostyka")
    print("  Hermes home:", HOME)
    try:
        print("  Hermes:", hermes_binary())
    except RuntimeError as exc:
        print("  Hermes: BŁĄD:", exc)
        return 1
    print("  Ollama (127.0.0.1:11434):", "OK" if ollama_ok() else "NIEDOSTĘPNA")
    print("  Baza:", database(), "(istnieje)" if database().exists() else "(brak)")
    print("  Automatyczny restart po:", MIN_IDLE, "s potwierdzonej awarii i ciszy")
    print("  Maks. rotacji w godzinę:", MAX_RECOVERS)
    print("  Konfiguracja Hermesa: nie jest zmieniana przez watchdoga")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Hermes terminal-only context watchdog")
    p.add_argument("--check", action="store_true", help="sprawdź środowisko, bez uruchamiania Hermesa")
    p.add_argument("args", nargs=argparse.REMAINDER, help="argumenty interaktywnego Hermesa")
    opts = p.parse_args()
    if opts.check:
        return doctor()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Hermes CLI Guard wymaga interaktywnego terminala", file=sys.stderr)
        return 2
    binary = hermes_binary()
    argv = [binary] + (opts.args[1:] if opts.args and opts.args[0] == "--" else opts.args)
    recoveries = []
    while True:
        started = time.time()
        code, need_recovery, selected_sid = run_pty(argv, started)
        if not need_recovery:
            return code
        now = time.time()
        recoveries = [t for t in recoveries if now - t < WINDOW_SECONDS]
        if len(recoveries) >= MAX_RECOVERS:
            say("Limit awaryjnych rotacji przekroczony. Nie restartuję w pętli.")
            return code or 1
        if not ollama_ok():
            say("Ollama jest niedostępna. Nie uruchamiam bezsensownie kolejnej sesji.")
            return code or 1
        snapshot = snapshot_database()
        if snapshot is None:
            say("Nie uzyskano kopii bazy. Dla bezpieczeństwa nie dokonuję rotacji.")
            return code or 1
        previous_id, handoff = write_handoff(snapshot, selected_sid, started)
        say("Zachowano starą sesję " + str(previous_id or "(ID nieznane)") + ". Przygotowuję nową.")
        successor_id = preseed_recovered_session(binary, handoff)
        if successor_id:
            say("Otwieram nową sesję: " + successor_id)
            argv = [binary, "--resume", successor_id]
        else:
            say("Nie udało się automatycznie przenieść kontekstu. Otwieram pustą sesję; notatka: " + str(handoff))
            argv = [binary]
        recoveries.append(now)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print("[Hermes CLI Guard] Błąd: " + str(exc), file=sys.stderr)
        sys.exit(1)

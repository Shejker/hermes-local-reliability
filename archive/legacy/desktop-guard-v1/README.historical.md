# Hermes Desktop Guard (macOS)

**Scope:** native Hermes Desktop app running its own loopback `hermes serve --port 0` backend. **Not** the CLI wrapper, messaging gateway, or Ollama service. No process restarts, no task interruptions, no editing Hermes files/SQLite databases.

## Installation

```sh
cd ~/Downloads/hermes-desktop-guard
chmod +x install.sh uninstall.sh
./install.sh
~/.hermes/desktop-guard/venv/bin/python3 ~/.hermes/desktop-guard/desktop_guard.py --check
```

Installer first performs a read-only API check and enables the LaunchAgent **only if it can connect**. The LaunchAgent then runs every 60 seconds while logged in. It needs the Mac awake and Hermes Desktop open. `--check` is read-only. `--once` enables the automatic context policy. If connectivity cannot be confirmed, installation leaves the LaunchAgent disabled. It does not silently claim protection.

## Policy

- Enumerates only local processes matching Hermes Desktop's `serve --host 127.0.0.1 --port 0`, discovers the real port via `lsof`, and authenticates to local WebSocket JSON-RPC.
- Reads `session.active_list` and `session.usage` per in-memory session.
- High pressure = **at least 68%** of the context window for **two distinct checks** at least 40 seconds apart.
- Compresses *only* sessions with status `idle`, last activity at least 120 seconds ago, and confirmed status `idle` again just before the operation.
- Creates SQLite online backups (using the SQLite backup API) of all found Hermes profile databases **before** compression. If no database or access token exists, skips the operation.
- At most one attempt per session per 30 minutes. Never sends `/new`, edits DB history, closes sessions, restarts Desktop or Ollama, or kills processes.
- If the app is busy or a session is not listed as live, it does **nothing** to it. Hermes' built-in auto-compression must still handle mid-turn growth.

## Logs / removal

```sh
tail -f ~/.hermes/desktop-guard/guard.log
./uninstall.sh
```

Backups: `~/.hermes/desktop-guard/backups`. They can contain sensitive conversation history; do not upload or share. Logs include session identifiers but not auth tokens or message contents.

## Limitations

**Unverified against your specific Desktop build.** Port discovery and local token availability vary by Hermes release. The guard fails closed when unauthenticated and emits a log diagnostic; it will not silently bypass auth. Some Hermes releases require an explicit API session token not provided by `/api/session-token` or the root HTML. Background execution only applies while the user's macOS session is awake/logged in; `launchd` is not a guarantee of 24/7 work on a sleeping laptop.

`session.compress` can take several minutes. If its WebSocket times out while the backend continues compressing, the cooldown avoids immediate retry. The Hermes server enforces a busy-session guard, but a concurrent race can still cause the compression RPC to be refused safely.

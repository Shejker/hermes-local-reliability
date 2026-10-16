# Hermes Desktop Guard v2.2 — strictly observe-only

This package upgrades an **already installed v2.1 guard** to v2.2. It is not a fresh-install package. The v2.1 release could incorrectly interrupt Hermes while Hermes itself was compacting its context, causing a stopped turn; avoid running v2.1 in production.

The v2.2 runtime (`desktop_guard_v22.py`) reads `session.active_list`, `session.usage`, and `session.context_breakdown`. It may write its own monitoring log/observations and optionally issue macOS notifications. It **never** calls `session.interrupt`, `session.compress`, `prompt.submit`, starts/stops Hermes, or modifies `state.db`. Legacy recovery phases are disabled.

### Upgrade an existing v2.1 guard

```bash
python3 test_guard.py
python3 install_guard.py --check
python3 install_guard.py --install
```

The installer expects a specific v2.1 SHA and preserves a backup under `~/.hermes/desktop-guard/source-backups-v22/`. It does not modify model configuration, Hermes Agent source or the SQLite database. The LaunchAgent `com.local.hermes-desktop-guard` normally runs every ~30 seconds.

**Warning:** `install_guard.py --rollback` restores unsafe v2.1 code and is **not** a safe way to recover an ordinary incident. Disable/stop the old Guard before considering such a rollback.

For the active runtime, check: `~/.hermes/desktop-guard/venv/bin/python3 ~/.hermes/desktop-guard/desktop_guard.py --version`; expect `2.2-observe-only`. Refer to `../../docs/OPERATIONS.md` for live log monitoring.

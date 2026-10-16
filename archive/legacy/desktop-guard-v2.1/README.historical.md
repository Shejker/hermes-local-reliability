# Hermes Desktop Context Guard v2.1 — macOS

Dedicated to **Hermes Desktop** (`hermes serve --host 127.0.0.1 --port 0`) and Ollama. **Does not use Hermes Gateway.** It updates the previous `com.local.hermes-desktop-guard` LaunchAgent rather than creating a second service. It does not change `~/.hermes/config.yaml` or installed models.

## Install/upgrade

With Hermes Desktop **open** and preferably no task currently running:

```bash
cd ~/Downloads/hermes-desktop-guard-v2.1
chmod +x install.sh uninstall.sh
./install.sh
```

This enables emergency interruption and automatic context compression, but **does not submit another prompt**. This is the safest policy for agents editing files or invoking commands with side effects. To opt in to automatic task continuation (risk of repeated actions, despite the safety prompt):

```bash
./install.sh --auto-continue
```

The installer keeps backups of the previous watchdog files and `launchd` plist in `~/.hermes/desktop-guard/install-backup/`. Previous session database backups are kept. Installs on top of existing v1, reuses the Python venv, checks live authenticated API before scheduling, and runs every 30 seconds. GUI is not restarted.

## Safety behavior

- For **all** sessions (`working` or `idle`): reads authenticated JSON-RPC `session.active_list` and `session.usage`; if context counters are missing, also attempts the read-only `session.context_breakdown` RPC. Logs the source and whether it is provider-confirmed; notifies at 75%.
- For `working` **with provider-confirmed usage only**: at **85%** during two checks separated by >=25 seconds, or **90%** in one check, rechecks the session and usage, backs up SQLite using the online backup API, then calls `session.interrupt` **once**. This *does interrupt the current agent turn* (including potential tool operations) to prevent it consuming all remaining context. Tools/processes started by the agent might continue on their own; these cannot be proven safe by a context watchdog.
- On confirmed `idle` over successive checks: takes another backup and calls `session.compress`, then checks that the *API-reported* context actually dropped to below 75% by at least 2048 tokens.
- If `--auto-continue` was selected and adequate context was restored, submits **one new cautious continuation prompt** to the same session. It never blindly resends the original command. Continuation may still repeat or alter real-world side effects, so do not use this option for important non-idempotent operations.
- If context usage is unavailable (common in lazy/uninitialized sessions), a compression fails, sessions remain busy, a subagent is reported active, or any required check fails: logs an alert and **stops rather than blindly restarting or editing the database**. An estimate-only context breakdown can show a warning but never authorizes interruption of a `working` session.
- Maximum 2 interruptions/recoveries per session per hour; no process `kill`, no Ollama restart, no `session.close`, no direct DB mutations, no programmatic deletion or branch.
- Errors from `session.interrupt` or `prompt.submit` may be ambiguous (remote operation could have occurred). For this reason it never blindly retries an ambiguous write operation.

### Important limitation

A poll-based watchdog **cannot guarantee stopping before 100%**. A single model response or tool result can grow the context past the limit *between two samples*, and `session.usage` may be updated only after a model call finishes. An interactive session can also remain stuck inside a noninterruptible operation. Thus this is a practical mitigation, not a fix for Hermes' internal compressor or a guaranteed 24/7 transaction-safe agent. Fixing this completely requires a compressor or hard context budget enforced inside the Hermes turn loop.

## Read-only status and log

```bash
~/.hermes/desktop-guard/venv/bin/python3 ~/.hermes/desktop-guard/desktop_guard.py --check
tail -40 ~/.hermes/desktop-guard/guard.log
launchctl print gui/$(id -u)/com.local.hermes-desktop-guard | grep -E 'runs =|last exit code|run interval'
```

## Change policy

Edit `~/.hermes/desktop-guard/policy.json` (loaded every cycle), e.g. set `auto_continue: false` to require manual continuation. Use a JSON boolean, not a string. Editing does not require a restart. When upgrading, using `./install.sh` disables auto-continue; `./install.sh --auto-continue` enables it.

## Disable

```bash
./uninstall.sh
```

This disables the LaunchAgent, preserving all backups and logs. Backups can contain the full sensitive chat history; do not share them.

## What `UNPROTECTED` means

If `session.usage` has no valid `context_used`/`context_max`, the guard asks `session.context_breakdown`. If that also cannot measure the context, the session is labeled `UNPROTECTED` and **no interrupt or compress is issued**. This can happen when the session has finished and its agent is unloaded; it may resume supplying usage after a new turn. The installer connects to the backend but **does not claim comprehensive coverage** if any session is unmeasured. Run `--check` after the GUI has an active task before relying on automatic interruption.

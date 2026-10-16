# Operation and monitoring

## System assumptions

- macOS Apple Silicon (observed M1 Max with 64 GiB RAM).
- Ollama local backend at `http://127.0.0.1:11434/v1`.
- Main context 65,536 tokens; default example model `sec-27b:latest`; compression auxiliary `qwen3-coder-compress:64k`.
- Guard v2.2 periodically runs as macOS LaunchAgent `com.local.hermes-desktop-guard` and is **observe-only**.

## Check health (read-only)

```bash
python3 tools/audit_install.py
~/.hermes/desktop-guard/venv/bin/python3 ~/.hermes/desktop-guard/desktop_guard.py --version
tail -n 20 ~/.hermes/desktop-guard/guard.log
tail -n 30 ~/.hermes/logs/agent.log
```

Follow events in a separate terminal:

```bash
tail -F ~/.hermes/logs/agent.log | grep -Ei --line-buffered \
 'session turn lease|lease lost|runway|compression attempt telemetry|Interrupt requested|Turn ended|no room to answer'
```

`tail -F` and `grep` are observation-only; Ctrl+C stops only the monitoring terminal.

## Read-only morning report

```bash
python3 helpers/hermes-morning-report.py --hours 16
```

The generated diagnostic archive **can include personal log content, paths and executed commands**. Review it before sending or sharing; do not commit it to this repository. Rotated agent logs (e.g., `agent.log.1`) may contain missing earlier events that the newest `agent.log` omits.

## Interpreting the log

- `commit_status=committed`: compression changed the state; examine `tokens_reclaimed` and `method`.
- `empty_post_handoff_window`, `would_grow`, `feasibility_skip`: may be recoverable and do not necessarily indicate failure.
- `Pre-API runway recovery`: guarded additional compression was attempted.
- `Pre-API runway guard BLOCKED`: oversized provider call was stopped; the agent may require manual intervention.
- `session turn lease lost`: real protective interruption, **not necessarily a user stop**.
- `background_review_superseded`: cancellation of a background review, often not a failed main turn.
- `usage_unchanged_s`: stale UI token usage, not proof of a stuck model.

## MacBook sleep / lid-close caveat

A real failure on 2026-10-15 at ~17:06 followed macOS sleep caused by closing the lid and switching to battery. A 300-second lease expired while normal Python processes were not running. Shortening lease refresh intervals **cannot renew a lease during system sleep**.

For safe unattended work, use AC power, ventilation and a supported awake mode. `caffeinate -i` prevents **idle sleep only**, not standard clamshell/lid-close sleep. If using Amphetamine or the optional Power Protect component to block clamshell sleep, verify it restores ordinary sleep after ending a session; never leave `sudo pmset -a disablesleep 1` permanently enabled, especially when carrying the laptop or placing it in a bag. To inspect: `pmset -g assertions`, `pmset -g | grep SleepDisabled`, and `pmset -g log`.

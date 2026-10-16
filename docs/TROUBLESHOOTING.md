# Known issues, observed tests and diagnostics

| Date / test | Observed result | Interpretation |
|---|---|---|
| 2026-10-14, before Recovery v2 | Ollama reached 65,401/65,536 context tokens and failed | Compression budget/fallthrough path needed a fail-closed safety runway |
| 2026-10-14 to 2026-10-15, 10-hour run | 34/36 successful compactions, 134 model calls | Strong overnight reliability baseline, not an exhaustive failure test |
| 2026-10-15, ~12:34–16:23 | Guard read-only monitoring and native compactions worked | Two patch sets coexisted without a visible new error |
| 2026-10-15, 17:06 | `session turn lease lost` and protective interruption | macOS `Clamshell Sleep` on battery after ~16:56 confirmed by `pmset` logs; manual resumption after wake |
| 2026-10-15–16, following overnight window | Many successful compactions and no context overflow | Recovery v2 extra-attempt edge case still not reproduced in live production |

These statements summarize prior *local diagnostics*; this repository intentionally does not ship private full log exports or `state.db`.

### When a new failure occurs

1. Save the exact local timestamp and whether you actually pressed Stop or resumed the turn.
2. Capture `agent.log` **and rotated** `agent.log.1` if the incident predates rotation; inspect `desktop.log`, `errors.log`, `guard.log` as appropriate.
3. Search for `session turn lease lost`, `Pre-API runway recovery`, `Pre-API runway guard BLOCKED`, `context compression attempt telemetry`, `Interrupt requested`, `Turn ended`, and provider error text.
4. If sleep is suspected, inspect `pmset -g log` around the timestamp; maintenance `DarkWake` is not evidence of sustained normal application execution.
5. Avoid blindly restarting or deleting state; use the existing backup and hash-checked rollback tooling only after the backend has stopped.

### Unverified limits

- Recovery v2's one-extra-attempt edge path has unit tests, but no confirmed production hit in the documented overnight run.
- Heartbeat v1 cannot maintain a time-limited DB lease while the entire Mac is asleep.
- Guard v2.2 is deliberately observe-only and cannot autonomously resume a failed main turn.
- Long-term stability depends on macOS power management, RAM/GPU capacity, Ollama behavior, SQLite responsiveness, and safe replay of external tool side effects.

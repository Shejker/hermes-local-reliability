# Hermes local Ollama pre-API runway guard v1 — experimental

This is a narrowly scoped **fail-closed safety patch**, **not** a fix for the underlying compression budget/rearm logic, and **not** unattended 24/7 operation. It changes exactly one Hermes source file (`agent/turn_preflight_gate.py`). It does not change the main model, Qwen, context size, Hermes DB, context compressor, or Desktop Guard.

## Evidence from the supplied source and logs

- `turn_preflight.run_preflight_compression()` makes proactive pre-API compression conditional on `compression_attempts < max_compression_attempts`.
- The function may return `fallthrough` without calling the compressor after the attempt budget is spent, unless the provider-overflow flag has already been set. This is a possible route to sending requests without enough output room.
- `_should_rearm_compression_budget()` only rearms after a provider-measured prompt falls *below* the compression threshold, which may explain prolonged lockout after a partially successful compression. Its **actual usage in `turn_response_intake.py` was not included** in the source bundle; the exact root cause remains unconfirmed.
- In the user's logs, after a 19:35 fallback, the next provider prompt was 43,367 tokens (>40,960 threshold); later requests reached 57,565, 61,069, 64,073 and finally failed at 65,401/65,536.

## What the patch does

1. Keeps the original `run_preflight_compression` decision first. If it chooses `continue` (successful compression), `return` (provider recovery result), or `break`, this patch does **nothing**.
2. Adds a privacy-safe `Pre-API pressure audit:` log showing prompt estimate, attempt budget, and whether the request was usage-anchored. No message text is logged.
3. Only for locally hosted Ollama at `localhost` / `127.0.0.1` / `::1` port 11434 with `api_mode=chat_completions`, checks if preflight was about to send a request without enough space reserved for model output. With a 65,536-token window, the estimated input cutoff is **57,344 tokens**, reserving 8,192 tokens.
4. If the existing compaction path would otherwise send it, the guard produces a readable error asking for `/compress`, refunds the provisional call budget, and ends that turn *before* contacting Ollama. It does **not** send an interrupt, start any additional compression, or auto-retry tools.

### Important limitations

- This may **stop a long-running task**; it is a safety net, not an auto-recovery solution. Resume manually after successful `/compress`. This is preferable to a near-100% context error, but not the user's final autonomous goal.
- The check uses Hermes' `request_pressure_tokens`, which may differ from Ollama's true tokenizer count; a large underestimate can still overflow. Conversely a high rough estimate could conservatively block a request that would fit. It cannot guarantee exact model context size.
- The change has passed **isolated gate behavior tests**, not full Hermes integration/regression tests. No full repository or live macOS/Ollama environment was available here. Test first with a non-critical session if possible.
- This patch does not fix `session turn lease lost`, `STILL IN PROGRESS`, or interruptions after manual compression.

## How to install safely

**Do not run the installer during an active Hermes task.** Finish/pause the work, close Hermes Desktop entirely, and verify it is no longer running. Keep a copy of the ZIP.

```bash
cd ~/Downloads/hermes-preflight-runway-v1
python3 tests/test_runway.py
python3 tests/test_installer.py
python3 install.py --check
python3 install.py --install
```

The installer refuses to edit files if Hermes Desktop is open, if the local target SHA256 differs from the supplied source, or if a previous install record exists. It atomically updates `~/.hermes/hermes-agent/agent/turn_preflight_gate.py`, preserves permissions, and backs up the original under `~/.hermes/patch-backups/preflight-runway-v1/`. It never restarts processes.

Restart Hermes Desktop, then check:

```bash
grep -E 'Pre-API pressure audit:|Pre-API runway guard BLOCKED' ~/.hermes/logs/agent.log | tail -20
```

A `Pre-API runway guard BLOCKED` entry means it stopped an oversized request; use `/compress` and `/context` before manually resuming. For more complete automation we need to inspect the source of usage-anchored budget rearming and the compressor's `should_compress_info()` behavior.

## Rollback

Close Hermes Desktop before rollback, then:

```bash
cd ~/Downloads/hermes-preflight-runway-v1
python3 install.py --rollback
```

The rollback refuses to delete unknown subsequent edits. Restart Hermes Desktop after rollback.

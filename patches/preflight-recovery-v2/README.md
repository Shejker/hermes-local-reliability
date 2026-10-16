# Hermes preflight recovery v2 — opt-in, single rescue compression

Target: the user's locally patched Hermes Agent at v0.21.7 / commit `71d34bad1045` with **Preflight Runway v1** already installed. This package does NOT patch Hermes Desktop Guard and does NOT patch turn leases.

## What it does

When `run_preflight_gate` would otherwise allow an oversized prompt through to Ollama and:
- locally hosted Ollama chat-completions context is >=16384;
- estimated request pressure is at least context minus an 8192-token reservation for 65536 context;
- the configured compression attempt budget is already exhausted;
- the pressure estimate is anchored on provider usage;
- there is no failure cooldown, no active unconfirmed post-compression usage, no anti-thrash block;
- compression is enabled and more than one message exists;
- no rescue attempt has happened yet this turn;

the gate invokes the **existing synchronous `run_preflight_compression()` one additional time**, temporarily allowing one extra attempt. It then re-evaluates the full prompt before any provider request (`continue`), or keeps Runway v1's fail-closed block if there was no improvement. It never calls `session.interrupt`, `session.compress` via websocket, `prompt.submit`, or writes to SQLite directly. It does **not** blindly resume tools. It adds a per-turn guard flag reset at the beginning of `run_conversation`.

The rescue is best-effort, not proof of uninterrupted 24/7 operation. If the request cannot be compressed safely, the turn may end with the pre-existing Runway v1 diagnostic. Per-turn recovery counter is not equivalent to an evergreen context watchdog. Do not artificially provoke full-context errors in production just to test this.

## Installation — only when Desktop and backend are fully closed

```
cd ~/Downloads/hermes-preflight-recovery-v2
python3 tests/test_recovery.py
python3 tests/test_install.py
python3 install.py --check
python3 install.py --install
```

Installer refuses unknown file SHA256, symlinked targets, running Desktop/backend, or a second install. It records original copies under `~/.hermes/patch-backups/preflight-recovery-v2/` before atomically replacing the two files. Nothing touches `state.db`, config, Ollama, or Guard v2.2.

On your machine `--check` should say **original** for the two files: 
- `agent/turn_preflight_gate.py`: `50237c4bd84b283a096e7a1e0c681d6b45d5869f8474c151d2137250635fc5cf` (Runway v1)
- `agent/conversation_loop.py`: `926435d563ef1b7de1049f5c36897f1cda7376508b98164a68a0cb5192656270` (unpatched)

Run `python3 -m py_compile ~/.hermes/hermes-agent/agent/{turn_preflight_gate,conversation_loop}.py` and restart Hermes Desktop after installation. You should normally see no change. If a near-full request is blocked after exhausting normal attempts, look for `Pre-API runway recovery: one extra controlled compression` in `~/.hermes/logs/agent.log`.

## Rollback — only when Desktop and backend are fully closed

```
cd ~/Downloads/hermes-preflight-recovery-v2
python3 install.py --rollback
```

Rollback rechecks the modified hashes and backup hashes before restoring, and restores **Runway v1**, not unpatched Hermes. Unknown edits are rejected.

## Second issue — session turn lease

DO NOT disable lease checks, overwrite holders, extend TTL arbitrarily or bypass the lost-lease interrupt. Those can allow two simultaneous transcript writers. We need the user's exact installed lease, scheduler and database source files to audit renewal failure before providing a second patch.

Run read-only collector while the agent is working, if desired:

```
python3 collect_lease_source.py
```

It creates `~/Desktop/hermes-lease-source-YYYYMMDD-HHMMSS.zip`, containing only existing Python source files and a checksum manifest. Inspect before sharing; it does not collect conversation history, SQLite state, Ollama logs, settings, keys or tokens. This package itself does NOT claim to fix lease loss.

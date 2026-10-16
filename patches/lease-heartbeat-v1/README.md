# Lease Heartbeat v1 — bounded wait and 30-second renewal

**Target:** Hermes Agent v0.21.7 (`71d34bad1045`) with Preflight Recovery v2 already installed. On the documented local host this patch has been installed and tested. This patch does **not** fix macOS sleep-related lease loss.

### Files changed

1. `hermes_state.py`: optional bounded wait for `SessionDB._lock` on explicitly selected write paths; normal writes keep the original blocking semantics.
2. `hermes_state_compression.py`: `refresh_session_turn_lease()` bounds per-attempt Python mutex acquisition to ~2 seconds and observes the existing overall renewal patience limit.
3. `agent/turn_facade_lease.py`: default refresh interval drops from 60 to 30 seconds. The lease TTL remains 300 seconds.

**Critical safety property:** owner-fenced SQL is retained. Failed renewal still interrupts the active turn; it never steals a lease or overwrites its owner.

### Tests / installation (in this directory)

```bash
python3 tests/test_lease.py
python3 tests/test_install.py
python3 install.py --check
# After quitting Hermes Desktop and waiting for the backend to exit:
python3 install.py --install
```

### Rollback (with Desktop stopped)

```bash
python3 install.py --rollback
```

The script verifies exact known file hashes and stores backups under `~/.hermes/patch-backups/lease-heartbeat-v1`. A source-level fix cannot refresh a DB lease while macOS has suspended the Python process for longer than its 300-second TTL. See `../../docs/OPERATIONS.md` and `../../docs/UPDATES.md`.

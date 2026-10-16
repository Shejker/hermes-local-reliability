# Hermes Agent — local reliability toolkit (macOS / Ollama)

**Personal recovery archive and source repository, assembled 2026-10-16.**

This repository preserves the **real patch source files**, their hash-checked installers and unit tests, the read-only Desktop monitor, selected helper scripts, and documentation for operating and upgrading a locally patched Hermes Agent installation. It is **not** an upstream Hermes release or a general-purpose one-click installer.

> **Baseline:** NousResearch Hermes Agent **v0.21.7**, Git commit **`71d34bad1045`**, macOS Apple Silicon, Ollama chat completions, 65,536 context tokens. All source replacements are version- and SHA256-pinned. Do **not** install these source snapshots onto a newer Hermes release without reviewing/rebasing them.

## At a glance

| Component | Directory | State on the documented installation | Purpose |
| --- | --- | --- | --- |
| Compaction handoff fix v1 | `patches/compaction-handoff-v1/` | Installed | Correctly handle the last nested summary marker |
| Preflight Runway v1 | `patches/preflight-runway-v1/` | Installed as the v2 prerequisite | Fail closed before dangerously oversized Ollama requests |
| Preflight Recovery v2 | `patches/preflight-recovery-v2/` | Installed | Allow one guarded extra pre-API compression attempt per turn |
| Lease Heartbeat v1 | `patches/lease-heartbeat-v1/` | Installed | Bound local mutex waits and renew session lease more frequently |
| Desktop Guard v2.2 | `monitor/desktop-guard-v2.2/` | Installed | Read-only Desktop session monitoring, no interruptions |
| Morning report | `helpers/hermes-morning-report.py` | Optional | Read-only diagnostics; results may contain private logs |
| Configuration reference | `examples/config.example.yaml` | Reference only | Sanitized illustration, not a deployable secret store |
| Old guards | `archive/legacy/` | **RETIRED — DO NOT INSTALL** | Historical code retained for audit only |

**Installation state above is documented history, not something this repository can independently verify on your computer.** Run `python3 tools/audit_install.py` on that computer to compare available hashes and status.

## First steps

```bash
# Unzip and inspect the repository; no installation is performed.
cd ~/Downloads/hermes-local-reliability
python3 tools/audit_install.py
python3 tools/cleanup_macos.py --plan

# Only if the preview is correct: organize old Downloads artifacts.
python3 tools/cleanup_macos.py --apply
```

The cleanup **never touches** `~/Downloads/BE`, `~/.hermes/hermes-agent`, live databases, or `~/.hermes/patch-backups`. It keeps installer directories accessible under `~/.hermes/maintenance/`, and moves known ZIP packages to `~/.hermes/maintenance/packages/`. Conflicts are preserved, never overwritten. Duplicate copies are moved to the Trash for manual review.

For recovery, installation order, operations, macOS clamshell caveats, and a safe update procedure, see:

- [Installation and rollback](docs/INSTALLATION.md)
- [Monitoring and everyday operations](docs/OPERATIONS.md)
- [Updating Hermes without losing patches](docs/UPDATES.md)
- [Known issues and test evidence](docs/TROUBLESHOOTING.md)
- [Security and private repository hygiene](SECURITY.md)

## Core operational guarantees and limitations

- **Guard v2.2 is observe-only:** it never calls `session.interrupt`, `session.compress`, `prompt.submit`, or modifies `state.db`.
- **Recovery v2 is bounded:** at most one extra native compression attempt in a turn under strict conditions, followed by another safety check. It cannot guarantee an automatic restart after every failure.
- **Lease Heartbeat v1 is fail-closed:** it never disables ownership validation or extends the original 300-second lease TTL. System sleep longer than the TTL can still interrupt a turn.
- **Do not disable sleep indefinitely when carrying the MacBook.** `caffeinate -i` prevents idle sleep but not ordinary lid-close sleep; system sleep-disabling tools may increase heat and battery risk.
- A successful test or a long uninterrupted night does not prove the ability to recover from arbitrary hardware, database, or provider failures.

## Repo publishing

Prefer a **private GitHub repository**. No `state.db`, messages, actual config files, authentication tokens, full personal logs, environment files, or Ollama model weights are included. Source snapshots may still contain upstream source code; attribution is documented in `THIRD_PARTY_NOTICES.md`.

```bash
# From this extracted repo root; requires local Git and your GitHub repository URL.
git init
git add .
git commit -m "Archive Hermes local reliability patches and documentation"
git branch -M main
git remote add origin git@github.com:YOUR_ACCOUNT/YOUR_PRIVATE_REPO.git
git push -u origin main
```

Never paste real credentials into this repo. No GitHub account URL is assumed.

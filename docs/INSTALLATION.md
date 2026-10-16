# Installation and rollback (macOS)

## Compatibility gate

These patches target NousResearch Hermes Agent v0.21.7 at `71d34bad1045`. **All installers inspect known source hashes and refuse unknown versions.** The original installed machine contains successive source modifications, so package order matters. Do not run installers or rollbacks while an active Hermes turn or backend is running.

Before making changes:

```bash
cd ~/.hermes/hermes-agent
git rev-parse HEAD
git status --short
cd /path/to/hermes-local-reliability
python3 tools/audit_install.py
```

The commands below describe the original installation sequence, **not** instructions to reinstall already-installed patches.

## Patch order (historical baseline only)

1. `patches/compaction-handoff-v1/`: `python3 test_patch.py && python3 patch_hermes.py --check && python3 patch_hermes.py --apply`
2. `patches/preflight-runway-v1/`: `python3 tests/test_runway.py && python3 tests/test_installer.py && python3 install.py --check && python3 install.py --install`
3. `patches/preflight-recovery-v2/`: `python3 tests/test_recovery.py && python3 tests/test_install.py && python3 install.py --check && python3 install.py --install`
4. `patches/lease-heartbeat-v1/`: `python3 tests/test_lease.py && python3 tests/test_install.py && python3 install.py --check && python3 install.py --install`

Run each set of commands **from within that package's directory**. Restart Hermes Desktop after source-code installations. Installers save original files under `~/.hermes/patch-backups/`, and all reject changes to files with unknown SHA256.

### Guard v2.2

`monitor/desktop-guard-v2.2/` is an upgrade package for a previously installed Guard v2.1. It is **not a fresh-install script**. The earlier v2.1 could interrupt Hermes during native compression and **must not be used in production**. To migrate an existing v2.1 installation, run `python3 test_guard.py`, `python3 install_guard.py --check`, then `python3 install_guard.py --install` in that directory. For an already installed v2.2, do **not** rerun it. For a fresh installation, design a separate safe observe-only bootstrap rather than running the unsafe legacy guard.

## Rollback order

Quit Hermes Desktop and wait for its backend process to exit. Roll back in reverse order, from each respective package directory:

1. Lease Heartbeat v1: `python3 install.py --rollback`.
2. Preflight Recovery v2: `python3 install.py --rollback` (restores **Runway v1**, not upstream).
3. Preflight Runway v1: `python3 install.py --rollback` (restores the upstream preflight gate).
4. Compaction Handoff v1: `python3 patch_hermes.py --rollback`.

Guard v2.2 has a distinct `python3 install_guard.py --rollback` and **would restore the unsafe v2.1 source**. Do not run that rollback without a specific incident-recovery reason and a plan to stop the Guard LaunchAgent. Guard is outside the upstream Hermes code checkout and normally does not need rollback for Hermes upgrades.

**Do not remove** any `~/.hermes/patch-backups` directories or the installer manifests. A source backup is not a Hermes conversation database backup.

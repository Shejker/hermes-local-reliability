# Updating upstream Hermes while keeping local patches

## Short answer

**The local patches do not categorically block upstream updates.** They do create a dirty source checkout and pin the patch installers to known SHA256 contents. An updater may stash these edits, replace source modules, or fail to reapply a stash cleanly. After an update, the *new* backend may run **without** the reliability improvements until they are ported and retested for that new release.

As documented in the official Hermes [update guide](https://hermes-agent.nousresearch.com/docs/getting-started/updating/), the **Desktop updater** uses `hermes update --keep-stash`: uncommitted source changes are parked in a Git stash and **not automatically reapplied**. A terminal interactive update may offer to reapply changes, and a noninteractive update can behave differently depending on its settings. Consult the updater documentation for your installed version.

## Do this first, without updating

```bash
cd ~/.hermes/hermes-agent
git rev-parse HEAD
git status --short
git diff --name-only
git stash list
hermes update --check
hermes update --plan
python3 /path/to/hermes-local-reliability/tools/audit_install.py
```

`hermes update --check` can fetch upstream metadata; it does **not** install a release. `--plan` inventories potential service impact. Both should be treated as diagnostics rather than an update operation.

## Preferred cautious workflow

1. **Finish and save the current agent task**. Quit the Desktop app and let its backend exit cleanly.
2. Take a **full Hermes state backup** using your installed release's documented backup method. Back up `~/.hermes` outside the Git repository; protect credentials and private session data.
3. Preserve the full local Git patch and original manifests: `python3 /path/to/hermes-local-reliability/tools/snapshot_local_sources.py` (a source-only ZIP is created on the Desktop); in addition, `git diff --binary > ~/.hermes/maintenance/pre-update-hermes.diff`; keep `~/.hermes/patch-backups/` intact. Record the old HEAD and source file hashes using `tools/audit_install.py`.
4. In a **separate test checkout or Mac account** if possible, update upstream, then compare new implementations to local patches. Upstream may already fix a problem; porting that old modification might reintroduce a bug.
5. Adapt patch source and unit tests to the new release. Because patch installers require **exact hashes**, they should normally refuse a new Hermes revision. Never bypass their checks to force an old full-file replacement.
6. Test the patched new release offline, including native compression, prompt boundary conditions, concurrent lease owners, and sleep/wake behavior.
7. Migrate the working Desktop installation during a scheduled downtime. Revalidate backend status, compression, guard and lease. Roll back using matching backups if anything fails; remember database migrations might need full state restoration.

## What **not** to do

- Do not click **Update** inside Desktop and assume all patches are preserved.
- Do not run `git reset --hard`, `git clean -fd`, or unreviewed `git stash apply` on the live checkout.
- Do not copy old patched **whole source files** over updated upstream files: you might erase new fixes, interfaces and security changes.
- Do not interpret a successful Hermes update as confirmation that local patches have been reinstalled.
- Do not delete install manifests or `~/.hermes/patch-backups/` during cleanup.

## Long-term maintainability

For advanced users, an isolated Git branch containing **small, reviewable commits** for the actual changes is more maintainable than replacing whole source files. Current Hermes documentation mentions `updates.parked_branch_strategy: update_in_place` for users deliberately maintaining such a branch; adopting this is a separate migration requiring careful testing. The recovery archive in this repository remains useful for reference and rollback regardless of that future strategy.

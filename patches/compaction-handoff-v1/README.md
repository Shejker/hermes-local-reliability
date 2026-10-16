# Hermes compaction handoff fix v1

**Target:** Hermes Agent v0.21.7, commit `71d34bad1045`. Modifies `agent/context_compressor.py`, exactly one line. This is installed on the documented local host.

The original `_reappend_inflight_user_task` inspected content after the **first** `_SUMMARY_END_MARKER`; nested summaries can make it misinterpret stale content as the current task. The patch uses `rsplit(_SUMMARY_END_MARKER, 1)` to inspect after the **last** marker. It does not guarantee completion of every long task.

```bash
python3 test_patch.py
python3 patch_hermes.py --check
# Quit Hermes Desktop and let its backend exit before applying:
python3 patch_hermes.py --apply
# Rollback, again only with Desktop stopped:
python3 patch_hermes.py --rollback
```

The installer checks the target AST and known baseline and writes backup(s) under `~/.hermes/patch-backups/hermes-compaction-handoff-v1`. Never force it on a newer Hermes release; see `../../docs/UPDATES.md` for the repo-wide policy.

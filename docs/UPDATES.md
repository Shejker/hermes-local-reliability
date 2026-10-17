# Updating Hermes

1. Finish active work and back up Hermes session data separately; the source-file backup is **not** a session backup.
2. Record `python3 tools/reliability.py --check` and your upstream Git commit.
3. Use the official Hermes updater. Source changes may be stashed, but must not be automatically re-applied.
4. Run `python3 tools/reliability.py --check`, `--plan`, and the health check again.
5. If upstream already includes equivalent behavior, do not patch. If the helper remains compatible and the change is still required, quit Desktop before opting in to installation again.

Structural compatibility checks improve portability, but cannot guarantee behavior on every future release. Incompatible code must be reviewed manually. Do not deploy retired whole-file patches from older versions.

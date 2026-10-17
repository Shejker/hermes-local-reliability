# Installation

Requirements: macOS, Python 3.11+, a git-based Hermes Agent checkout, and (for the optional source adjustment) local Ollama configured with `context_length=65536`, `ollama_num_ctx=65536`, `compression.enabled=true`, `compression.threshold_tokens=40960`, `model.base_url=http://127.0.0.1:11434/v1`.

The source adjustment is optional, not needed for basic monitoring. It patches a *recognized* upstream helper, not a whole backend module. Upstream changes that alter the relevant helper cause a safe refusal.

From this repository:

```bash
python3 -m unittest discover -s tests -v
python3 tools/reliability.py --check
python3 tools/reliability.py --plan
```

If the helper is already reported as `installed`, do not install again. Otherwise, **finish any active session and completely quit Hermes Desktop**, then run:

```bash
python3 tools/reliability.py --install
python3 tools/reliability.py --check
python3 tools/reliability.py --self-test
```

Start Hermes Desktop manually and monitor normal operation. The installer records the exact original file and checksums in `~/.hermes/patch-backups/reliability-layer/`. This backup folder must be retained to support rollback.

To revert: completely quit Desktop again, then:

```bash
python3 tools/reliability.py --rollback
```

Rollback refuses to overwrite a changed source file, including after an upstream update. Do not bypass checks, forcibly kill active tasks, or apply a stale Git stash to a new Hermes release.

`python3 tools/healthcheck.py --hours 8` performs read-only local checks. The optional monitor under `monitor/` is provided as a separate reference source; it is not installed by the command above and does not require Hermes Gateway.

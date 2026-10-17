# Desktop session monitor (optional)

`desktop_guard.py` is the v2.2 read-only monitoring runtime. It queries local Hermes Desktop session information and writes local observations under `~/.hermes/desktop-guard/`. It does not edit the Hermes backend, compress conversations, interrupt tasks or invoke Gateway.

The file is included for reproducibility and review of existing deployments. **This public package does not automatically install, migrate or configure the monitor.** Its use depends on the local Desktop session API; `verified=False` or unchanged token readings should not be treated as authoritative context measurements.

Runtime dependency: `websocket-client` (see `requirements.txt`). Run its regression checks using `python3 monitor/test_guard.py` (no running Desktop required). If maintaining an existing deployment, check the locally installed file and LaunchAgent separately; do not replace a running process based solely on this reference source.

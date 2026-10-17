# Hermes Local Reliability

A small, unofficial toolkit for monitoring and maintaining local [Hermes Agent](https://github.com/NousResearch/hermes-agent) installations on macOS.

It includes read-only health checks, an optional compression-budget adjustment, and an optional Desktop session monitor.

## Quick start

```bash
python3 -m unittest discover -s tests -v
python3 tools/healthcheck.py --hours 8
python3 tools/reliability.py --check
python3 tools/reliability.py --plan
```

The optional source change requires a compatible Hermes backend, a supported local Ollama configuration, and a stopped Desktop process. Installation and rollback are explicit; no background source updates are performed.

See [Installation](docs/INSTALL.md) and [Updates](docs/UPDATES.md) for details.

MIT-licensed community tooling. Not affiliated with NousResearch.

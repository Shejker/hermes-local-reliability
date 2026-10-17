# Security and privacy

This repository contains source code and synthetic tests only. Do not commit live Hermes configuration, model credentials, session databases, generated log reports, local source snapshots, patch backups or `.env` files.

The health check reads local configuration and logs and may display machine-specific paths and model names; review its output before sharing. The optional monitor uses an authenticated **localhost** Hermes Desktop session API with a hardcoded read-only RPC allowlist. It never submits prompts, interrupts sessions or requests compression.

The source installer is opt-in, validates a known function structure and local settings, refuses to modify a running Hermes backend, and retains checksummed local backups for rollback. Audit the code before running; local tools execute with your normal user permissions.

Report potential issues through the repository's issue tracker without including private data.

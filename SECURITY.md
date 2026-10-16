# Security and privacy

- Publish **privately** unless you have reviewed every source file and its upstream license.
- This archive intentionally excludes Hermes live `state.db`, chat history, `auth.json`, `.env`, real `config.yaml`, Ollama weights, installed manifests/backups and log exports.
- `examples/config.example.yaml` is a reference based on the user's local configuration; it uses empty auxiliary API key fields. The example intentionally shows `approvals.mode: off` from the local configuration; **do not copy that setting onto an untrusted machine without considering shell/tool execution risk**.
- Never commit a generated diagnostic ZIP or a live `.hermes` directory; the `.gitignore` excludes common sensitive file names but is not a complete data-loss-prevention control.
- The monitor includes a local-session token discovery routine used only for read-only localhost RPC. Do not expose the local backend or token on a public interface.
- Install/rollback scripts write Hermes source files. Run them only against the expected baseline while Hermes Desktop and backend are fully stopped. The monitor v2.2 installer is a separate migration that checks for the previous installed version.
- This repository does not provide persistence, shell privileges or automatic update execution. No `sudo` commands are required by the cleanup/audit helpers.
- Consider keeping a separate offline backup of the *live* Hermes data before any upstream update; source-code patch backups are not a data backup.

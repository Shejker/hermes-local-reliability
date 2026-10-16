# Hermes CLI Guard (macOS, terminal only)

Wrapper around the interactive `hermes` CLI. It does **not** run a gateway, modify config.yaml, or edit `state.db`.

## Installation

From the unzipped directory:

```zsh
chmod +x install.sh
./install.sh
source ~/.zshrc
hermes-safe --check
hermes
```

The installer appends one marked shell function to `~/.zshrc` so `hermes`, `hermes -c`, and `hermes --resume <id>` go through the guard. Other Hermes subcommands remain untouched. It saves a backup of `.zshrc` first. The actual Hermes binary stays installed unchanged.

To bypass the guard at any time: `command hermes`.

## How recovery works

1. Hermes runs inside an interactive PTY; the guard forwards terminal input/output.
2. Guard looks for known fatal context/compaction error messages. It **does not** restart on a high context percentage, an otherwise slow response, or a period of quiet at the prompt.
3. If a fatal message is seen **and** there has been no output or user input for 180 seconds, while Ollama still responds on localhost:11434, it asks the child to terminate (SIGINT, then SIGTERM after 15 s if needed). It never uses SIGKILL.
4. When the process ends, it takes a consistent SQLite backup with SQLite's `backup()` API, writes a bounded recovery note from the previous session, opens a separate new one-shot Hermes chat to import the note, then resumes that new session.
5. At most 2 automatic recoveries per hour. If the one-shot import fails, it opens a blank session and prints the note's location.

Previous session remains untouched and resumable by ID. External side effects of interrupted tools **cannot** be safely replayed, so the import tells the model not to repeat prior commands.

## Limits and operational caveats

- Recovery is based on **observable terminal error text**. If Hermes silently stops with no fatal message, the guard cannot reliably distinguish a stuck model from one waiting for you. It will not force-restart it. Therefore this is a **best-effort** safety net, not a guarantee of 24/7 recovery.
- The guard only runs **while your terminal and the wrapped Hermes CLI remain running**. It does not survive closing the terminal or macOS sleep; no gateway or launchd daemon is installed.
- Currently designed for the default Hermes home or `$HERMES_HOME`; named profiles selected using Hermes `--profile` are not tracked reliably. The shell function only wraps selected interactive invocations.
- Autosnapshots are stored privately in `$HERMES_HOME/cli-guard/backups/`. No data is uploaded to external services by the guard. The recovery one-shot uses the Hermes provider configured on your system (for you, local Ollama).
- Nothing alters the number of Hermes tools, models, approvals or compression thresholds. Your existing compression settings still do the main work of preventing overload.
- Requires Python 3.9+, macOS with zsh, and an existing `hermes` executable in PATH. The guard itself uses only the Python standard library.
- We validated Python syntax and SQLite snapshot/restore behavior in an isolated test; we could not run against your actual macOS/Hermes build from here.

## Settings

Optional environment variables set in `.zshrc` before using Hermes:

```zsh
export HERMES_GUARD_IDLE_SECONDS=180
export HERMES_GUARD_MAX_RECOVERS=2
```

`HERMES_GUARD_HERMES` selects a different underlying binary for advanced setups.

## Files

- Guard log: `$HERMES_HOME/cli-guard/guard.log`
- SQLite backups: `$HERMES_HOME/cli-guard/backups/`
- Recovery handoffs: `$HERMES_HOME/cli-guard/handoffs/`

All guard-created files/directories are restricted to the current user (mode 0600/0700).

## Uninstall

```zsh
./uninstall.sh
source ~/.zshrc
```

The uninstall script removes the shell wrapper but **leaves** recovery snapshots, logs and your Hermes database intact.

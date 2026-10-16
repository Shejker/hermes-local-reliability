#!/bin/zsh
set -euo pipefail
if [[ -f "$HOME/.zshrc" ]]; then
  cp -p "$HOME/.zshrc" "$HOME/.zshrc.hermes-guard-uninstall-backup.$(date +%Y%m%d%H%M%S)"
  /usr/bin/sed -i '' '/^# BEGIN HERMES CLI GUARD$/,/^# END HERMES CLI GUARD$/d' "$HOME/.zshrc"
fi
rm -f "$HOME/.local/bin/hermes-safe"
rm -f "$HOME/.local/share/hermes-cli-guard/hermes_guard.py"
echo "Odinstalowano wrapper. Nie usunięto bazy Hermesa ani kopii i handoffów."

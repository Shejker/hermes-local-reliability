#!/bin/zsh
set -euo pipefail
here="${0:A:h}"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/hermes-cli-guard"
chmod 700 "$HOME/.local/share/hermes-cli-guard"
install -m 700 "$here/hermes_guard.py" "$HOME/.local/share/hermes-cli-guard/hermes_guard.py"
cat > "$HOME/.local/bin/hermes-safe" <<'EOF'
#!/bin/zsh
exec /usr/bin/env python3 "$HOME/.local/share/hermes-cli-guard/hermes_guard.py" "$@"
EOF
chmod 700 "$HOME/.local/bin/hermes-safe"
if [[ -f "$HOME/.zshrc" ]]; then
  cp -p "$HOME/.zshrc" "$HOME/.zshrc.hermes-guard-backup.$(date +%Y%m%d%H%M%S)"
fi
if ! grep -q '^# BEGIN HERMES CLI GUARD$' "$HOME/.zshrc" 2>/dev/null; then
cat >> "$HOME/.zshrc" <<'EOF'

# BEGIN HERMES CLI GUARD
hermes() {
  if (( $# == 0 )); then
    "$HOME/.local/bin/hermes-safe"
  elif [[ "$1" == "-c" || "$1" == "--continue" || "$1" == "-r" || "$1" == "--resume" || "$1" == "--tui" ]]; then
    "$HOME/.local/bin/hermes-safe" "$@"
  else
    command hermes "$@"
  fi
}
# END HERMES CLI GUARD
EOF
fi
print "Zainstalowano Hermes CLI Guard."
print "Nowy terminal / source ~/.zshrc i potem: hermes-safe --check"
print "Interaktywny 'hermes' uruchomi się przez guard. 'hermes config', 'hermes update' itp. działają normalnie."

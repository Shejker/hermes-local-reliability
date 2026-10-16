#!/bin/zsh
set -euo pipefail
HERE="${0:A:h}"
ROOT="$HOME/.hermes/desktop-guard"
mkdir -p "$ROOT"
chmod 700 "$ROOT"
PYTHON=""
for candidate in "$HOME"/.hermes/tools/python-*/bin/python3; do
  if [[ -x "$candidate" ]]; then
    PYTHON="$candidate"
  fi
done
if [[ -z "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
if [[ ! -x "$ROOT/venv/bin/python3" ]]; then
  "$PYTHON" -m venv "$ROOT/venv"
fi
"$ROOT/venv/bin/python3" -m pip install --disable-pip-version-check 'websocket-client>=1.8,<2.0'
cp "$HERE/desktop_guard.py" "$ROOT/desktop_guard.py"
chmod 600 "$ROOT/desktop_guard.py"
cat > "$ROOT/run.sh" <<'EOF'
#!/bin/zsh
exec "$HOME/.hermes/desktop-guard/venv/bin/python3" "$HOME/.hermes/desktop-guard/desktop_guard.py" --once
EOF
chmod 700 "$ROOT/run.sh"
mkdir -p "$HOME/Library/LaunchAgents"
PLIST="$HOME/Library/LaunchAgents/com.local.hermes-desktop-guard.plist"
if [[ -f "$PLIST" ]]; then
  cp "$PLIST" "$PLIST.backup.$(date +%Y%m%d%H%M%S)"
fi
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.local.hermes-desktop-guard</string>
  <key>ProgramArguments</key><array><string>$ROOT/run.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$ROOT/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$ROOT/launchd.err.log</string>
</dict></plist>
EOF
/bin/launchctl bootout "gui/$(id -u)/com.local.hermes-desktop-guard" 2>/dev/null || true
"$ROOT/venv/bin/python3" "$ROOT/desktop_guard.py" --check | tee "$ROOT/install-check.log"
if ! grep -q 'connected, active sessions=' "$ROOT/install-check.log"; then
  echo 'Desktop API not verified: LaunchAgent NOT enabled. Inspect install-check.log.'
  exit 1
fi
/bin/launchctl bootstrap "gui/$(id -u)" "$PLIST"
printf '\nInstalled. Verify without modifying sessions:\n%s\n\n' "$ROOT/venv/bin/python3 $ROOT/desktop_guard.py --check"

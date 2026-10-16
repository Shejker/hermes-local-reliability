#!/bin/zsh
set -euo pipefail
HERE="${0:A:h}"
ROOT="$HOME/.hermes/desktop-guard"
PLIST="$HOME/Library/LaunchAgents/com.local.hermes-desktop-guard.plist"
AUTO=false
if [[ "${1:-}" == '--auto-continue' ]]; then
  AUTO=true
elif [[ $# -ne 0 ]]; then
  echo 'Usage: ./install.sh [--auto-continue]'
  exit 2
fi
mkdir -p "$ROOT" "$HOME/Library/LaunchAgents"
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
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ROOT/install-backup/$STAMP"
for name in desktop_guard.py run.sh policy.json observations-v2.json; do
  if [[ -f "$ROOT/$name" ]]; then
    cp "$ROOT/$name" "$ROOT/install-backup/$STAMP/$name"
  fi
done
if [[ -f "$PLIST" ]]; then
  cp "$PLIST" "$ROOT/install-backup/$STAMP/$(basename "$PLIST")"
fi
# Do not leave a previous scheduled run using partially replaced files.
/bin/launchctl bootout "gui/$(id -u)/com.local.hermes-desktop-guard" 2>/dev/null || true
cp "$HERE/desktop_guard.py" "$ROOT/desktop_guard.py"
chmod 600 "$ROOT/desktop_guard.py"
"$ROOT/venv/bin/python3" - "$ROOT/policy.json" "$AUTO" <<'PY'
import json, pathlib, sys
path=pathlib.Path(sys.argv[1])
base={"warn_percent":75,"critical_percent":85,"emergency_percent":90,"verify_seconds":25,"min_idle_seconds":90,"cooldown_seconds":600,"max_recoveries_per_hour":2,"auto_interrupt":True,"auto_continue":sys.argv[2].lower()=="true"}
# Explicit installer flag determines continuation mode; preserve advanced customizations.
if path.exists():
    old=json.loads(path.read_text())
    for name in base:
        if name not in ('auto_continue',) and name in old:
            base[name]=old[name]
path.write_text(json.dumps(base,indent=2)+"\n")
path.chmod(0o600)
PY
cat > "$ROOT/run.sh" <<'EOF'
#!/bin/zsh
exec "$HOME/.hermes/desktop-guard/venv/bin/python3" "$HOME/.hermes/desktop-guard/desktop_guard.py" --once
EOF
chmod 700 "$ROOT/run.sh"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.local.hermes-desktop-guard</string>
  <key>ProgramArguments</key><array><string>$ROOT/run.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$ROOT/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$ROOT/launchd.err.log</string>
</dict></plist>
EOF
"$ROOT/venv/bin/python3" "$ROOT/desktop_guard.py" --check | tee "$ROOT/install-check-v2.log"
if ! grep -q 'connected; sessions=' "$ROOT/install-check-v2.log"; then
  echo 'API verification failed. LaunchAgent was not enabled. Previous version is saved under install-backup.'
  exit 1
fi
/bin/launchctl bootstrap "gui/$(id -u)" "$PLIST"
/bin/launchctl enable "gui/$(id -u)/com.local.hermes-desktop-guard"
echo "Hermes Desktop Guard v2 installed. auto_continue=$AUTO, auto_interrupt=true, interval=30s"
echo "Configuration: $ROOT/policy.json"
echo "Logs: $ROOT/guard.log"

#!/bin/zsh
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.local.hermes-desktop-guard.plist"
/bin/launchctl bootout "gui/$(id -u)/com.local.hermes-desktop-guard" 2>/dev/null || true
rm -f "$PLIST"
echo 'Watchdog disabled. Session backups, log and policy preserved in ~/.hermes/desktop-guard/'

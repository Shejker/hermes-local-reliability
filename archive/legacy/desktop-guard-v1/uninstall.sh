#!/bin/zsh
set -euo pipefail
LABEL='com.local.hermes-desktop-guard'
/bin/launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo 'Stopped. Backups/logs kept at ~/.hermes/desktop-guard (delete manually if desired).'

#!/bin/bash
# =============================================================================
# Install TASI systemd services
# =============================================================================
# PURPOSE: Copy service/timer files from repo to systemd user directory
# 
# USAGE:
#   cd /home/mino/tasi-exec
#   bash systemd/install.sh
#
# WHAT IT DOES:
#   1. Copies *.service and *.timer files to ~/.config/systemd/user/
#   2. Runs daemon-reload
#   3. Shows status of installed services
#
# WHAT IT DOES NOT DO:
#   - Does NOT enable services (use systemctl --user enable <service>)
#   - Does NOT start services (use systemctl --user start <service>)
#   - Does NOT modify existing active services
#
# SAFETY:
#   - Backs up existing files before overwriting (timestamped .bak)
#   - Only copies files, doesn't delete anything
# =============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$HOME/.config/systemd/user"

echo "=== TASI Systemd Install ==="
echo "Source: $SCRIPT_DIR"
echo "Target: $TARGET_DIR"
echo ""

# Create target if missing
mkdir -p "$TARGET_DIR"

# Copy service and timer files
for file in "$SCRIPT_DIR"/*.service "$SCRIPT_DIR"/*.timer; do
    [[ -f "$file" ]] || continue
    
    basename_file=$(basename "$file")
    target_file="$TARGET_DIR/$basename_file"
    
    # Backup existing if different
    if [[ -f "$target_file" ]]; then
        if ! diff -q "$file" "$target_file" >/dev/null 2>&1; then
            backup_name="${target_file}.bak.$(date +%Y%m%d-%H%M%S)"
            cp "$target_file" "$backup_name"
            echo "📦 Backed up: $basename_file → ${backup_name##*/}"
        fi
    fi
    
    cp "$file" "$target_file"
    echo "✅ Installed: $basename_file"
done

echo ""
echo "=== Reloading systemd ==="
systemctl --user daemon-reload
echo "✅ daemon-reload complete"

echo ""
echo "=== Installed Services ==="
systemctl --user list-unit-files | grep -E "tasi|chrome-health" | awk '{printf "  %-40s %s\n", $1, $2}'

echo ""
echo "=== Next Steps ==="
echo "Enable services:  systemctl --user enable <service>"
echo "Start services:   systemctl --user start <service>"
echo "Check status:     systemctl --user status <service>"
echo ""
echo "Current enabled services:"
systemctl --user list-unit-files --state=enabled | grep -E "tasi|chrome-health" | awk '{printf "  ✅ %s\n", $1}' || echo "  (none enabled yet)"

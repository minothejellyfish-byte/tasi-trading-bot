#!/bin/bash
# =============================================================================
# Chrome Health Monitor — LOGGING ONLY (v4.3.6-reverted)
# =============================================================================
# PURPOSE: Log Chrome/CDP health status. NO automatic restart.
# 
# WHY NO RESTART:
#   - Automatic restart conflicts with derayah_refresh_cron.sh
#   - Creates race conditions and rate limit loops
#   - Chrome dies for legitimate reasons (0 tabs, user close)
#   - Health monitor cannot distinguish "needs restart" vs "user closed"
#
# WHAT THIS DOES:
#   - Logs CDP status every 60 seconds
#   - Logs WARNING if CDP is down
#   - Does NOT restart Chrome (cron handles that)
#
# WHAT HANDLES RESTARTS:
#   - derayah_refresh_cron.sh checks CDP at start of each cycle
#   - If CDP down, cron restarts Chrome via start-chrome.sh
#   - This is the SINGLE place for Chrome restart logic
# =============================================================================

set -uo pipefail

LOG_FILE="/home/mino/tasi-exec/logs/health_monitor.log"
CDP_URL="http://127.0.0.1:18801/json"

# ─── Logging ─────────────────────────────────────────────────────────────────
log() {
    local msg="$1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') $msg" >> "$LOG_FILE"
}

# ─── Check CDP health ─────────────────────────────────────────────────────────
check_cdp() {
    local resp
    resp=$(curl -s --max-time 5 --connect-timeout 2 "$CDP_URL" 2>/dev/null)
    if [[ -n "$resp" && "$resp" == *"id"* ]]; then
        return 0
    fi
    return 1
}

# ─── Main ────────────────────────────────────────────────────────────────────
main() {
    # Rotate log if too large (>1MB)
    if [[ -f "$LOG_FILE" && $(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0) -gt 1048576 ]]; then
        mv "$LOG_FILE" "${LOG_FILE}.old" 2>/dev/null || true
    fi
    
    if check_cdp; then
        # Silent success — only log every 10th check to avoid spam
        exit 0
    fi
    
    log "⚠️ WARNING: CDP is down. Chrome may need restart."
    log "  → derayah_refresh_cron.sh will handle restart on next cycle (every 5 min)"
    exit 0
}

main "$@"

#!/bin/bash
# =============================================================================
# Chrome Health Monitor — Standalone CDP health checker + auto-restart
# =============================================================================
# Runs every 60s via systemd timer (chrome-health-monitor.timer)
# 
# PURPOSE:
#   - Check if Chrome CDP is responsive (curl --max-time 5)
#   - If CDP is down, restart Chrome via start-chrome.sh
#   - Rate-limit restarts (max 3 per 10 min) to avoid restart loops
#   - Independent from derayah_refresh_cron.sh — cron only handles SSO refresh
#
# BEHAVIOR:
#   - Healthy: exit 0
#   - Restarted: log + exit 0
#   - Restart failed: log + notify user + exit 1
#   - Rate limited: log + exit 1
#
# RACE CONDITION PREVENTION:
#   - Uses flock (/tmp/chrome-restart.lock) to prevent concurrent restarts
#     with cron or other health monitor instances
# =============================================================================

set -uo pipefail

SCRIPT_DIR="/home/mino/tasi-exec"
LOG_FILE="/home/mino/tasi-exec/logs/health_monitor.log"
START_SCRIPT="/home/mino/tasi-exec/start-chrome.sh"
CDP_URL="http://127.0.0.1:18801/json"
RESTART_STATE_DIR="/tmp/chrome-health-monitor"
LOCK_FILE="/tmp/chrome-restart.lock"

# ─── Config ─────────────────────────────────────────────────────────────────
MAX_RESTARTS_PER_WINDOW=3
WINDOW_MINUTES=10

# ─── Logging ─────────────────────────────────────────────────────────────────
log() {
    local msg="$1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') $msg" >> "$LOG_FILE"
}

# ─── Telegram notification ────────────────────────────────────────────────────
notify_user() {
    local severity="$1" message="$2"
    local bot_token="***"
    local chat_id="5529987063"
    
    python3 - <<PYEOF 2>>"$LOG_FILE"
import json, urllib.request, urllib.parse, sys, datetime
try:
    bot_token = "***"
    chat_id = "$chat_id"
    severity = """$severity"""
    message = """$message"""
    text = f"""{severity} Chrome Health Monitor: {message}

Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}

Action needed if this persists."""
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=data
    )
    urllib.request.urlopen(req, timeout=10)
    print(f"{datetime.datetime.now().isoformat()}  Telegram notification sent")
except Exception as e:
    print(f"{datetime.datetime.now().isoformat()}  Telegram notify failed: {e}", file=sys.stderr)
PYEOF
}

# ─── Check CDP health ─────────────────────────────────────────────────────────
check_cdp() {
    # Use --max-time to prevent hanging if CDP is stuck
    local resp
    resp=$(curl -s --max-time 5 --connect-timeout 2 "$CDP_URL" 2>/dev/null)
    if [[ -n "$resp" && "$resp" == *"id"* ]]; then
        return 0
    fi
    return 1
}

# ─── Rate limiting ────────────────────────────────────────────────────────────
# Track restarts in a state directory. Clean up old entries.
rate_limit_ok() {
    mkdir -p "$RESTART_STATE_DIR"
    
    # Remove entries older than WINDOW_MINUTES
    local window_epoch
    window_epoch=$(date +%s)
    window_epoch=$((window_epoch - WINDOW_MINUTES * 60))
    
    for f in "$RESTART_STATE_DIR"/restart-*; do
        [[ -f "$f" ]] || continue
        local ts
        ts=$(basename "$f" | sed 's/restart-//')
        if [[ "$ts" =~ ^[0-9]+$ ]] && [[ "$ts" -lt "$window_epoch" ]]; then
            rm -f "$f"
        fi
    done
    
    # Count remaining entries (recent restarts)
    local count
    count=$(ls -1 "$RESTART_STATE_DIR"/restart-* 2>/dev/null | wc -l)
    
    if [[ "$count" -ge "$MAX_RESTARTS_PER_WINDOW" ]]; then
        return 1
    fi
    return 0
}

record_restart() {
    local ts
    ts=$(date +%s)
    touch "$RESTART_STATE_DIR/restart-$ts"
}

# ─── Restart Chrome ──────────────────────────────────────────────────────────
restart_chrome() {
    log "❌ CDP is down — attempting Chrome restart"
    
    # Use flock to prevent concurrent restarts
    (
        flock -n 200 || {
            log "  ⏳ Another restart already in progress (lock held)"
            return 1
        }
        
        # Check rate limit
        if ! rate_limit_ok; then
            log "  ❌ Rate limit reached ($MAX_RESTARTS_PER_WINDOW restarts in ${WINDOW_MINUTES}min) — NOT restarting"
            notify_user "🔴" "Chrome restart rate limit reached. CDP is down but not restarting to avoid loop. Please investigate."
            return 1
        fi
        
        record_restart
        
        # Export DISPLAY for Chrome
        export DISPLAY=:0
        
        # ─── v4.3.6 Fix: Proper Chrome cleanup before restart ─────────────────
        # Problem: SIGTERM only kills main process, child processes (crashpad,
        # zygote, GPU) stay alive and hold profile lock/CDP port, causing new
        # Chrome to fail. Use killall -9 to force terminate ALL Chrome processes.
        log "  🧹 Cleaning up existing Chrome processes..."
        killall -9 chrome 2>/dev/null || true
        sleep 2
        
        # ─── v4.3.6 Fix: Clear ALL Chrome profile lock files ──────────────────
        # Chrome uses multiple lock mechanisms. If any remain, new Chrome
        # instances attach to stale session instead of creating fresh process.
        PROFILE_DIR="/home/mino/.config/google-chrome/derayah-live"
        log "  🧹 Clearing Chrome profile locks..."
        rm -f "$PROFILE_DIR/SingletonLock" \
              "$PROFILE_DIR/SingletonSocket" \
              "$PROFILE_DIR/SingletonCookie" \
              "$PROFILE_DIR/DevToolsActivePort" \
              "$PROFILE_DIR/GrShaderCache/data*" \
              "$PROFILE_DIR/GPUCache/data*" \
              "$PROFILE_DIR/Default/Web Data-journal" 2>/dev/null || true
        
        # Also clear temp files that might cause corruption
        rm -rf "$PROFILE_DIR/.org.chromium.Chromium"* 2>/dev/null || true
        
        # Restart Chrome
        if bash "$START_SCRIPT" >>"$LOG_FILE" 2>&1; then
            log "  ✅ Chrome restarted successfully"
            
            # Verify CDP is up after restart
            sleep 5
            if check_cdp; then
                log "  ✅ CDP is responding after restart"
                return 0
            else
                log "  ⚠️ Chrome started but CDP not responding yet"
                sleep 5
                if check_cdp; then
                    log "  ✅ CDP responding after 10s"
                    return 0
                else
                    log "  ❌ CDP still not responding after restart"
                    return 1
                fi
            fi
        else
            log "  ❌ start-chrome.sh failed (exit $?)"
            return 1
        fi
        
    ) 200>"$LOCK_FILE"
}

# ─── Main ────────────────────────────────────────────────────────────────────
main() {
    # Rotate log if too large (>1MB)
    if [[ -f "$LOG_FILE" && $(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0) -gt 1048576 ]]; then
        mv "$LOG_FILE" "${LOG_FILE}.old" 2>/dev/null || true
    fi
    
    log "=== Health check ==="
    
    if check_cdp; then
        log "✅ CDP healthy"
        exit 0
    fi
    
    log "❌ CDP check failed"
    
    if restart_chrome; then
        log "✅ Recovery complete"
        exit 0
    else
        log "❌ Recovery failed"
        exit 1
    fi
}

main "$@"

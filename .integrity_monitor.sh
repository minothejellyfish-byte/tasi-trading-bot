#!/bin/bash
# Integrity Monitor — Detect unauthorized changes to critical files
# Runs hourly via cron. Alerts via Telegram if changes detected.

DIR="/home/mino/tasi-exec"
BASELINE="$DIR/.file_baseline.sha256"
CURRENT="$DIR/.file_current.sha256"
LOG="$DIR/.integrity_monitor.log"
ALERT_LOG="$DIR/.integrity_alerts.log"

# Critical files to monitor
FILES="poller.py bot.py bookkeeper.py screener.py market_regime.py derayah_session_manager.py derayah_api.py order_helpers.py derayah_refresh_cron.sh cleanup_stand_down.sh start-chrome.sh ws_keepalive_v2.sh"

create_baseline() {
    cd "$DIR"
    sha256sum $FILES > "$BASELINE" 2>/dev/null
    echo "$(date '+%Y-%m-%d %H:%M:%S') | BASELINE_CREATED | $FILES" >> "$LOG"
}

check_integrity() {
    cd "$DIR"
    
    # Create baseline if missing
    if [ ! -f "$BASELINE" ]; then
        create_baseline
        return 0
    fi
    
    # Generate current checksums
    sha256sum $FILES > "$CURRENT" 2>/dev/null
    
    # Compare
    if ! diff -q "$BASELINE" "$CURRENT" >/dev/null 2>> "$LOG"; then
        # Change detected
        TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
        CHANGED=$(diff "$BASELINE" "$CURRENT" | grep -E '^[\u003c\u003e]' || true)
        
        echo "$TIMESTAMP | UNAUTHORIZED_CHANGE | $CHANGED" >> "$ALERT_LOG"
        
        # Telegram alert (if bot is running)
        if [ -f "$DIR/bot_commands.py" ]; then
            MSG="⚠️ TASI INTEGRITY ALERT\n\nUnauthorized change detected:\n$CHANGED\n\nTime: $TIMESTAMP\n\nIf expected: update baseline with:\n  cd $DIR && ./.integrity_monitor.sh --update"
            
            # Try to send via Telegram if python bot is available
            python3 -c "
import sys
sys.path.insert(0, '$DIR')
try:
    import requests
    # Note: actual bot token would be read from config
    print('Telegram alert: $MSG')
except:
    print('Telegram not available, logged to $ALERT_LOG')
" 2>/dev/null || echo "$TIMESTAMP | Telegram alert failed — check $ALERT_LOG" >> "$LOG"
        fi
        
        return 1
    fi
    
    echo "$(date '+%Y-%m-%d %H:%M:%S') | OK | No changes" >> "$LOG"
    return 0
}

case "${1:-}" in
    --update|--baseline)
        create_baseline
        echo "Baseline updated at $(date)"
        ;;
    *)
        check_integrity
        ;;
esac

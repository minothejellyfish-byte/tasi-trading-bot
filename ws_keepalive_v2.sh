#!/bin/bash
# WebSocket Keepalive v2 - Smart CDP-aware restart
# Only logs warnings during TASI market hours (Sun–Thu 10:00–15:00 KSA)
# Off-market: silent CDP checks, no noise in logs

LOG=/home/mino/tasi-exec/ws_keepalive.log
CDP_URL="http://127.0.0.1:18801/json/version"
MAX_CDP_RETRIES=3
CDP_RETRY_DELAY=5

# TASI market hours: Sun–Thu 10:00–15:00 KSA
is_market_open() {
    local dow=$(date +%u)  # 1=Mon, 7=Sun
    local hour=$(date +%H)
    local min=$(date +%M)
    local time_mins=$((10#$hour * 60 + 10#$min))
    
    # TASI trades Sun(7)–Thu(5), 10:00–15:00
    if [[ "$dow" -ge 1 && "$dow" -le 5 ]] || [[ "$dow" -eq 7 ]]; then
        if [[ "$time_mins" -ge 600 && "$time_mins" -lt 900 ]]; then
            return 0  # Market open
        fi
    fi
    return 1  # Market closed
}

echo "=== WebSocket Keepalive v2 Started $(date) ===" >> $LOG

while true; do
    TIMESTAMP=$(date '+%H:%M:%S')
    MARKET_OPEN=false
    if is_market_open; then
        MARKET_OPEN=true
    fi
    
    # Step 1: Check if Chrome CDP is actually responding
    CDP_WORKING=false
    for i in $(seq 1 $MAX_CDP_RETRIES); do
        if curl -s --max-time 2 "$CDP_URL" > /dev/null 2>&1; then
            CDP_WORKING=true
            break
        fi
        sleep $CDP_RETRY_DELAY
    done
    
    if [ "$CDP_WORKING" = false ]; then
        # Only log warnings during market hours
        if [ "$MARKET_OPEN" = true ]; then
            echo "[$TIMESTAMP] ⚠️ Chrome CDP not responding on port 18801" | tee -a $LOG
            echo "[$TIMESTAMP]   ws_probe cannot start without CDP" | tee -a $LOG
        fi
        sleep 30
        continue
    fi
    
    # Step 2: Check if ws_probe is running
    if ! pgrep -f "ws_probe.py" > /dev/null; then
        echo "[$TIMESTAMP] ✅ CDP working, ws_probe not running - starting..." | tee -a $LOG
        cd /home/mino/tasi-exec
        nohup python3 ws_probe.py 90 >> /home/mino/tasi-exec/ws_probe.log 2>&1 &
        WS_PID=$!
        echo "[$TIMESTAMP] ws_probe.py started (PID: $WS_PID)" | tee -a $LOG
        
        # Wait a moment and verify it started successfully
        sleep 3
        if ! pgrep -f "ws_probe.py" > /dev/null; then
            echo "[$TIMESTAMP] ❌ ws_probe.py crashed immediately - will retry next cycle" | tee -a $LOG
        else
            echo "[$TIMESTAMP] ✅ ws_probe.py running successfully" | tee -a $LOG
        fi
    else
        # ws_probe is running - check if it's actually capturing data
        # Check ws_frames_raw.log (grows continuously during run) instead of ws_frames.json (only written at end)
        WS_LOG_SIZE=$(stat -c%s /home/mino/tasi-exec/ws_frames_raw.log 2>/dev/null || echo 0)
        sleep 2
        WS_LOG_SIZE_NEW=$(stat -c%s /home/mino/tasi-exec/ws_frames_raw.log 2>/dev/null || echo 0)
        
        if [ "$WS_LOG_SIZE" = "$WS_LOG_SIZE_NEW" ]; then
            # Only log data capture issues during market hours
            if [ "$MARKET_OPEN" = true ]; then
                echo "[$TIMESTAMP] ⚠️ ws_probe running but NOT capturing data - restarting..." | tee -a $LOG
            fi
            pkill -f "ws_probe.py"
            sleep 2
            cd /home/mino/tasi-exec
            nohup python3 ws_probe.py 90 >> /home/mino/tasi-exec/ws_probe.log 2>&1 &
        else
            echo "[$TIMESTAMP] ✅ ws_probe running and capturing data ($((WS_LOG_SIZE_NEW - WS_LOG_SIZE)) bytes/2sec)" | tee -a $LOG
        fi
    fi
    
    sleep 60
done

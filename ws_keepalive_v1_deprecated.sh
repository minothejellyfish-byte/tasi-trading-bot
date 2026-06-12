#!/bin/bash
# WebSocket Keepalive - Restarts ws_probe.py every 60 seconds
# This ensures continuous WebSocket capture

LOG=/home/mino/tasi-exec/ws_keepalive.log
echo "=== WebSocket Keepalive Started $(date) ===" > $LOG

while true; do
    TIMESTAMP=$(date '+%H:%M:%S')
    
    # Check if ws_probe is running
    if ! pgrep -f "ws_probe.py" > /dev/null; then
        echo "[$TIMESTAMP] ws_probe.py not running - starting..." | tee -a $LOG
        cd /home/mino/tasi-exec
        nohup python3 ws_probe.py 90 >> /home/mino/tasi-exec/ws_probe.log 2>&1 &
        echo "[$TIMESTAMP] ws_probe.py started (PID: $!)" | tee -a $LOG
    else
        echo "[$TIMESTAMP] ws_probe.py already running - OK" | tee -a $LOG
    fi
    
    sleep 60
done

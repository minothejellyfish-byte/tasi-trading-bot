#!/bin/bash
# Auto-start TASI poller before market open

LOG="/home/mino/tasi-exec/poller.log"
PIDFILE="/home/mino/tasi-exec/poller.pid"

echo "[$(date)] Starting poller..." >> "$LOG"

# Check if already running
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE" 2>/dev/null)
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "[$(date)] Poller already running (PID $OLD_PID)" >> "$LOG"
        exit 0
    fi
fi

cd /home/mino/tasi-exec
nohup /usr/bin/python3 poller.py >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "[$(date)] Poller started (PID $!)" >> "$LOG"

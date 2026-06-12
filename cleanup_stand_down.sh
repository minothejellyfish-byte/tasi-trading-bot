#!/bin/bash
# Automated cleanup of stand_down and blocked_symbols before trading
# Runs at 09:55 AM Sunday-Thursday

STAND_DOWN="/home/mino/tasi-exec/stand_down"
BLOCKED="/home/mino/tasi-exec/blocked_symbols.txt"
LOG_FILE="/home/mino/tasi-exec/cleanup.log"

echo "$(date '+%Y-%m-%d %H:%M:%S') - Cleaning up trading blocks..." >> "$LOG_FILE"

# Remove stand_down if exists
if [ -f "$STAND_DOWN" ]; then
    rm -f "$STAND_DOWN"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Removed stand_down" >> "$LOG_FILE"
fi

# Remove blocked_symbols if exists
if [ -f "$BLOCKED" ]; then
    rm -f "$BLOCKED"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Removed blocked_symbols.txt" >> "$LOG_FILE"
fi

# Verify cleanup
if [ ! -f "$STAND_DOWN" ] && [ ! -f "$BLOCKED" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Ready for trading" >> "$LOG_FILE"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ⚠️ Cleanup incomplete" >> "$LOG_FILE"
fi

#!/bin/bash
# run_post_market.sh - Wrapper script for post-market analysis with Telegram notification

set -e

LOG_FILE="/home/mino/tasi-exec/logs/post_market_cron.log"
TOKEN="898953…VYQU"
CHAT_ID="5529987063"

echo "$(date): Starting post-market analysis..." >> "$LOG_FILE"

# Check if trading day
cd /home/mino/tasi-exec
if ! python3 -c "
import sys
sys.path.insert(0, '.')
from market_calendar import is_tasi_trading_day
from datetime import datetime
import pytz
if not is_tasi_trading_day(datetime.now(pytz.timezone('Asia/Riyadh'))):
    print('NOT_TRADING_DAY')
    sys.exit(1)
"; then
    echo "$(date): Not a trading day, skipping" >> "$LOG_FILE"
    curl -s -X POST \
        -H "Content-Type: application/json" \
        -d "{\"chat_id\":\"$CHAT_ID\",\"text\":\"⏭️ TASI holiday/weekend — post-market analysis skipped\"}" \
        "https://api.telegram.org/bot$TOKEN/sendMessage" > /dev/null
    exit 0
fi

# Run post-market analysis
echo "$(date): Running post_market.py..." >> "$LOG_FILE"
cd /home/mino/tasi-exec
python3 post_market.py >> "$LOG_FILE" 2>&1

# Generate PDF
DATE_STR=$(date +%Y-%m-%d)
REPORT_DIR="/home/mino/tasi-exec/relearning/daily/$DATE_STR"

if [ -f "$REPORT_DIR/report.md" ]; then
    echo "$(date): Generating PDF..." >> "$LOG_FILE"
    cd "$REPORT_DIR"
    pandoc report.md -o report.pdf --pdf-engine=xelatex 2>> "$LOG_FILE" || echo "PDF generation failed" >> "$LOG_FILE"
    
    # Extract summary for Telegram
    TRADES=$(grep -c "Entry:" "$REPORT_DIR/report.md" 2>/dev/null || echo "0")
    PNL=$(grep "Total actual P&L" "$REPORT_DIR/report.md" | sed 's/.*Total actual P&L: //' | head -1)
    MISSED=$(grep "MISSED PROFIT" "$REPORT_DIR/report.md" | sed 's/.*avg missed //' | head -1)
    
    # Send summary to Telegram
    MESSAGE="📊 TASI Post-Market Report: $DATE_STR

✅ Trades: $TRADES executed
💰 P&L: ${PNL:-N/A}
💸 Missed profit: ${MISSED:-N/A}

Full report: $REPORT_DIR/report.pdf"
    
    curl -s -X POST \
        -H "Content-Type: application/json" \
        -d "{\"chat_id\":\"$CHAT_ID\",\"text\":\"$MESSAGE\"}" \
        "https://api.telegram.org/bot$TOKEN/sendMessage" > /dev/null
    
    echo "$(date): Report sent to Telegram" >> "$LOG_FILE"
else
    echo "$(date): Report not found!" >> "$LOG_FILE"
    curl -s -X POST \
        -H "Content-Type: application/json" \
        -d "{\"chat_id\":\"$CHAT_ID\",\"text\":\"⚠️ Post-market analysis failed — report not generated\"}" \
        "https://api.telegram.org/bot$TOKEN/sendMessage" > /dev/null
fi

echo "$(date): Done" >> "$LOG_FILE"
#!/bin/bash
# TASI Chrome CDP Startup - Working Version
# Uses derayah-live profile (fixes Chrome 148 freeze bug)
# Created: 2026-06-07

CHROME="/usr/bin/google-chrome-stable"
PROFILE_DIR="/home/mino/.config/google-chrome/derayah-live"
CDP_PORT="18801"
LOG="/home/mino/tasi-exec/chrome_startup.log"

# System display is :0 (lightdm + CRD uses existing display)
export DISPLAY=:0

echo "$(date): Starting Chrome with CDP on port $CDP_PORT..." >> $LOG

$CHROME \
  --no-sandbox \
  --disable-gpu \
  --disable-software-rasterizer \
  --remote-debugging-port=$CDP_PORT \
  --remote-allow-origins="*" \
  --user-data-dir=$PROFILE_DIR \
  --no-first-run \
  --disable-sync \
  --no-default-browser-check \
  --proxy-server=socks5://localhost:1080 \
  --password-store=basic \
  https://derayah.tickerchart.net/app/en \
  https://newonline.derayah.com/ \
  >> $LOG 2>&1 &

CHROME_PID=$!
echo "$(date): Chrome started with PID $CHROME_PID" >> $LOG
sleep 5

# Verify CDP is working
for i in {1..10}; do
    if curl -s --max-time 2 http://127.0.0.1:$CDP_PORT/json/version > /dev/null; then
        echo "$(date): ✅ CDP port $CDP_PORT is responding" >> $LOG
        exit 0
    fi
    echo "$(date): Waiting for CDP... ($i/10)" >> $LOG
    sleep 2
done

echo "$(date): ❌ CDP failed to start" >> $LOG
exit 1

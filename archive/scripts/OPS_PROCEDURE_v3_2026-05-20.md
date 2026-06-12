# TASI Trading System — Operations Procedure v3.0
**Date:** 2026-05-20
**Previous:** v2.0 (2026-05-18)

---

## What's New in v3.0 (2026-05-20)

### 1. Browser: Chromium → Google Chrome
| | Before | After |
|---|---|---|
| Binary | `/snap/bin/chromium` | `/usr/bin/google-chrome-stable` |
| Profile | `~/snap/chromium/common/derayah-profile` | `~/.config/google-chrome/derayah-profile` |
| CDP Port | 18801 | 18801 |
| Flags | `--no-sandbox` | `--single-process --no-sandbox` |

**Why:** Snap Chromium renderer processes were crashing when loading Derayah reCAPTCHA iframes, causing CDP to hang and keepalive loops to fail.

**Fix:** Removed Snap Chromium entirely. Google Chrome with `--single-process` works reliably (multi-process mode fails to bind CDP port 18801 on this system).

### 2. WS Price Feed Fix
**Problem:** Poller was using yfinance fallback instead of real-time WS prices → no entry signals.

**Root cause:** Multiple poller instances running simultaneously:
- systemd `tasi-poller.service` (auto-started on boot)
- OpenClaw cron `tasi-price-poller` (started at 10:00)
- Manual restarts during debugging

Each instance had its own `_ws_price_cache`. WS listener in one instance wrote to cache A, but `fetch_data()` in another instance read from empty cache B.

**Fix:**
```bash
systemctl --user stop tasi-poller.service
systemctl --user disable tasi-poller.service
pkill -9 -f "python3.*poller\.py"
```

Now only OpenClaw cron starts poller at 10:00. Single instance = single cache.

### 3. Post-Market Analysis v2 (Comprehensive)
**Before:** Basic DM message with pick performance only.

**After:** Full analysis posted to TASI Execution group:
- Today's picks performance (open/high/low/close)
- 3-5 missed opportunities with gap analysis
- Back-run module (simulate what would have happened)
- Strategy recommendations with fine-tuning suggestions
- Continuous learning loop (`learning.json`)

**Missed opportunity criteria:**
- High-low range ≥ 2.0% of open (captures volatility)
- Open-to-high ≥ 1.0% (confirms upside)
- Both required

**Files:**
- `post_market_v2.py` — main script
- `ws_logger.py` — price logger
- `reports/` — daily HTML reports
- `learning.json` — strategy tracking

### 4. WS Price Logger (NEW)
- Logs every WS price update to `ws_prices_YYYY-MM-DD.jsonl`
- Format: `{ts, time, symbol, price, change, pchange, real}`
- Post-market analysis uses WS data first, yfinance as fallback
- Modified `poller.py` to call `ws_logger.log_price()` on every WS frame

### 5. Continuous Learning Loop
1. Post-market analysis runs automatically at 15:35
2. Identifies missed opportunities (3-5 stocks)
3. Analyzes why each was missed (gap analysis)
4. Generates strategy recommendations
5. Amin reviews and approves/rejects changes
6. Approved changes implemented in screener/poller
7. Next session uses updated strategy
8. Cycle repeats — strategy evolves over time

---

## Trading Schedule (Thursday 2026-05-21)

| Time | Action | Actor |
|------|--------|-------|
| 09:50 | Screener runs | OpenClaw cron |
| 10:00 | Market opens | TASI |
| 10:00 | Poller starts | OpenClaw cron |
| 10:00 | WS listener starts | Poller auto |
| 10:00–14:45 | Active trading | Poller + Bot |
| 14:45 | Hard close | Poller auto |
| 15:30 | Market close | TASI |
| 15:35 | Post-market analysis | OpenClaw cron |

---

## Verification Checklist (Thursday)

- [ ] Chrome running with CDP on port 18801
- [ ] Derayah dashboard tab loaded and logged in
- [ ] TickerChart tab loaded with JWT
- [ ] Bot running (PID visible)
- [ ] Poller running (exactly ONE PID)
- [ ] WS cache populated within 10s of poller start
- [ ] No "WS cache miss" in poller log
- [ ] Screener picks appear at 09:50
- [ ] Post-market report sent at 15:35

---

## Commands

```bash
# Check Chrome
ps aux | grep "google-chrome.*18801" | grep -v grep

# Check Bot
ps aux | grep "python3.*bot\.py" | grep -v grep

# Check Poller (should return 1)
pgrep -c -f "python3.*poller\.py"

# Check CDP
curl -s http://127.0.0.1:18801/json/version | grep Browser

# Start Chrome manually
bash /home/mino/tasi-exec/start-chrome.sh
```

---

## Known Issues & Status

| Issue | Status | Fix |
|-------|--------|-----|
| Chrome CDP not working | ✅ Fixed | `--single-process` flag |
| WS feed empty cache | ✅ Fixed | Single poller instance |
| Post-market basic | ✅ Fixed | Comprehensive v2 |
| WS logger | ✅ New | Real-time price logging |
| Mid-day re-screening | 🅿️ Parked | Area of improvement |

---

## File Reference

| File | Purpose |
|------|---------|
| `bot.py` | Telegram bot + order execution |
| `poller.py` | Price poller + signal detection |
| `derayah_keepalive.py` | Session keepalive |
| `derayah_api.py` | Derayah REST API wrapper |
| `post_market_v2.py` | Post-market analysis |
| `ws_logger.py` | WS price logger |
| `start-chrome.sh` | Chrome startup script |
| `picks.json` | Daily screener picks |
| `positions.json` | Open positions |
| `performance.json` | Performance tracking |
| `learning.json` | Strategy learning loop |
| `sharia_list.json` | Sharia-compliant tickers |
| `reports/` | Daily post-market reports |
| `memory/issues.md` | Issues log |

---

## Crons

| Name | Schedule | Purpose |
|------|----------|---------|
| `derayah-keepalive` | Every 5 min | Session health check |
| `tasi-price-poller` | 10:00 Sun–Thu | Price feed + trading |
| `tasi-premarket-screener` | 09:50 Sun–Thu | Stock selection |
| `post-market-analysis` | 15:35 Sun–Thu | Performance review |

---

## Contact

- **Bot:** @TASIExecBot
- **Group:** TASI Execution (-5235925419)
- **Amin:** @AMAS989
- **Mino:** minothejellyfish@gmail.com

---

*Generated: 2026-05-20*
*Next update: After Thursday's session (2026-05-21)*

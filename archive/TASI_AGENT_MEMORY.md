# TASI Dedicated Agent - System Memory & Configuration
## v4.3 — 2026-06-10: 5-min refresh_cron (was 15-min)

## System Overview
- **System Path**: `/home/mino/tasi-exec/`
- **Market**: Tadawul All Share Index (Saudi Stock Exchange)
- **Trading Hours**: 10:00 AM - 3:00 PM (Sun-Thu), GMT+3
- **Hard Close**: 14:45 (auto STAND DOWN)
- **Auto Start**: 09:50 (premarket screener) + 10:00 (poller start)

## Architecture Components

### Core Files
| File | Purpose |
|------|---------|
| `bot.py` | Telegram bot, keepalive, commands, capital refresh |
| `poller.py` | Price polling, entry/exit signals, auto-trade execution |
| `ws_probe.py` | WebSocket price capture (restarts every 90s) |
| `screener.py` | Premarket stock screening |
| `midscreen_ws.py` | Mid-session momentum screening (10:30, 12:00, 13:30) |
| `market_regime.py` | Market regime classifier (TRENDING/NEUTRAL/DEFENSIVE) |
| `derayah_api.py` | REST API wrapper for orders |
| `capital_tracker.py` | P&L tracking, capital calculation |

### Configuration Files
| File | Purpose |
|------|---------|
| `capital.json` | Account balance (all fields: available, grand_total, securities_value, money_transfer, fees) |
| `positions.json` | Open positions with entry prices |
| `blocked_symbols.txt` | Symbols blocked by filters |
| `stand_down` | Trading halt marker file |
| `regime.json` | Current market regime |
| `picks_1030.json` | Midscreen 1 results |
| `picks_1200.json` | Midscreen 2 results |
| `picks_1330.json` | Rescreen results |

## Daily Schedule (Auto)
```
09:50 — tasi-premarket-screener cron (announces picks in group)
09:55 — cleanup_stand_down.sh (remove stand_down + blocked_symbols)
10:00 — tasi-price-poller cron starts poller.py
10:00-10:30 — Gap-up entry window
10:30 — tasi-midscreen-1 cron (announces picks in group)
12:00 — tasi-midscreen-2 cron (announces picks in group)
13:30 — tasi-rescreen cron (announces picks in group)
14:45 — Auto STAND DOWN (create stand_down file)
15:10 — Market closes
15:35 — post-market-analysis cron runs
```

## Key Changes from Jun 3, 2026 Session

### 1. Keepalive Completely Redesigned (CRITICAL)
**OLD**: `derayah_keepalive.py` + `chromium-derayah.service` + "Derayah Trade" click
**NEW**: `bot.py` native keepalive using `TickerChartUrl` endpoint
- **Method**: `GET /apispark/trade/TickerChartUrl` → open SSO URL in new tab
- **Why**: Works even with expired Derayah Access Token (uses server-side session)
- **Fallback**: "Derayah Trade" nav link click
- **Old systems**: DISABLED (derayah_keepalive.py stopped, chromium-derayah.service disabled)

### 2. Capital Tracking Fixed
- **Before**: Only saved `available_capital`, lost grand_total/securities_value/fees
- **After**: `save_capital_full()` saves ALL fields
- **30-min auto-refresh**: `_capital_refresh_thread()` in bot.py scrapes actual Derayah balance
- **Sell recalculation**: Updates available (+net), grand_total (-fees), securities_value=0

### 3. Market Regime Now Visible
- **Before**: Regime calculated but not shown in STATUS
- **After**: `get_status()` displays current regime + parameters
- **Classification**: TRENDING/NEUTRAL/DEFENSIVE based on session return + VWAP

### 4. Midscreens Now Announced
- **Before**: Saved to JSON only, no group announcement
- **After**: All 3 midscreens (10:30, 12:00, 13:30) announce picks in Telegram group
- **Format**: Markdown table with symbol, entry zone, score, change%

### 5. In-Zone Priority Entry Logic (OPTION B) — NEW
- **Before**: Monitored top 5 by raw score only. Gap-up days = no entries (all gapped above zone)
- **After**: If top 5 are ALL out of zone, re-sort by actionability (score × zone_bonus) and monitor top 10
- **Actionability scoring**:
  - In zone: score × 1.5
  - Near zone (within 2%): score × 1.2
  - Below zone: score × 0.5
  - Gapped above (>2%): score × 0.3
- **Why**: On gap-up days, high-score picks gap above zone while lower-score picks from later screens remain in zone
- **Simulation results (Jun 2)**: Current system found 2 entries (+1.33%). Option B would have found same entries.
- **Simulation results (Jun 3)**: Current system found 0 entries. Option B would have found 3 in-zone entries (expensive/flat stocks, ~0% profit). Market was flat — no system could profit.
- **Conclusion**: Entry logic was not the problem. Market conditions (gap-up + flat) caused no entries. System proved it works on Jun 2.

### 6. Session Refresh Cron — 5-min interval (v4.3)

**File**: `/home/mino/tasi-exec/derayah_refresh_cron.sh`
**Crontab**: `*/5 * * * *` (was `*/15 * * * *` before 2026-06-10 21:00)
**Logs**: `/home/mino/tasi-exec/refresh_cron.log`

**Per-run flow:**
1. Decode `Derayah_accesstoken` exp
2. Call `GET /apispark/trade/TickerChartUrl` with Bearer
3. **If 200** (access alive OR within 5-min grace):
   - Parse SSO URL from response
   - Navigate TC tab to SSO URL
   - Poll localStorage for new TC_DERAYAH (up to 30s)
   - Verify with `GET /trading/Portfolio/List` (expect 200)
4. **If 401** (access dead, past grace):
   - Trigger Phase 3 auto-recovery via email OTP (24 sec)
   - Bails to manual + Telegram DM only if reCAPTCHA detected

**Worst-case bot gap**: 5 min 24 sec (down from 15 min 24 sec)
**Typical bot gap**: 0 sec (grace period catches it)
**Cost**: 12 cron runs/hr, each ~1-2 sec, negligible

### 7. Position 4021 Closed (legacy, see Jun 3 section below)
- Sold at 12:57 Jun 3 via MARKET order
- `positions.json`: `"closed": true, "close_time": "2026-06-03T12:57:14"`
- `capital.json`: All fields updated with actual scraped values
- No re-sell risk (securities_value=0)

## Session Startup Protocol
When a new TASI session starts:
1. Read `TASI_SYSTEM_BLUEPRINT.md` for full architecture
2. Check process statuses: `ps aux | grep -E "bot.py|ws_probe|poller"`
3. Check `capital.json` and `positions.json`
4. Check `regime.json` for current regime
5. Check Chrome/CDP on port 18801
6. Report: Market status, positions, regime, process health

## Authentication
- JWT Token: Managed via Chrome/CDP cookies + `TC_DERAYAH` localStorage
- **Refresh**: Via `TickerChartUrl` endpoint (not OAuth refresh_token)
- **Expiry**: TC JWT ~60 min, Derayah Access Token ~60 min
- **Keepalive**: Every 15 min via bot.py

## Common Issues & Solutions
1. **Token Expired**: Bot keepalive will refresh via TickerChartUrl endpoint
2. **Poller Down**: Check PID, run `systemctl --user start tasi-poller`
3. **WebSocket No Frames**: Check Chrome/CDP on port 18801
4. **Capital Wrong**: Check if `_capital_refresh_thread` is running in bot
5. **No Trades**: Check if STAND DOWN file exists, check regime parameters

## User Preferences
- Notifications: Group for picks, DM for status/errors
- Trading: User confirms all trades, agent suggests only
- Auto-close: 14:45 hard stand_down
- Capital refresh: Every 30 min during market hours

## Crons (agentId: main)
| Name | Time | Purpose | Delivery |
|------|------|---------|----------|
| tasi-premarket-screener | 09:50 | Scan Sharia stocks | Group |
| tasi-price-poller | 10:00 | Start price monitoring | Silent |
| tasi-midscreen-1 | 10:30 | Early momentum | Group |
| tasi-midscreen-2 | 12:00 | Mid-session scan | Group |
| tasi-rescreen | 13:30 | Pre-cutoff eval | Group |
| post-market-analysis | 15:35 | Daily review | DM |
| ocean-health-monitor | Every 30m | System health | Silent |

## Disabled/Archived
- ~~`derayah_keepalive.py`~~ — Process killed, no auto-restart
- ~~`chromium-derayah.service`~~ — `systemctl disable`, auto-restart removed

## Current Status (As of Jun 3, 2026)
- Market: CLOSED (until 10:00 AM Jun 4)
- Regime: TRENDING (session return +0.25%, above VWAP)
- Positions: None (4021 closed)
- Capital: 993.87 SAR (all fields accurate)
- Bot: Running (PID 17635, uptime 1h+)
- Poller: Running (PID 13125, uptime 5h+)
- ws_probe: Running (PID 1454, uptime 10h+)
- Chrome/CDP: Running on port 18801
- WebSocket: 226,551 frames today (5.3h continuous, 2 minor gaps)
- Keepalive: Native bot.py keepalive (TickerChartUrl method)
- Entry Logic: Option B (In-Zone Priority) — ready for Jun 4

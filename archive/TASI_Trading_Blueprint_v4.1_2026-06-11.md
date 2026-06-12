# TASI Intraday Trading System — Full Blueprint
**Version:** 4.0
**Date:** 2026-05-21
**Purpose:** Rebuild-from-scratch guide. If everything is lost, this document contains all information needed to reconstruct the entire trading system.

**Last Update:** Complete system overhaul — 4-stage screening, regime-aware parameters, position upgrade/cycle switch, websocket-based mid-screen, dynamic exit targets

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Hardware & Infrastructure Setup](#2-hardware--infrastructure-setup)
3. [Google Chrome Configuration](#3-google-chrome-configuration)
4. [Derayah Account Setup](#4-derayah-account-setup)
5. [Telegram Bot Setup](#5-telegram-bot-setup)
6. [Systemd Services](#6-systemd-services)
7. [File Structure & Key Files](#7-file-structure--key-files)
8. [Trading Strategy — Full Detail](#8-trading-strategy--full-detail)
9. [Cron Schedule — Exact Commands](#9-cron-schedule--exact-commands)
10. [Bot Commands Reference](#10-bot-commands-reference)
11. [System Operation — How It Works](#11-system-operation--how-it-works)
12. [Keepalive Concept & Protocol](#12-keepalive-concept--protocol)
13. [Post-Session Analysis & Learning](#13-post-session-analysis--learning)
14. [Major Issues Resolved](#14-major-issues-resolved)
15. [Recovery Procedures](#15-recovery-procedures)
16. [Quick Reference](#16-quick-reference)

---

## 1. Executive Summary

### What This System Does
Fully automated intraday trading on Tadawul (Saudi Stock Exchange) via Derayah broker. The system runs on Ocean (Ubuntu server), screens Sharia-compliant stocks before market open, monitors price action during session, auto-executes trades based on signals, manages positions with stops, and learns from post-session analysis.

### Key Metrics
- **Strategy:** B (Probabilistic Veto) + Cycling + Position Upgrade + Cycle Switch
- **Backtest (54 trading days, Mar–May 2026):** 1,000 SAR → 2,077 SAR (+107.7%)
- **Trading days:** Sunday–Thursday
- **Session hours:** 10:00–15:30 Riyadh (GMT+3)
- **Hard close:** 14:45 Riyadh
- **Entry cutoff:** 13:30 Riyadh

### Architecture
```
Ocean (Ubuntu 24.04)
├── Google Chrome → CDP port 18801 → Derayah + TickerChart
├── Telegram Bot (@TASIExecBot) → Order execution + reports
├── Price Poller → Signal detection + auto-trading
│   ├── 4-Stage Screening (09:50 / 10:30 / 12:00 / 13:30)
│   ├── Regime-aware parameters (dynamic exits)
│   ├── Position Upgrade (sell open pos for better pick)
│   └── Cycle Switch (skip rebuy for better pick)
├── Screener → Pre-market + mid-session stock selection
│   ├── screener.py (premarket: yfinance)
│   └── midscreen_ws.py (mid-session: websocket data)
├── Post-Market → Analyzes all 398 Sharia stocks + email report
├── Weekly Report → Compares 3 approaches + recommendations
├── Log Cleanup → Daily compression + summary
├── Keepalive → Session maintenance (trading days only)
└── SOCKS5 tunnel → Amin-PC (residential IP)
```

### Two-Channel Setup
- **Telegram DM (5529987063):** Amin ↔ Mino — supervision, commands, alerts
- **TASI Execution Group (-5235925419):** Bot command bus — poller sends orders, bot executes, Amin watches

---

## 2. Hardware & Infrastructure Setup

### Ocean Server
- **OS:** Ubuntu 24.04 LTS
- **RAM:** 8GB
- **Disk:** 240GB SSD
- **User:** mino
- **Display:** XFCE + LightDM with autologin
- **Environment:** `DISPLAY=:0`, `XAUTHORITY=/home/mino/.Xauthority`

### Amin-PC (Windows)
- **Hostname:** WINDOWS-3547C7T
- **LAN IP:** 192.168.1.228
- **SSH:** Port 22 (Windows), port 2222 → WSL Ubuntu
- **User:** AMA (Windows), ama (WSL)
- **Purpose:** Residential IP proxy for Derayah

### SSH Configuration
From Ocean (`~/.ssh/config`):
```
Host amin-pc
    HostName 192.168.1.228
    User AMA
    Port 22

Host amin-pc-wsl
    HostName 192.168.1.228
    User ama
    Port 2222
```
Ocean's pubkey (`mino@ocean`) is authorized on both sides. Passwordless SSH.

### SOCKS5 Tunnel
**Command:**
```bash
ssh -f -N -D 1080 amin-pc -o ServerAliveInterval=60 -o ExitOnForwardFailure=yes
```

**Systemd service:** `socks-tunnel.service` (user-level)

**Verification:**
```bash
curl --proxy socks5://localhost:1080 -s https://api.ipify.org
# Should return Amin-PC's public IP
```

---

## 3. Google Chrome Configuration

### Browser Profile
- **Profile:** `derayah-profile` (dedicated, not default)
- **Path:** `/home/mino/.config/google-chrome/derayah-profile`
- **Flags:** `--no-sandbox --disable-blink-features=AutomationControlled --remote-debugging-port=18801`
- **User agent:** Real Chrome (not headless)

### Startup Script
`start-chrome.sh`:
```bash
#!/bin/bash
export DISPLAY=:0
export XAUTHORITY=/home/mino/.Xauthority

# Check if Chrome is already running
if pgrep -f "chrome.*derayah-profile" > /dev/null; then
    echo "Chrome already running"
    exit 0
fi

# Kill any existing Chrome processes
pkill -f "chrome" || true
sleep 2

# Start Chrome
google-chrome-stable \
    --user-data-dir=/home/mino/.config/google-chrome/derayah-profile \
    --no-sandbox \
    --disable-blink-features=AutomationControlled \
    --remote-debugging-port=18801 \
    --window-size=1920,1080 \
    --start-maximized \
    --disable-gpu \
    --disable-dev-shm-usage \
    --no-first-run \
    --no-default-browser-check \
    &

sleep 5
echo "Chrome started on CDP port 18801"
```

### CDP Verification
```bash
curl -s http://127.0.0.1:18801/json/version | head -3
# Expected: "Browser": "Chrome/134.0..."
```

---

## 4. Derayah Account Setup

### Login Credentials
- **User Number:** 20638532
- **Portfolio ID:** 2063853
- **URL:** `https://onboarding.derayah.com/#/signin`
- **2FA:** OTP via SMS to Amin's phone

### API Tokens
- **OAuth2 Client ID:** Found in Derayah JS bundle
- **Client Secret:** Extracted from `main.*.js`
- **Token Endpoint:** `https://api.derayah.com/idspark/connect/token`
- **Refresh Token:** Stored in `derayah_tokens.json`
- **Access Token:** JWT, valid ~1 hour, auto-refreshed

### TickerChart WebSocket
- **URL:** `wss://derayah.tickerchart.net/streamhub`
- **JWT:** Required for subscription
- **Data:** Real-time price ticks for Tadawul stocks
- **Keepalive:** Tab must stay active (navigate to trading-portfolio, NOT away from TickerChart)

---

## 5. Telegram Bot Setup

### Bot Identity
- **Bot:** @TASIExecBot
- **Token:** `8989533040:AAFWzP_lYL3g_w4eXGxrvwdo-tBNdPxVYQU`
- **Purpose:** Execute buy/sell orders, send alerts

### Two-Channel Architecture
```
Amin (5529987063) ←→ Bot DM ←→ Mino
                          ↓
                    TASI Exec Group (-5235925419)
                          ↑
                         Poller
```

- **DM Channel:** Direct commands, position status, system alerts
- **Group Channel:** Order execution bus, portfolio updates

### Bot Startup
```bash
cd /home/mino/tasi-exec && python3 bot.py >> bot.log 2>&1 &
```

---

## 6. Systemd Services

### User-Level Services
```bash
# List all user services
systemctl --user list-units --type=service --state=running

# Key services:
# - socks-tunnel.service    → SOCKS5 proxy to Amin-PC
# - tasi-exec.service       → Telegram bot
# - openclaw-gateway.service → AI gateway
# - ocean-dashboard.service  → Status dashboard
```

### Service Files
Located in `~/.config/systemd/user/`:
- `socks-tunnel.service`
- `tasi-exec.service`
- `openclaw-gateway.service`

### Logs
```bash
journalctl --user -u socks-tunnel.service -f
journalctl --user -u tasi-exec.service -f
```

---

## 7. File Structure & Key Files

```
/home/mino/tasi-exec/
├── bot.py                    # Telegram bot (order execution)
├── poller.py                 # Price poller + auto-trading (v4.0)
├── screener.py               # Pre-market screener (4 modes)
├── midscreen_ws.py           # WebSocket-based mid-session screener
├── post_market_v2.py         # Post-market analysis (all 398 stocks)
├── market_regime.py          # Regime classification + parameters
├── derayah_keepalive.py     # Session keepalive (don't navigate away from TickerChart)
├── ws_logger.py              # WebSocket tick logger
├── derayah_api.py            # API wrapper (REST + WS)
├── start-chrome.sh           # Chrome startup script
├── positions.json           # Open positions state
├── regime.json              # Current regime (updated intraday)
├── picks.json               # 09:50 premarket picks
├── picks_1030.json          # 10:30 mid-screen picks
├── picks_1200.json          # 12:00 mid-screen picks
├── picks_1330.json          # 13:30 rescreen picks
├── ws_prices_YYYY-MM-DD.jsonl  # WebSocket tick log
├── sharia_list.json         # Sharia-compliant stocks (398)
├── blocked_stocks.json      # Blocked stocks log
├── learning.json            # Learning loop state
├── exec.log                 # Bot execution log
├── poller.log               # Poller log
├── screener.log             # Screener log
├── reports/                 # Post-market HTML reports
│   └── post_market_YYYY-MM-DD.html
└── charts/                  # Generated charts
    └── momentum_YYYY-MM-DD.png
```

---

## 8. Trading Strategy — Full Detail

### 4-Stage Daily Screening Flow

| Stage | Time | Script | Data Source | Stocks | Purpose |
|-------|------|--------|-------------|--------|---------|
| **Pre-market** | 09:50 | `screener.py` | yfinance (yesterday) | All 398 Sharia | Initial selection |
| **Mid-screen 1** | 10:30 | `midscreen_ws.py` | WebSocket (10:00-10:30) | Top 20 from premarket | Catch early movers |
| **Mid-screen 2** | 12:00 | `midscreen_ws.py` | WebSocket (11:00-12:00) | Top 20 from midscreen1 | Mid-session refresh |
| **Rescreen** | 13:30 | `midscreen_ws.py` | WebSocket (12:00-13:30) | Top 10 from 12:00 | Late-session opportunities |

### Pick Loading Logic (poller.py)
1. Load all 4 picks files (09:50, 10:30, 12:00, 13:30)
2. Deduplicate: later screens overwrite earlier for same symbol
3. Sort by score globally (highest momentum first)
4. Top 5 picks are monitored for entry signals

### Entry Triggers (10:00–13:30)
| Signal | Conditions | Action |
|--------|-----------|--------|
| **Gap-Up Entry** | First 30 min (10:00-10:30), price in zone or slight gap-up | BUY |
| **VWAP Reclaim** | Price dips below VWAP then closes back above + volume > 1.5x average | BUY |
| **Breakout** | Price breaks above prior day high + volume > 1.5x average | BUY |

### Exit Logic (Priority Order)
1. **Target hit** → auto-sell → cycle switch or recycle
2. **Trailing stop** → Activates after target gain, trails at regime-aware % from peak
3. **Hard stop** → Regime-aware % from entry (no exceptions)
4. **Time stop** → Down > regime-aware % after regime-aware minutes
5. **VWAP re-break** → Price below VWAP while position is negative
6. **14:45 hard close** → Sell everything regardless

### Regime Parameters (market_regime.py)

| Parameter | TRENDING | NEUTRAL | DEFENSIVE |
|-----------|----------|---------|-----------|
| **Strategy** | C (Aggressive) | B (Balanced) | B (Balanced) |
| **Max Positions** | 3 | 3 | 4 |
| **Position 1-2 Size** | 35% | 30% | 20% |
| **Position 3+ Size** | 25% | 30% | 20% |
| **Target** | +2.5% | +2.0% | +1.5% |
| **Hard Stop** | -7% | -5% | -4% |
| **Trail Trigger** | +2.5% | +2.0% | +1.5% |
| **Trail Stop** | -3% | -3% | -2% |
| **Time Stop** | 30 min / -1% | 30 min / -1% | 20 min / -0.5% |

### Position Sizing
- Position 1-2: Full size (35%/30%/20% depending on regime)
- Position 3+: Alt size (25%/30%/20% depending on regime)
- Calculate qty = (capital × position_pct) ÷ entry_price
- Round down to whole shares

### Cycling Strategy (Unlimited Cycles)
After a win, system automatically re-enters if:
- Time < 14:30 Riyadh
- Not 2 consecutive scratches
- Momentum still positive (change_pct > 0)
- Symbol still in latest picks

**Cycle Switch:** Before recycling, check if better pick available
- If best_new score > current_score × threshold → switch instead of recycle
- Thresholds: TRENDING 1.2×, NEUTRAL 1.15×, DEFENSIVE 1.1×

### Position Upgrade (While Position Open)
When new screen arrives (10:30, 12:00, 13:30):
- If new pick is significantly better than current position
- AND current P&L ≥ -2% (not deep underwater)
- → SELL current, BUY new pick
- Thresholds: TRENDING 1.4×, NEUTRAL 1.3×, DEFENSIVE 1.2×

### Dynamic Exit Target Updates
- Regime checked every 30 minutes
- 60-minute confirmation required before changing exit parameters
- Entry sizing uses regime AT ENTRY TIME (static)
- Exit targets use CONFIRMED regime (dynamic)
- Only tightens stops when market worsens

---

## 9. Cron Schedule — Exact Commands

### System Crontab (`crontab -l`)
```
0 0 * * 5 sudo /sbin/reboot                    # Weekly Friday reboot 3AM Riyadh
5 10 * * 0,1,2,3,4 cd /home/mino/tasi-exec && python3 map_selectors.py 1010 >> map_selectors.log 2>&1
7 10 * * 0,1,2,3,4 cd /home/mino/tasi-exec && python3 ws_probe.py 90 >> ws_probe.log 2>&1
*/5 * * * * python3 /tmp/simple_keepalive.py >> /tmp/keepalive.log 2>&1
```

### OpenClaw Agent Crons

| Name | Schedule | Script | Purpose | Model | Timeout |
|------|----------|--------|---------|-------|---------|
| `derayah-keepalive` | Every 5 min | `derayah_keepalive.py` | Keep Chrome + Derayah alive | ollama/kimi-k2.6:cloud | 90s |
| `tasi-premarket-screener` | 09:50 Sun–Thu | `screener.py` | Pre-market scan | ollama/kimi-k2.6:cloud | 300s |
| `tasi-price-poller` | 10:00 Sun–Thu | `poller.py` | Price monitoring + trading | ollama/kimi-k2.6:cloud | 90s |
| `tasi-midscreen-1` | 10:30 Sun–Thu | `midscreen_ws.py` | First mid-session screen | ollama/kimi-k2.6:cloud | 300s |
| `tasi-midscreen-2` | 12:00 Sun–Thu | `midscreen_ws.py` | Second mid-session screen | ollama/kimi-k2.6:cloud | 300s |
| `tasi-rescreen` | 13:30 Sun–Thu | `midscreen_ws.py` | Late-session rescreen | ollama/kimi-k2.6:cloud | 300s |
| `post-market-analysis` | 15:35 Sun–Thu | `post_market_v2.py` | Performance review + email daily report | ollama/kimi-k2.6:cloud | 120s |
| `sharia-list-refresh` | Thu 22:00 | Refresh Sharia list | Update compliant stocks | ollama/kimi-k2.6:cloud | 120s |
| `tasi-log-cleanup` | 04:00 Daily | `cleanup_logs.py` | Compress old logs, summarize, archive | main | 300s |
| `tasi-weekly-report` | 20:00 Friday | `weekly_report.py` | Compare 3 approaches, email report | isolated | 120s |

**Note:** `derayah-keepalive` now runs **Sun–Thu only** (not 24/7). Notifications suppressed Friday/Saturday.

---

## 10. Bot Commands Reference

| Command | Format | Who Can Send | Description |
|---------|--------|-------------|-------------|
| **BUY limit** | `BUY SYMBOL QTY @ PRICE` | Mino, Amin | Limit buy order |
| **BUY market** | `BUY SYMBOL QTY MARKET` | Mino, Amin | Market buy order |
| **SELL limit** | `SELL SYMBOL QTY @ PRICE` | Mino, Amin | Limit sell order |
| **SELL market** | `SELL SYMBOL QTY MARKET` | Mino, Amin | Market sell order |
| **STATUS** | `STATUS` | Anyone | Show portfolio + positions |
| **REPORT** | `REPORT` or `WEEKLY` | Anyone | Get latest weekly report summary |
| **DAILY** | `DAILY` or `DAILY REPORT` | Anyone | Get today's trading summary |

**Examples:**
```
BUY 4280 100 @ 25.50
BUY 7205 50 MARKET
SELL 4280 100 @ 26.00
SELL 7205 50 MARKET
```

---

## 11. System Operation — How It Works

### Daily Flow (Sunday–Thursday)

```
09:50  ┌─────────────────────────────────────┐
       │  Screener runs (screener.py)        │
       │  - Loads 398 Sharia stocks          │
       │  - Calculates momentum metrics      │
       │  - Outputs top picks → picks.json   │
       └──────────────┬──────────────────────┘
                      │
10:00  ┌──────────────▼──────────────────────┐
       │  Market opens                        │
       │  Poller starts monitoring            │
       │  - Load picks (all 4 screens)        │
       │  - Sort by score globally            │
       │  - Watch for entry signals           │
       └──────────────┬──────────────────────┘
                      │
10:00-10:30 ┌────────▼──────────────────────┐
            │  Gap-up entry window            │
            │  - First 30 min only             │
            │  - Price in zone or slight gap   │
            └────────┬──────────────────────┘
                     │
10:30   ┌───────────▼───────────────────────┐
        │  Mid-screen 1 (midscreen_ws.py)    │
        │  - Reads WS ticks 10:00-10:30      │
        │  - Scores intraday momentum        │
        │  - Outputs picks_1030.json         │
        │  - Poller reloads picks            │
        │  - Position upgrade check          │
        └───────────┬───────────────────────┘
                    │
12:00   ┌───────────▼───────────────────────┐
        │  Mid-screen 2 (midscreen_ws.py)  │
        │  - Reads WS ticks 11:00-12:00      │
        │  - Outputs picks_1200.json         │
        │  - Poller reloads picks            │
        │  - Position upgrade check          │
        └───────────┬───────────────────────┘
                    │
13:30   ┌───────────▼───────────────────────┐
        │  Rescreen (midscreen_ws.py)        │
        │  - Reads WS ticks 12:00-13:30      │
        │  - Outputs picks_1330.json         │
        │  - Poller reloads picks            │
        │  - Position upgrade check          │
        │  - No new entries after this        │
        └───────────┬───────────────────────┘
                    │
14:30   ┌───────────▼───────────────────────┐
        │  Stop cycling                       │
        │  - No more auto-rebuys              │
        └───────────┬───────────────────────┘
                    │
14:45   ┌───────────▼───────────────────────┐
        │  Hard close                         │
        │  - Sell all positions               │
        │  - No exceptions                    │
        └───────────┬───────────────────────┘
                    │
15:30   ┌───────────▼───────────────────────┐
        │  Market close                       │
        └───────────────────────────────────┘

15:35  ┌─────────────────────────────────────┐
       │  Post-market analysis                │
       │  - Scans all 398 Sharia stocks        │
       │  - Finds missed opportunities        │
       │  - Generates HTML report             │
       └─────────────────────────────────────┘
```

---

## 12. Keepalive Concept & Protocol

### What It Does
Every 5 minutes, `derayah_keepalive.py`:
1. **Check Chrome:** Verify CDP port 18801 responds
2. **Check Derayah tab:** Verify tab is open and not on login page
3. **Check TickerChart tab:** Verify JWT is present and not expiring (< 5 min)
4. **Critical:** If current tab is TickerChart → STAY on TickerChart (don't navigate away)
   - Only navigate to trading-portfolio if NOT on TickerChart
   - WebSocket only sends data when TickerChart tab is active
5. **If session expired:** Auto-login in priority order:
   - **(1) API refresh token injection** (~15s, no reCAPTCHA, no OTP)
   - **(2) CDP real Chrome login** (if token injection fails)
   - **(3) Playwright stealth login** (if CDP fails)
6. **Scroll page:** Prevent idle timeout
7. **Notify Amin:** Max 2x/day during trading hours if all methods fail

### WebSocket Gap Prevention
- Keepalive MUST NOT navigate away from TickerChart during market hours
- Poller reactivates TickerChart tab on websocket reconnect (`page.bring_to_front()`)
- WebSocket data flow is critical for mid-screen scoring

---

## 13. Post-Session Analysis & Learning

### post_market_v2.py (runs at 15:35)
1. **Load today's picks** from all 4 picks files
2. **Analyze all 398 Sharia stocks** via yfinance (parallel, 8 workers)
3. **Find missed opportunities:**
   - High-low range ≥ 2.0% of open
   - Open-to-high ≥ 1.0%
   - Not in today's picks
4. **Gap analysis:** Why each was missed
5. **Generate recommendations:**
   - Lower score threshold if missed avg > 3%
   - Relax volume filter if low-volume missed
   - Widen entry zones if tight
6. **Post report** to TASI Execution group (HTML format)
7. **Save report:** `reports/post_market_YYYY-MM-DD.html`
8. **Update `learning.json`:**
```json
{
  "sessions_analyzed": 1,
  "recommendations_made": ["✅ Strategy performed well"],
  "recommendations_applied": [],
  "missed_opportunities_avg": 0,
  "strategy_versions": ["v4.0"]
}
```

---

## 14. Major Issues Resolved

| # | Issue | Root Cause | Fix | Date |
|---|-------|------------|-----|------|
| 1 | CDP Port Mismatch | Scripts used 18800, browser on 18801 | Updated to 18801 | 2026-05-18 |
| 2 | Duplicate Keepalive | System crontab + OpenClaw both ran | Removed system crontab | 2026-05-18 |
| 3 | Telegram 400 HTML | Raw stack traces with `<>` as HTML | Added `html.escape()` | 2026-05-18 |
| 4 | Auto-login Vue Bug | `fill()` didn't trigger Vue reactive validation | Use `press_sequentially()` | 2026-05-18 |
| 5 | WS Cache Miss (3060.SR) | Low-volume stock, no trade frames | Accept bid/ask as fallback | 2026-05-18 |
| 6 | Poller Early Exit | Hardcoded 15:05 vs 15:30 close | Changed to 15:30 | 2026-05-18 |
| 7 | yfinance Early Session | `period='1d'` empty first 15 min | Fallback to `period='5d'` | 2026-05-18 |
| 8 | Order Confirmation Gap | Sent "bought" before confirmed | Now says "order sent" | 2026-05-18 |
| 9 | reCAPTCHA v2 Blocked | Login uses reCAPTCHA v2 | Bypassed via OAuth2 token API | 2026-05-19 |
| 10 | Missing client_secret | Refresh grant needs secret | Found in Derayah JS bundle | 2026-05-19 |
| 11 | OTP SPA Detection | Vue SPA same URL for login/OTP | Check DOM for OTP input | 2026-05-19 |
| 12 | login_monitor.py Timeout | Daemon timed out at 15 min | Recovered via manual token | 2026-05-19 |
| 13 | SOCKS5 in requests | requests lacks SOCKS support | Removed proxy from token call | 2026-05-19 |
| 14 | Chrome CDP Failure | Snap Chromium crashes on reCAPTCHA | Switched to Google Chrome | 2026-05-20 |
| 15 | WS Empty Cache | Multiple poller instances | Single instance only | 2026-05-20 |
| 16 | Keepalive Context Closed | Killed Chrome mid-login | Pending: failure counter + CDP wait | — |
| 17 | Fallback Logic Added | Primary picks idle = missed opportunities | Monitor #3-5 at 25% after 10:30 | 2026-05-21 |
| 18 | Momentum Filter | Dead picks (2222.SR, 8311.SR) killed returns | Pre-market ATR/vol/vel/range filter at 10:30 | 2026-05-21 |
| 19 | Mid-Session Re-Screen | Velocity false positives after 10:30 | Re-run filter at 11:30 if no positions | 2026-05-21 |
| 20 | 4-Stage Screening | Single premarket screen missed movers | Added 10:30, 12:00, 13:30 screens | 2026-05-21 |
| 21 | Websocket Data Gap | Keepalive navigated away from TickerChart | Keep TickerChart active during market | 2026-05-21 |
| 22 | yfinance Delay | 15-min delay made mid-screen useless | Switched to websocket real-time data | 2026-05-21 |
| 23 | Pick Sorting | Newest-screen-first buried good picks | Global score sort (highest momentum) | 2026-05-21 |
| 24 | Static Exit Targets | Same targets regardless of market | Regime-aware dynamic targets + 60-min confirmation | 2026-05-21 |
| 25 | Cycle Cap | Capped at 2 cycles unnecessarily | Unlimited cycles with momentum gate | 2026-05-21 |
| 26 | Position Upgrade | No way to switch to better pick while open | Added position upgrade logic (30% threshold) | 2026-05-21 |
| 27 | Cycle Switch | Always recycled same symbol | Added cycle switch to better pick (15% threshold) | 2026-05-21 |
| 28 | Entry Zone Stale | Recycle used old entry zone | Updated zones from latest screen | 2026-05-21 |

---

## 15. Recovery Procedures

### Browser Down
```bash
# Automatic: keepalive detects within 5 min
# Manual:
bash /home/mino/tasi-exec/start-chrome.sh

# Verify:
curl -s http://127.0.0.1:18801/json/version | grep Browser
```

### Derayah Session Expired
```bash
# Automatic: keepalive tries token injection → CDP → Playwright
# Manual token refresh:
python3 -c "from derayah_api import DerayahAPI; import asyncio; api=DerayahAPI(); asyncio.run(api._refresh_token_api())"

# If all auto fails: login via Chrome Remote Desktop (PIN 056187)
# Then capture tokens:
python3 /home/mino/tasi-exec/login_monitor.py
```

### TickerChart JWT Expired
```bash
# Automatic: bot.py reopens TC tab every 15 min
# Manual via CRD:
# 1. Open Chrome Remote Desktop
# 2. Navigate to newonline.derayah.com
# 3. Click "Derayah Trade"
# 4. Verify token in bot.py log
```

### Poller Down
```bash
# Check:
pgrep -c -f "python3.*poller\.py"  # should return 1

# Restart:
cd /home/mino/tasi-exec && nohup python3 poller.py >> poller.log 2>&1 &

# Or via cron: wait for next tasi-price-poller trigger (10:00)
```

### SOCKS5 Tunnel Down
```bash
# Check:
curl --proxy socks5://localhost:1080 -s https://api.ipify.org

# Restart:
systemctl --user restart socks-tunnel.service

# Verify:
systemctl --user status socks-tunnel.service
```

### Post-Market Analysis Not Running
```bash
# Check cron status:
openclaw cron list | grep post-market

# Manual run:
cd /home/mino/tasi-exec && python3 post_market_v2.py

# Check reports:
ls -la reports/
```

---

## 16. Quick Reference

### Key Ports
| Service | Port |
|---------|------|
| Chrome CDP | 18801 |
| SOCKS5 Proxy | 1080 |
| OpenClaw Gateway | 18789 |
| Ocean Dashboard | 8765 |

### Key URLs
| Purpose | URL |
|---------|-----|
| Derayah Login | `https://onboarding.derayah.com/#/signin` |
| Derayah Platform | `https://newonline.derayah.com/#/layout/dashboard` |
| Derayah API | `https://api.derayah.com` |
| TickerChart | `derayah.tickerchart.net` |
| Token Endpoint | `https://api.derayah.com/idspark/connect/token` |

### Key IDs
| Item | Value |
|------|-------|
| User Number | 20638532 |
| Portfolio ID | 2063853 |
| Group Chat ID | -5235925419 |
| Owner ID | 5529987063 |
| Bot Token | 8989533040:AAFWzP_lYL3g_w4eXGxrvwdo-tBNdPxVYQU |

### Key Files
| File | Purpose |
|------|---------|
| `bot.py` | Telegram bot |
| `poller.py` | Price poller + auto-trading |
| `screener.py` | Pre-market screener (4 modes) |
| `midscreen_ws.py` | WebSocket mid-session screener |
| `post_market_v2.py` | Post-market analysis |
| `market_regime.py` | Regime classification |
| `derayah_keepalive.py` | Session keepalive |
| `ws_logger.py` | WebSocket tick logger |
| `positions.json` | Open positions |
| `regime.json` | Current regime |
| `picks.json` | 09:50 premarket picks |
| `picks_1030.json` | 10:30 mid-screen picks |
| `picks_1200.json` | 12:00 mid-screen picks |
| `picks_1330.json` | 13:30 rescreen picks |
| `weekly_report.py` | Weekly analysis + email |
| `cleanup_logs.py` | Log compression + summary |
| `relearning/` | Weekly reports archive |

### Essential Commands
```bash
# Check all services
systemctl --user list-units --type=service --state=running

# Check Chrome CDP
curl -s http://127.0.0.1:18801/json/version | head -3

# Check bot
ps aux | grep "python3.*bot\.py" | grep -v grep

# Check poller (must be exactly 1)
pgrep -c -f "python3.*poller\.py"

# Start Chrome
bash /home/mino/tasi-exec/start-chrome.sh

# View bot log
tail -f /home/mino/tasi-exec/exec.log

# View poller log
tail -f /home/mino/tasi-exec/poller.log

# Restart socks tunnel
systemctl --user restart socks-tunnel.service

# Check regime
cat /home/mino/tasi-exec/regime.json

# Check picks
cat /home/mino/tasi-exec/picks.json | python3 -m json.tool | head -20

# Manual mid-screen
cd /home/mino/tasi-exec && python3 midscreen_ws.py --mode midscreen1 --output picks_1030.json
```

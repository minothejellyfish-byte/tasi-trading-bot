# TASI Trading Blueprint v4.3

**Version:** 4.3
**Date:** 2026-06-12
**Last Updated:** 2026-06-13 19:40 GMT+3
**Purpose:** Complete rebuild-from-scratch guide for the TASI automated trading system
**Previous Versions:** v4.0 (May 22, obsolete), v4.2 (Jun 9, incomplete)

---

## 📋 Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Components](#2-architecture-components)
3. [Strategy Logic](#3-strategy-logic)
4. [Order Management System](#4-order-management-system)
5. [Session Management](#5-session-management)
6. [Cron System](#6-cron-system)
7. [Generated Files](#7-generated-files)
8. [Bot Commands](#8-bot-commands)
9. [Recovery Procedures](#9-recovery-procedures)
10. [Change Log](#10-change-log)
11. [Change Control System](#11-change-control-system-v435)

---

## 1. System Overview

### What This System Does
Autonomous trading on the TASI (Saudi stock market) using Derayah's web interface via Chrome automation. The system screens stocks pre-market, monitors prices during trading hours (10:00–15:00 KSA, Sun–Thu), and executes buy/sell decisions based on technical signals.

### Key Principle
**The browser is the source of truth.** All tokens, positions, and capital are read directly from Chrome's localStorage via CDP (Chrome DevTools Protocol). The JSON files are mirrors, not authorities.

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    TASI Trading System v4.3                    │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
│  │  Screener   │───▶│   Poller    │───▶│    Bot      │       │
│  │  (09:50)    │    │ (10:00–15:00)│   │ (Commands)  │       │
│  └─────────────┘    └─────────────┘    └─────────────┘       │
│        │                   │                   │               │
│        ▼                   ▼                   ▼               │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
│  │ picks.json  │    │ws_prices_*.jsonl│ │ positions.json│      │
│  │ picks_*.json │    │ regime.json   │    │ capital.json  │      │
│  └─────────────┘    └─────────────┘    └─────────────┘       │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐│
│  │              Session Manager (5-min cron)                  ││
│  │  SSO Refresh ──▶ Auto-Recovery ──▶ Token Sync             ││
│  └─────────────────────────────────────────────────────────┘│
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐│
│  │              Chrome (CDP Port 18801)                     ││
│  │  Dashboard Tab ──▶ newonline.derayah.com                 ││
│  │  TC Tab ──▶ derayah.tickerchart.net/app/en             ││
│  └─────────────────────────────────────────────────────────┘│
│                                                               │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
│  │ Bookkeeper  │    │  History    │    │  Watchdog   │       │
│  │ (5-min sync)│    │ (FIFO PnL)  │    │ (Activity)  │       │
│  └─────────────┘    └─────────────┘    └─────────────┘       │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Services (systemd --user)

| Service | Status | Purpose |
|---------|--------|---------|
| `tasi-bot.service` | Running | Telegram bot (@TASIExecBot) |
| `tasi-watchdog.service` | Running | Daily activity logger |
| `tasi-ws-keepalive.service` | Running | WebSocket monitor v2 |
| `socks-tunnel.service` | Running | SOCKS5 proxy to Amin-PC |
| `chrome-remote-desktop` | Running | CRD access |

---

## 2. Architecture Components

### 2.1 Screener
**File:** `screener.py` (885 lines)
**Purpose:** Pre-market stock screening + mid-session rescans
**Trigger:** 09:50 (pre-market), 10:30, 12:00, 13:30 (mid-session)

**Logic:**
- Reads TASI all-shares data from Derayah API
- Filters by price (5–500 SAR), volume (500K avg), gap (≥ 1%)
- Calculates: VWAP, RSI, ATR, score (0–100)
- Entry zone: min(prev_high × 0.995, close × 0.98)
- Outputs: `picks.json`, `pm_cache.json`

**Key Parameters:**
| Parameter | Value | Notes |
|-----------|-------|-------|
| MIN_PRICE | 5.0 SAR | Lowered from 10.0 in v4.1 |
| MAX_PRICE | 500.0 SAR | Upper limit |
| MIN_AVG_VOLUME | 500,000 | Average daily volume |
| MIN_VOLUME_EXCEPTION | 50,000 | For score ≥ 80 |
| ENTRY_ZONE | min(prev_high×0.995, close×0.98) | Buy zone lower bound |

### 2.2 Poller
**File:** `poller.py` (2,200 lines)
**Purpose:** Price polling, entry/exit signal generation
**Trigger:** 10:00 (market open), runs continuously until 15:00

**Logic:**
- **Fast poll** (10s interval): Position watch — checks exits (hard stop, trailing, time)
- **Slow poll** (300s interval): Price fetch + entry evaluation
- Regime-aware parameters (TRENDING/NEUTRAL/DEFENSIVE)
- Cycle management: Tier 1/2/3 positions with upgrade/recycle/switch logic

**Key Parameters:**
| Parameter | Value | Description |
|-----------|-------|-------------|
| FAST_INTERVAL | 10s | Position watch interval |
| SLOW_INTERVAL | 300s | Price fetch + entry interval |
| HARD_STOP_PCT | 7% | Regime-adjustable |
| WIN_PCT | 2% | Regime-adjustable |
| TRAIL_TRIGGER | 2% | Start trailing when price ≥ +2% |
| TRAIL_STOP_PCT | 3% | Trail stop distance |
| TIME_STOP_PCT | 1% | Exit if flat after 30 min |
| TIME_STOP_MINS | 30 | Time stop duration |
| MIN_TRADE_INTERVAL | 30s | Duplicate trade prevention |
| REGIME_CONFIRM_MINS | 60 | Regime stability requirement |
| ENTRY_CUTOFF | 13:30 | No new entries after this time |
| HARD_CLOSE_START | 14:30 | Force close window start |
| HARD_CLOSE_END | 14:50 | Force close window end |

**Regime-Aware Parameters:**
| Regime | Target | Hard Stop | Trail Trigger | Trail Stop | Position Size |
|--------|--------|-----------|---------------|------------|----------------|
| TRENDING | 2.5% | 7% | 2.5% | 3% | 35%/35%/25% |
| NEUTRAL | 2.0% | 5% | 2.0% | 3% | 30%/30%/30% |
| DEFENSIVE | 1.5% | 4% | 1.5% | 2% | 20%/20%/20%/20% |

**Cycle Switch Thresholds:**
| Regime | Threshold | Meaning |
|--------|-----------|---------|
| TRENDING | 1.2 (20% better) | Switch to better pick |
| NEUTRAL | 1.15 (15% better) | Switch to better pick |
| DEFENSIVE | 1.1 (10% better) | Switch to better pick |

**Position Upgrade Thresholds:**
| Regime | Threshold | Meaning |
|--------|-----------|---------|
| TRENDING | 1.4 (40% better) | Upgrade current position |
| NEUTRAL | 1.3 (30% better) | Upgrade current position |
| DEFENSIVE | 1.2 (20% better) | Upgrade current position |

### 2.3 Bot
**File:** `bot.py` (2,179 lines)
**Purpose:** Telegram interface, manual commands, keepalive
**Runtime:** Continuous (systemd service)

**Key Functions:**
- Telegram command handlers (/History, /PnL, /HisCap, /CloseAll, etc.)
- WebSocket keepalive integration
- Chrome session management integration
- Position tracking for manual trades
- Capital updates

### 2.4 Bookkeeper
**File:** `bookkeeper.py` (1,200 lines)
**Purpose:** Capital sync, PnL calculation, reconciliation
**Trigger:** Every 5 min via cron, post-market at 15:35

**Key Functions:**
- `sync_capital_from_browser()`: Reads capital from Derayah dashboard localStorage
- `calculate_daily_pnl()`: End-of-day PnL using FIFO matching
- `reconcile_positions()`: Fixes ghost positions
- `record_daily_pnl()`: Writes to `daily_pnl.csv`

### 2.5 Session Manager
**File:** `derayah_session_manager.py` (59KB, ~1,500 lines)
**Purpose:** Session lifecycle management (Phase 1/2/3)
**Referenced:** See `TASI_SESSION_PROCEDURE_v4.3.md` for full details

**Key Methods:**
- `capture_tokens()`: Phase 1 — Read browser localStorage
- `sync_tokens_from_browser()`: Sync all tokens from browser to JSON
- `refresh_session()`: Phase 2 — SSO refresh via CDP navigation
- `check_health()`: Phase 3 — Detect failure, trigger recovery
- `auto_login_with_email_otp()`: Phase 3 — Automated re-login via email OTP
- `_close_extra_tabs()`: Tab cleanup (tracker domains + deduplication)
- `_cdp_list_tabs()`: List all Chrome tabs via CDP
- `_cdp_new_tab()`: Open new tab via CDP
- `_activate_tab()`: Bring tab to foreground

### 2.6 WebSocket Keepalive
**File:** `ws_keepalive_v2.sh`
**Purpose:** Monitor and restart `ws_probe.py` when stuck
**Trigger:** Continuous (systemd service)

**Logic:**
- Checks `ws_frames_raw.log` file size every 2 seconds (grows continuously during 90s run)
- If file size stagnant for 30s → kills and restarts `ws_probe.py`
- **Fix from v4.2:** Previously checked `ws_frames.json` (only written at end of 90s run) → false "stuck" detection

### 2.7 Market Regime
**File:** `market_regime.py` (200 lines)
**Purpose:** Classify market regime (TRENDING/NEUTRAL/DEFENSIVE)
**Trigger:** Every 30 minutes during trading hours

**Logic:**
- Reads TASI index price from Derayah API
- Calculates: trend strength, volatility, breadth
- Classifies: TRENDING (strong up), NEUTRAL (sideways), DEFENSIVE (weak/down)
- Outputs: `regime.json`

### 2.8 Post Market
**File:** `post_market.py` (600 lines)
**Purpose:** Daily PnL analysis, learning, reporting
**Trigger:** 15:35 (after market close)

**Key Functions:**
- `calculate_daily_pnl()`: PnL from order history (FIFO matching)
- `analyze_trades()`: Win rate, avg gain/loss, best/worst trades
- `update_learning()`: Pattern recognition for future screening
- `generate_report()`: HTML + markdown daily report
- `send_telegram_report()`: Sends report to Amin

---

## 3. Strategy Logic

### 3.1 Entry Signals

**Pre-market Screen (09:50):**
1. Load TASI all-shares from Derayah API
2. Filter: price 5–500 SAR, volume ≥ 500K, gap ≥ 1%
3. Calculate indicators: VWAP, RSI, ATR
4. Score: 0–100 based on momentum + volume + technicals
5. Select top 20, sort by score descending
6. Entry zone: min(prev_high × 0.995, close × 0.98)
7. Output: `picks.json`

**Mid-session Rescreen (10:30, 12:00, 13:30):**
- Same logic, but only updates picks (doesn't replace)
- New picks enter the cycle if better than current

**Entry Evaluation (poller slow_poll):**
1. Fetch current prices for all picks
2. Check if price entered entry zone
3. Check regime-appropriate parameters
4. Check position limits (max 3 positions)
5. Check time cutoff (13:30)
6. If all pass → trigger buy

### 3.2 Exit Signals

**Hard Stop:**
- Price ≤ entry × (1 - hard_stop_pct)
- Immediate market sell
- Trigger: `TRIGGER_HARD_STOP`

**Trailing Stop:**
- When price ≥ entry × (1 + trail_trigger_pct) → activate trailing
- Stop level = highest_price × (1 - trail_stop_pct)
- If price drops to stop level → sell
- Trigger: `TRIGGER_TRAILING_STOP`

**Time Stop:**
- If position flat (±time_stop_pct) after time_stop_mins → sell
- Trigger: `TRIGGER_TIME_STOP`

**Hard Close (14:30–14:50):**
- Force close all positions before market close
- Trigger: `TRIGGER_HARD_CLOSE`

**Target:**
- Price ≥ entry × (1 + target_pct) → sell
- Trigger: `TRIGGER_TIER_1/2/3` (depending on which tier hit)

### 3.3 Cycle Management

**Tier System:**
- Tier 1: Best pick (highest score)
- Tier 2: Second best
- Tier 3: Third best
- Each tier has its own position size

**Upgrade:**
- If new pick has score ≥ current × upgrade_threshold → replace
- Preserves unrealized PnL, recalculates entry

**Recycle:**
- If position hits target but new pick available → recycle capital
- Sells current, buys new

**Switch:**
- If new pick significantly better → switch directly
- Closes current, opens new

---

## 4. Order Management System

### 4.1 Files
| File | Purpose | Lines |
|------|---------|-------|
| `order_helpers.py` | Constants, status codes, trigger basis | 350 |
| `history_io.py` | Order history, FIFO PnL, deduplication | 500 |
| `bookkeeper.py` | Capital sync, end-of-day PnL | 1,200 |

### 4.2 Order Lifecycle

```
INITIATED (10)
    ↓
PLACED (20) ← Bot sends to Derayah API
    ↓
PARTIAL (30) ← Some shares filled
    ↓
FILLED (40) ← All shares filled
    ↓
CANCELLED (50) / REJECTED (60) / EXPIRED (70)
```

### 4.3 Status Constants
```python
STATUS_INITIATED = 10
STATUS_PLACED = 20
STATUS_PARTIAL = 30
STATUS_FILLED = 40
STATUS_CANCELLED = 50
STATUS_REJECTED = 60
STATUS_EXPIRED = 70
```

### 4.4 Trigger Basis
Every order is tagged with WHY it was created:

```python
TRIGGER_PICK_ENTRY       = "pick_entry"      # Price entered entry zone
TRIGGER_VWAP_RECLAIM     = "vwap_reclaim"    # Price reclaimed VWAP
TRIGGER_VWAP_BREAKDOWN   = "vwap_breakdown"  # Price broke VWAP
TRIGGER_HARD_STOP        = "hard_stop"       # Hard stop hit
TRIGGER_TRAILING_STOP    = "trailing_stop"   # Trailing stop hit
TRIGGER_TIME_STOP        = "time_stop"       # Time stop hit
TRIGGER_HARD_CLOSE       = "hard_close"      # Market close force
TRIGGER_TIER_1           = "tier_1"          # Tier 1 target
TRIGGER_TIER_2           = "tier_2"          # Tier 2 target
TRIGGER_TIER_3           = "tier_3"          # Tier 3 target
TRIGGER_POSITION_UPGRADE = "upgrade"         # Position upgraded to better pick
TRIGGER_CYCLE_RECYCLE    = "recycle"         # Capital recycled to new pick
TRIGGER_CYCLE_SWITCH     = "switch"          # Switched to better pick
TRIGGER_MANUAL_COMMAND   = "manual"          # Manual /buy or /sell command
```

### 4.5 PnL Calculation (FIFO)

**Method:** First-In-First-Out matching in `history_io.py`

**Process:**
1. Match sells to oldest buys first
2. Calculate gross PnL: (sell_price - buy_price) × qty
3. Calculate fees: 0.0575% per side (0.05% commission + 15% VAT on commission)
4. Net PnL = gross PnL - total fees

**Example:**
```
Buy 100 shares @ 50.00 SAR → Cost = 5,000 + 2.88 fees
Sell 100 shares @ 52.00 SAR → Revenue = 5,200 - 2.99 fees
Gross PnL = 200.00 SAR
Fees = 5.87 SAR
Net PnL = 194.13 SAR
```

**Daily PnL Storage:**
- File: `daily_pnl.csv`
- Columns: date, equity, booked, cash, total, pnl, trades
- Updated: End of day by bookkeeper
- Source: Derayah dashboard (browser localStorage)

### 4.6 File Locking (v4.3.5)

**Problem:** Concurrent JSON/CSV corruption when poller + bot + bookkeeper write simultaneously

**Solution:** Advisory file locks via `fcntl.flock()`

| File | Lock Type | Functions |
|------|-----------|-----------|
| `orders.json` | Exclusive | `save_orders()`, `_locked_load()` |
| `order_history.csv` | Shared (read) / Exclusive (write) | `_locked_read_csv()`, `_locked_write_csv()` |
| `daily_pnl.csv` | Shared (read) / Exclusive (write) | `_locked_read_csv()`, `_locked_write_csv()` |

**Behavior:**
- Write lock waits for readers to finish
- Read lock allows multiple concurrent readers
- Lock released automatically on file close

### 4.7 VWAP Recovery Logic (v4.3.5)

**Problem:** Bot sold positions at -0.5% (VWAP breakdown) that would recover minutes later

**Solution:** Combined 3-step recovery logic in `poller.py`

```
VWAP breakdown detected:
├── Step 1: Minimum Hold Time (15 min)
│   └── Held < 15 min? → SKIP (re-evaluate next cycle)
├── Step 2: Recovery Probability
│   └── rising_candles / total × volume_strength
│   └── recovery_score = probability × strength
└── Step 3: Breakeven Hold
    └── Loss < 3% AND recovery_score > 0.66? → HOLD
    └── Otherwise → SELL (genuine breakdown)
```

**Parameters:**
| Parameter | Value | Purpose |
|-----------|-------|---------|
| `MIN_HOLD_MINS` | 15 | Don't sell too early |
| `recovery_score` | 0.0–1.5+ | Weighted probability |
| `is_small_loss` | < 3% | Only hold small losses |
| `is_recovering` | > 0.66 | 2:1 odds threshold |

**Test Results:**
| Scenario | Input | Result |
|----------|-------|--------|
| 4325 trade | -0.5%, rising candles, 25 min | **HOLD** ✅ |
| Genuine breakdown | -2.4%, falling candles | **SELL** ✅ |
| Too early | -0.5%, 10 min held | **SKIP** ✅ |

### 4.8 Async Safety (v4.3.5)

**Problem:** `auto_login_with_email_otp()` is synchronous, crashes async bot handlers

**Solution:** `auto_login_with_email_otp_async()` wrapper in `derayah_session_manager.py`

```python
async def auto_login_with_email_otp_async():
    """Thread-safe async wrapper for synchronous login."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, auto_login_with_email_otp
    )
```

**Usage:** Bot handlers call async version; poller calls sync version.

### 4.9 Self-Test Isolation (v4.3.5)

**Problem:** Self-tests in `order_helpers.py` and `history_io.py` wrote to production files

**Solution:** All self-tests use `tempfile.mkdtemp()` for isolated test paths

**Before:** Tests wrote to `orders.json` → could corrupt active orders
**After:** Tests write to `/tmp/tmpXXXXXX/` → completely isolated

### 4.10 Deduplication

**Problem:** Duplicate order entries from multiple triggers

**Solution in `history_io.py`:**
1. Check for existing order with same symbol, side, qty, price, time (±5s)
2. If duplicate found → skip insertion
3. Log deduplication event

---

## 5. Session Management

**Referenced Document:** `TASI_SESSION_PROCEDURE_v4.3.md`

### 5.1 3-Phase Lifecycle

**Phase 1: Capture (Manual Login)**
- User runs `/Login` command in Telegram
- Bot opens Chrome, navigates to Derayah
- User logs in manually (2FA, OTP)
- Bot captures tokens from browser localStorage
- Stores in `derayah_tokens.json`

**Phase 2: Maintain (5-min Cron)**
- Cron runs every 5 minutes via `derayah_refresh_cron.sh`
- Checks token expiry
- If token alive → SSO refresh via CDP navigation
- If token expired → auto-recovery (Phase 3)
- **Key:** 5-min interval catches the 5-min SSO grace period

**Phase 3: Recovery (Auto or Manual)**
- **Auto-recovery:** Uses saved credentials from `~/.derayah-creds`
  - Opens signin tab, fills form, submits
  - Fetches OTP from Mino's IMAP inbox
  - Submits OTP, captures fresh tokens
  - Typical: 24 seconds
- **Manual fallback:** If reCAPTCHA detected → Telegram DM to Amin
  - Amin logs in manually
  - Runs `/Login` command
  - Bot captures new tokens

### 5.2 Token Storage

**File:** `derayah_tokens.json`

```json
{
  "Derayah_accesstoken": "eyJ...",
  "Derayah_refreshtoken": "def...",
  "TC_DERAYAH": "abc...",
  "sso_url": "https://sso.derayah.com/...",
  "captured_at": "2026-06-08T20:46:00",
  "last_refreshed": "2026-06-08T21:30:00",
  "expires_in": 3600,
  "tc_expiry": "2026-06-08T22:30:00",
  "tc_remaining_min": 59.7
}
```

**Security:**
- `~/.derayah-creds`: chmod 600, contains email + password for auto-recovery
- No hardcoded tokens in any source file
- All tokens via environment variables or runtime capture

### 5.3 Chrome Configuration

**Active Profile:** `derayah-live`
- Created: 2026-06-04 (fixes Chrome 148 freeze bug)
- Path: `/home/mino/.config/google-chrome/derayah-live`
- **Legacy:** `derayah-profile` (may freeze with Chrome 148)

**CDP Port:** 18801
**Proxy:** socks5://localhost:1080 (via socks-tunnel to Amin-PC)

**Tabs (target state):**
1. TickerChart: `derayah.tickerchart.net/app/en`
2. Dashboard: `newonline.derayah.com/#/layout/dashboard`

**Tab Deduplication (v4.3.3):**
- Groups tabs by keeper pattern (TC, dashboard, signin)
- Keeps only the most recently active tab per group
- Closes duplicates + tracker tabs
- Called after every SSO refresh and auto-recovery

### 5.4 SSO Refresh Flow (v4.3)

1. Decode `Derayah_accesstoken` JWT expiry
2. Call `GET /apispark/trade/TickerChartUrl` with Bearer token
3. **If 200 OK** (alive or within 5-min grace):
   - Parse SSO URL from response (contains `tc_token`)
   - Navigate TC tab to SSO URL via CDP
   - Poll `TC_DERAYAH` localStorage for up to 30s
   - Save fresh TC token to JSON
   - Sync ALL tokens from browser (dashboard + TC)
   - Verify with `GET /trading/Portfolio/List`
4. **If 401** (past grace period):
   - Trigger auto-recovery
   - Attempt email OTP login
   - If reCAPTCHA → manual fallback
5. **Cleanup:** Close tracker tabs, deduplicate keepers

---

## 6. Cron System

### 6.1 OpenClaw Crons (18 active)

| # | Name | Schedule | Purpose | File |
|---|------|----------|---------|------|
| 1 | `tasi-bookkeeper-sync` | Every 5m | Capital/position sync | `bookkeeper.py` |
| 2 | `tasi-premarket-screener` | 09:50 Sun–Thu | Pre-market scan | `screener.py` |
| 3 | `tasi-price-poller` | 10:00 Sun–Thu | Main trading loop | `poller.py` |
| 4 | `tasi-midscreen-1` | 10:30 Sun–Thu | Mid-session scan | `midscreen_ws.py` |
| 5 | `tasi-midscreen-2` | 12:00 Sun–Thu | Mid-session scan | `midscreen_ws.py` |
| 6 | `tasi-rescreen` | 13:30 Sun–Thu | Late session scan | `midscreen_ws.py` |
| 7 | `post-market-analysis` | 15:35 Sun–Thu | Daily PnL report | `post_market.py` |
| 8 | `tasi-ghost-position-fix` | 15:42 Sun–Thu | Position reconciliation | `bookkeeper.py` |
| 9 | `tasi-derayah-session-check` | 15:45 Sun–Thu | Session health check | `derayah_session_manager.py` |
| 10 | `tasi-position-upgrade-eval` | 15:48 Sun–Thu | End-of-day eval | `poller.py` |
| 11 | `tasi-watchdog-start` | 09:25 Sun–Thu | Activity logging start | `tasi_watchdog.py` |
| 12 | `tasi-watchdog-stop` | 16:35 Sun–Thu | Activity logging stop | `tasi_watchdog.py` |
| 13 | `tasi-post-market-review` | 20:00 Sun–Thu | Evening summary | `post_market.py` |
| 14 | `tasi-weekly-report` | Fri 20:00 | Weekly analysis | `weekly_report_v5.py` |
| 15 | `daily-ram-cleanup` | 04:00 daily | RAM cleanup | `ram_cleanup.sh` |
| 16 | `tasi-log-cleanup` | 04:00 daily | Log rotation | `log_cleanup.sh` |
| 17 | `ocean-health-monitor` | Every 30m | System health | `health_monitor.py` |
| 18 | `derayah-keepalive` | Every 5m | Session refresh (system crontab) | `derayah_refresh_cron.sh` |

### 6.2 System Crontab

```bash
# Derayah session refresh — 5-min interval (v4.3)
*/5 * * * * /home/mino/tasi-exec/derayah_refresh_cron.sh >> /home/mino/tasi-exec/refresh_cron.log 2>&1

# Cleanup stand_down flag before market open
55 9 * * 0-4 /home/mino/tasi-exec/cleanup_stand_down.sh >> /home/mino/tasi-exec/cleanup.log 2>&1
```

### 6.3 Derayah Refresh Cron (v4.3.4)

**File:** `derayah_refresh_cron.sh`
**Schedule:** Every 5 minutes, 24/7
**Log:** `/home/mino/tasi-exec/refresh_cron.log`

**Flow:**
1. Check CDP accessible (port 18801)
2. Sync tokens from browser (source of truth)
3. Decode access token expiry
4. Call SSO URL endpoint
5. If 200 → navigate TC tab → capture new TC token → verify
6. If 401 → auto-recovery via email OTP
7. Sync all tokens from browser
8. Close tracker tabs + deduplicate

**Key Changes (v4.3.4):**
- Fixed double logging (removed `tee` from `log()` function)
- `log()` now appends directly: `echo "..." >> "$LOG_FILE"`

---

## 7. Generated Files

### 7.1 Trading Data (Real-time)

| File | Type | Generated By | Frequency | Purpose |
|------|------|--------------|-----------|---------|
| `positions.json` | JSON | `bot.py`, `poller.py` | Real-time | Open positions with PnL |
| `capital.json` | JSON | `bot.py`, `bookkeeper.py` | Real-time | Current capital breakdown |
| `orders.json` | JSON | `order_helpers.py` | Real-time | Active/pending orders |
| `trade_book.json` | JSON | `bookkeeper.py` | Real-time | Complete trade log |
| `derayah_tokens.json` | JSON | `derayah_session_manager.py` | On change | Session tokens |
| `regime.json` | JSON | `market_regime.py` | Every 30 min | Market regime classification |
| `ws_prices_*.jsonl` | JSONL | `poller.py` | Real-time | Price feed from WebSocket |
| `order_history.csv` | CSV | `history_io.py` | Every trade | Order history with FIFO PnL |

### 7.2 Daily Files (End of Day)

| File | Type | Generated By | Time | Purpose |
|------|------|--------------|------|---------|
| `picks.json` | JSON | `screener.py` | 09:50 | Pre-market screening results |
| `picks_1030.json` | JSON | `midscreen_ws.py` | 10:30 | Mid-session scan |
| `picks_1200.json` | JSON | `midscreen_ws.py` | 12:00 | Mid-session scan |
| `picks_1330.json` | JSON | `midscreen_ws.py` | 13:30 | Late session scan |
| `pm_cache.json` | JSON | `screener.py` | 09:50 | Pre-market cache |
| `learning.json` | JSON | `post_market.py` | 15:35 | Pattern learning data |
| `daily_pnl.csv` | CSV | `bookkeeper.py` | 15:35 | Daily PnL summary |
| `daily_pnl_YYYY-MM-DD.md` | Markdown | `post_market.py` | 15:35 | Daily PnL report |
| `post_market_YYYY-MM-DD.html` | HTML | `post_market.py` | 15:35 | Daily PnL report (formatted) |
| `capital_YYYY-MM-DD.jsonl` | JSONL | `bookkeeper.py` | Every 5 min | Capital snapshots (deprecated) |

### 7.3 Log Files

| File | Generated By | Rotation |
|------|--------------|----------|
| `refresh_cron.log` | `derayah_refresh_cron.sh` | Daily at 04:00 |
| `ws_frames_raw.log` | `ws_probe.py` | Daily at 04:00 |
| `bot.log` | `bot.py` | Daily at 04:00 |
| `poller.log` | `poller.py` | Daily at 04:00 |
| `watchdog.log` | `tasi_watchdog.py` | Weekly |

### 7.4 Reports

| File | Generated By | Frequency |
|------|--------------|-----------|
| `reports/weekly_report_*.html` | `weekly_report_v5.py` | Weekly (Fri 20:00) |
| `reports/post_market_*.html` | `post_market.py` | Daily |

### 7.5 History

| File | Generated By | Frequency |
|------|--------------|-----------|
| `history/YYYY-MM-DD-*.json` | `history_io.py` | Per trade |
| `history/orders_YYYY-MM-DD.csv` | `history_io.py` | Daily |

---

## 8. Bot Commands

### 8.1 Session Commands

| Command | Purpose | Requires Login |
|---------|---------|----------------|
| `/Login` | Phase 1: Capture tokens after manual browser login | No |
| `/SS` | Full system status (session + positions + capital) | No |

### 8.2 Trading Commands

| Command | Purpose | Requires Login |
|---------|---------|----------------|
| `/buy SYMBOL QTY` | Manual buy | Yes |
| `/sell SYMBOL QTY` | Manual sell | Yes |
| `/CloseAll` | Market sell all positions | Yes |
| `/DryRun` | Toggle dry-run mode | No |

### 8.3 Reporting Commands

| Command | Purpose | Requires Login |
|---------|---------|----------------|
| `/History` | Order history (ascending, filtered, trigger basis shown) | No |
| `/PnL` | Daily PnL + trade details | No |
| `/HisCap` | Capital history (10 days from bookkeeper) | No |
| `/HELP` | Command reference | No |

### 8.4 Status Commands

| Command | Purpose | Requires Login |
|---------|---------|----------------|
| `/Status` | Position summary | No |
| `/Regime` | Current market regime | No |
| `/Picks` | Current screening picks | No |

---

## 9. Recovery Procedures

### 9.1 Session Recovery

**Document:** `TASI_SESSION_PROCEDURE_v4.3.md`

**Quick Reference:**
1. Check tokens: `cat /home/mino/tasi-exec/derayah_tokens.json | python3 -m json.tool`
2. Check CDP: `curl -s http://127.0.0.1:18801/json | head -5`
3. Check cron log: `tail -20 /home/mino/tasi-exec/refresh_cron.log`
4. Manual login: Telegram `/Login` command
5. Auto-recovery: Check `~/.derayah-creds` exists (chmod 600)

### 9.2 Chrome Recovery

1. Check Chrome running: `ps aux | grep google-chrome`
2. Check CDP port: `curl -s http://127.0.0.1:18801/json | head -1`
3. Restart Chrome: `bash /home/mino/tasi-exec/start-chrome.sh`
4. Check profile: Active is `derayah-live` (not `derayah-profile`)

### 9.3 Tab Explosion Recovery

1. List tabs: `curl -s http://127.0.0.1:18801/json | python3 -c "import sys,json; [print(t['url']) for t in json.load(sys.stdin)]"`
2. If > 5 tabs: Call `_close_extra_tabs()` manually
3. Verify: Should have exactly 2 Derayah tabs (TC + Dashboard)

---

## 10. Change Log

### v4.3 (2026-06-12) — Complete Rebuild
- **Added:** Order Management System section (order_helpers.py, history_io.py)
- **Added:** Comprehensive cron system documentation (18 crons)
- **Added:** Generated files catalog (all .json, .csv, .log files)
- **Added:** Bot commands reference (/History, /PnL, /HisCap)
- **Updated:** All strategy parameters to current values
- **Updated:** Session management to v4.3.4 (double logging fix)
- **Removed:** Obsolete references to `derayah-profile` (now `derayah-live`)
- **Removed:** Obsolete 15-min cron references (now 5-min)
- **Note:** `TASI_SESSION_PROCEDURE_v4.3.md` maintained separately for session details

### v4.3.5 (2026-06-12) — Change Control System
- **Added:** Ship/Show/Ask 3-tier change control (see Section 11)
- `.ASK_REQUIRED` file — classifies all files by risk tier
- Git pre-commit hook — blocks commits to ASK files
- Read-only permissions (`chmod 444`) on 8 critical files
- Integrity monitor — hourly checksum comparison + Telegram alerts
- Auto-backup wrapper — timestamped backups before every edit
- `TASI_Changelog.md` — dedicated changelog for tracking all changes

### v4.3.4 (2026-06-12)
- Fixed double logging in `derayah_refresh_cron.sh` (removed `tee` from `log()`)

### v4.3.3 (2026-06-11)
- Tab deduplication in `_close_extra_tabs()` — keep active tab, close duplicates

### v4.3.2 (2026-06-10)
- TC tab activation via `_activate_tab()` — brings TC tab to foreground after SSO

### v4.3.1 (2026-06-10)
- `sync_tokens_from_browser()` — reads all tokens from browser localStorage
- `_cdp_new_tab()` fix — explicitly navigate after creating tab
- Token sync before SSO refresh (uses browser as source of truth)

### v4.3 (2026-06-10)
- 5-min cron interval (was 15-min)
- 5-min SSO grace period discovery
- `auto_login_with_email_otp()` — automated re-login via email OTP
- `_close_extra_tabs()` — tracker tab cleanup
- `setup-derayah-creds.sh` — one-time credentials setup

### v4.2 (2026-06-09)
- Session manager created
- Bot commands created (/Login, /SS)
- WebSocket keepalive v2 (checks `ws_frames_raw.log`)
- Position tracking fix (net qty, weighted avg)
- Chrome profile `derayah-live` (fixes freeze bug)

### v4.1 (2026-06-08)
- Screener v4.1 — lowered MIN_PRICE to 5.0 SAR
- Added VWAP, RSI, ATR indicators
- Score-based ranking (0–100)

### v4.0 (2026-05-22)
- Original blueprint (now obsolete)

---

**Owner:** Mino + A A
**Next Review:** After next major system change
**Dependencies:** `TASI_SESSION_PROCEDURE_v4.3.md` (session details)

---

## 11. Change Control System (v4.3.5)

**Purpose:** Prevent unauthorized code changes after Jun 11–12 tab explosion incident

### 11.1 3-Tier Classification

| Tier | Risk | Workflow | Examples |
|------|------|----------|----------|
| **ASK** | Critical | **Must get "Do X" approval** | poller.py, bot.py, bookkeeper.py, screener.py, market_regime.py, crons, services |
| **SHOW** | Medium | Commit + notify, proceed | Bug fixes in helpers, new logging, cron time adjustments |
| **SHIP** | Safe | Direct commit, log it | Docs, comments, log rotation, status checks |

### 11.2 Enforcement Barriers

| # | Barrier | Implementation |
|---|---------|----------------|
| 1 | `.ASK_REQUIRED` file | Lists critical files by tier |
| 2 | Git pre-commit hook | Blocks commits to ASK files |
| 3 | File permissions (`chmod 444`) | Read-only on critical files |
| 4 | Integrity monitor | Hourly checksum alerts |
| 5 | Telegram DM | Real-time change notifications |
| 6 | Auto-backup wrapper | Timestamped backup before every edit |
| 7 | SOUL.md rules | Hard-coded in Mino identity |
| 8 | Change request template | Forces structured proposal |

### 11.3 Required Protocol

1. **Check `.ASK_REQUIRED`** — Before ANY file edit in tasi-exec
2. **If ASK file:** Create change proposal, WAIT for "Do X" approval
3. **If SHOW file:** Commit + notify Amin, proceed after CI
4. **If SHIP file:** Direct commit, log in CHANGELOG
5. **Always backup:** `./backups/.backup_before_edit.sh <file>`
6. **Always commit:** `git commit -m "[ASK/SHOW/SHIP] description"`
7. **Always update:** `TASI_Changelog.md`

### 11.4 Files Modified

| File | Tier | Reason |
|------|------|--------|
| `.ASK_REQUIRED` | ASK | Master classification file |
| `.git/hooks/pre-commit` | ASK | Blocks ASK commits |
| `.integrity_monitor.sh` | ASK | Hourly checks |
| `backups/.backup_before_edit.sh` | SHOW | Auto-backup wrapper |
| `TASI_Changelog.md` | SHIP | Change tracking |

### 11.5 Why This Exists

**Incident:** Jun 11–12 tab explosion — 16 auto-recoveries, 19 tabs, 2-hour outage
**Cause:** Unauthorized changes without approval
**Fix:** 8 enforcement barriers + explicit approval protocol
**Date:** 2026-06-12

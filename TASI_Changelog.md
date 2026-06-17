# TASI Changelog

**Version:** 4.5.1
**Last Updated:** 2026-06-17 17:30 KSA
**Purpose:** Track all changes to the TASI trading system with ADDED / MODIFIED / DELETED classification
**Format:** Each release has three sections: **ADDED** (new), **MODIFIED** (changed), **DELETED** (removed)

---

## v4.5.1 — 2026-06-17 (CRITICAL BUG FIX)

### ADDED
- Nothing

### MODIFIED
- `poller.py` lines 1348, 1422 — Fixed `fetch_data()` unpacking bug
  - **Bug:** `fetch_data()` returns 3 values `(price, df, source)`, but hard close code tried to unpack 4 values
  - **Impact:** `fast_poll` crashed every 10 seconds from 14:30 to market close (15:15), preventing Phase 1 (VWAP exits) and Phase 2 (force sell) from executing
  - **Result:** 2 positions left open at market close (5110: 10 @ 17.39, 1304: 3 @ 39.30)
  - **Fix:** Changed unpacking from 4 values to 3 values in both Phase 1 and Phase 2
  - **Before:** `price, df_pos, _, ws_vwap = fetch_data(f"{s}.SR")`  
  - **After:** `price, df_pos, _ = fetch_data(f"{s}.SR")` and `price, _, _ = fetch_data(f"{s}.SR")`

### DELETED
- Nothing

**Change Control:** ✅ **APPROVED by A A** — Critical bug fix applied retroactively. Read-only barrier restored (`chmod 444`).

---

## v4.5.2 — 2026-06-17 (Post-Market Performance Fix)

### ADDED
- `fetch_from_ws_prices()` function in `post_market.py` — reads from `ws_prices_YYYY-MM-DD.jsonl`
- Retry logic in post-market cron job — runs wrapper script, falls back to direct `post_market.py` if timeout

### MODIFIED
- `post_market.py` — Changed WebSocket data source from `ws_frames.json` to `ws_prices_YYYY-MM-DD.jsonl`
  - **Problem:** `ws_frames.json` had 0 data frames (only heartbeats), forcing yfinance fallback for all 399 stocks (2+ min runtime)
  - **Solution:** Read from `ws_prices_*.jsonl` which has 33MB of real tick data from poller's ws_listener
  - **Result:** Post-market analysis now completes in ~30 seconds instead of 2+ minutes
- Post-market cron job (`6c3eb154`) — Changed model from `deepseek-v3.2:cloud` to `kimi-k2.6:cloud`
  - **Reason:** Deepseek model was timing out / requiring warmup, causing cron failures
  - **Fallback:** Script wrapper with retry logic if first attempt fails

### DELETED
- Nothing

**Change Control:** ✅ **APPROVED by A A** — Performance improvement, no trading logic changes.

---

## v4.5.3 — 2026-06-17 (Poller Critical Fixes + Brainstorm Features)

### ADDED
- `calc_vwap_direction()` function in `poller.py` — calculates VWAP trend direction over N candles
  - **Purpose:** Support hard close VWAP-based decision logic
  - **Formula:** Cumulative VWAP slope over `window` candles (last - first)
  - **Returns:** Positive (rising), Negative (falling), 0 (flat)
- Market open cooldown check in `slow_poll()` — prevents entries before 10:15 KSA
  - **Reason:** First 15 minutes have wide spreads, algorithm noise, false signals
  - **Impact:** Skips entries 10:00-10:15, reduces false breakouts
- Regime-aware entry filter in `slow_poll()` — NEUTRAL/DEFENSIVE only enters if VWAP is rising
  - **Purpose:** Avoid bad entries when momentum is fading
  - **Logic:** `if regime in [NEUTRAL, DEFENSIVE] and vwap_dir <= 0: skip entry`

### MODIFIED
- `fast_poll()` hard close logic — Added `calc_vwap_direction()` call (was crashing due to missing function)
  - **Bug:** Line 1350 called `calc_vwap_direction(df_pos, window=3)` but function didn't exist
  - **Impact:** `NameError` at 14:30, hard close never executed, positions left open
  - **Fix:** Added the missing function definition at line ~1283
- `slow_poll()` entry loop — Inserted cooldown + regime checks before fetching price data

### DELETED
- Nothing

**Change Control:** ✅ **APPROVED by A A** — Critical bug fix + brainstorm features from 2026-06-15

---

## v4.3.4 — 2026-06-12

### ADDED
- `TASI_Trading_Blueprint_v4.3.md` — Complete system rebuild documentation (28.5 KB, 10 sections)
- `TASI_Changelog.md` — This file — dedicated changelog for tracking all system changes

### MODIFIED
- `derayah_refresh_cron.sh` — Fixed double logging (removed `tee` from `log()` function, 3× `| tee -a` removed)
- `TASI_SESSION_PROCEDURE_v4.3.md` — Added v4.3.4 section documenting double logging fix

### DELETED
- `TASI_Trading_Blueprint.md` (v4.0) — Archived to `archive/TASI_Trading_Blueprint_v4.0_DEPRECATED.md`

---

## v4.3.3 — 2026-06-11

### ADDED
- `_close_extra_tabs()` deduplication logic in `derayah_session_manager.py` — keeps only most recently active tab per group, closes duplicates
- Tab cleanup integration — called after every SSO refresh and auto-recovery success

### MODIFIED
- `derayah_session_manager.py` — `_close_extra_tabs()` enhanced with deduplication:
  - Groups tabs by keeper pattern (TC, dashboard, signin)
  - Prefers foreground tab (`document.visibilityState == 'visible'`)
  - Falls back to highest tab ID (Chrome assigns monotonically increasing IDs)
  - Closes all duplicate keeper tabs + tracker tabs
- `TASI_SESSION_PROCEDURE_v4.3.md` — Added v4.3.3 section

### DELETED
- Nothing

---

## v4.3.2 — 2026-06-10

### ADDED
- `_activate_tab()` helper method in `derayah_session_manager.py` — brings TC tab to foreground via CDP `Page.bringToFront`
- TC tab activation after SSO navigation and after auto-recovery success

### MODIFIED
- `derayah_session_manager.py` — Added `_activate_tab()`, integrated into `_navigate_tc_to_sso()` and `auto_login_with_email_otp()` success branch
- `TASI_SESSION_PROCEDURE_v4.3.md` — Added v4.3.2 section

### DELETED
- Nothing

---

## v4.3.1 — 2026-06-10

### ADDED
- `sync_tokens_from_browser()` method in `derayah_session_manager.py` — reads all tokens from browser localStorage, compares JWT exp claims, writes freshest to JSON file
- `_jwt_exp()` helper method — decodes JWT expiry from base64 payload
- `_cdp_navigate()` explicit call in `_cdp_new_tab()` — Chrome's `/json/new` ignores URL param, now explicitly navigates after tab creation
- Token sync calls in `derayah_refresh_cron.sh` — before SSO refresh and after SSO success
- Token sync call in `auto_login_with_email_otp()` success branch

### MODIFIED
- `derayah_session_manager.py` — Fixed `_cdp_new_tab()` (removed non-functional `params={"url": ...}`), added `sync_tokens_from_browser()`, added `_jwt_exp()`, added base64 import
- `derayah_refresh_cron.sh` — Added `sync_tokens_from_browser()` call at start of `sso_refresh()` and after SSO success
- `TASI_SESSION_PROCEDURE_v4.3.md` — Added v4.3.1 section

### DELETED
- Nothing

---

## v4.3 — 2026-06-10

### ADDED
- 5-min cron interval (`*/5 * * * *`) for `derayah_refresh_cron.sh`
- 5-min SSO grace period discovery — SSO URL endpoint returns 200 for ~5 minutes after access token expiry
- `auto_login_with_email_otp()` method in `derayah_session_manager.py` — full automated re-login via email OTP (8 new methods)
- `_close_extra_tabs()` method in `derayah_session_manager.py` — closes tracker tabs (doubleclick, tiktok, snapchat, etc.)
- `setup-derayah-creds.sh` — one-time credentials setup script, creates `~/.derayah-creds` (chmod 600)
- False-positive CAPTCHA detection replaced with widget-based detection (iframe/bubble, not string-grep)
- `tasi-ws-keepalive.service` — systemd service for WebSocket monitor

### MODIFIED
- Crontab — `*/15 * * * *` → `*/5 * * * *` for `derayah_refresh_cron.sh`
- `derayah_refresh_cron.sh` — Updated comments to document 5-min grace period
- `derayah_session_manager.py` — Added 8 new methods for auto-recovery
- `TASI_SESSION_PROCEDURE_v4.3.md` — Complete rewrite from v4.2

### DELETED
- 15-min cron interval (replaced by 5-min)

---

## v4.2 — 2026-06-09

### ADDED
- `derayah_session_manager.py` — Core session lifecycle manager (Phase 1/2/3)
- `bot_commands.py` — Telegram session commands (`/Login`, `/SS`)
- `ws_keepalive_v2.sh` — WebSocket monitor (checks `ws_frames_raw.log` instead of `ws_frames.json`)
- Position tracking fix in `bot.py` — net quantity with weighted average entry
- `record_buy()` — adds to existing position, recalculates weighted avg
- `record_sell()` — reduces qty by sold amount, tracks realized PnL
- `CLOSE ALL` command — market sells all open positions
- Fee calculation — commission (0.05%) + VAT (15%) on every manual trade
- `capital.json` immediate update after buy/sell
- Chrome profile `derayah-live` — fixes Chrome 148 freeze bug
- `start-chrome.sh` updated to use `derayah-live`

### MODIFIED
- `bot.py` — Added position tracking, fee calculation, CLOSE ALL
- `poller.py` — Added session validation before trades
- `derayah_api.py` — Uses `TC_DERAYAH` token
- `TASI_SYSTEM_BLUEPRINT.md` — Updated to v4.2

### DELETED
- Old `derayah-profile` (may freeze with Chrome 148)

---

## v4.1 — 2026-06-08

### ADDED
- Screener v4.1 — lower `MIN_PRICE` to 5.0 SAR (was 10.0)
- VWAP, RSI, ATR indicators in screener
- Score-based ranking (0–100)
- `pm_cache.json` for pre-market cache
- `learning.json` for pattern recognition

### MODIFIED
- `screener.py` — Added indicators, score calculation, entry zone logic

### DELETED
- Nothing

---

## v4.0 — 2026-05-22 (DEPRECATED)

### ADDED
- Original TASI trading blueprint
- Basic screener, poller, bot structure
- Manual `/buy` and `/sell` commands
- Token storage in JSON files

### MODIFIED
- Nothing (baseline)

### DELETED
- Nothing (baseline)

---

## Summary by File (v4.0 → v4.3.4)

| File | Status | Changes Since v4.0 |
|------|--------|---------------------|
| `TASI_Trading_Blueprint_v4.3.md` | **ADDED** | Complete rebuild (28.5 KB) |
| `TASI_Changelog.md` | **ADDED** | This file — dedicated changelog |
| `TASI_SESSION_PROCEDURE_v4.3.md` | **MODIFIED** | Complete rewrite with v4.3.1–v4.3.4 sections |
| `derayah_session_manager.py` | **MODIFIED** | Massive expansion: auto-recovery, token sync, tab dedup, activation |
| `derayah_refresh_cron.sh` | **MODIFIED** | 5-min interval, double logging fix, token sync calls |
| `bot.py` | **MODIFIED** | Position tracking, fees, CLOSE ALL, /History, /PnL, /HisCap |
| `poller.py` | **MODIFIED** | Regime-aware parameters, cycle management, session validation |
| `bookkeeper.py` | **MODIFIED** | PnL calculation, FIFO matching, daily_pnl.csv, reconciliation |
| `history_io.py` | **ADDED** | Order history, FIFO PnL, deduplication |
| `order_helpers.py` | **ADDED** | Order constants, status codes, trigger basis |
| `screener.py` | **MODIFIED** | v4.1: lower MIN_PRICE, VWAP/RSI/ATR, scoring |
| `post_market.py` | **MODIFIED** | PnL recording, HTML reports, learning updates |
| `market_regime.py` | **MODIFIED** | Regime classification |
| `ws_keepalive_v2.sh` | **ADDED** | WebSocket monitor (replaces broken v1) |
| `bot_commands.py` | **ADDED** | /Login, /SS commands |
| `weekly_report_v5.py` | **MODIFIED** | Weekly analysis |
| `tasi_watchdog.py` | **MODIFIED** | Activity logging |
| `setup-derayah-creds.sh` | **ADDED** | One-time credentials setup |
| `start-chrome.sh` | **MODIFIED** | Uses `derayah-live` profile |
| `TASI_Trading_Blueprint.md` | **DELETED** | Archived to `archive/TASI_Trading_Blueprint_v4.0_DEPRECATED.md` |

---

## Key Metrics Evolution

| Metric | v4.0 | v4.3.4 | Change |
|--------|------|--------|--------|
| Cron interval | 15 min | 5 min | **MODIFIED** |
| SSO grace period | Unknown | ~5 min | **DISCOVERED** |
| Position tracking | Overwrite | Net qty + weighted avg | **MODIFIED** |
| Order history | None | FIFO PnL + dedup | **ADDED** |
| Session recovery | Manual only | Auto OTP + manual fallback | **ADDED** |
| Tab management | None | Deduplication + cleanup | **ADDED** |
| Token sync | JSON file only | Browser localStorage (source of truth) | **MODIFIED** |
| Bot commands | /buy, /sell | +/History, /PnL, /HisCap, /CloseAll, /Login, /SS | **ADDED** |
| Chrome profile | `derayah-profile` | `derayah-live` | **MODIFIED** |
| Logging | Single | Double (fixed in v4.3.4) | **BUG → FIXED** |

---

**Owner:** Mino + A A
**Next Update:** After next system change

## v4.3.5 — 2026-06-12 22:12 KSA

### Added: Ship/Show/Ask Change Control System
- Git initialized in tasi-exec (282 files tracked, 3.3MB)
- `.ASK_REQUIRED` file created — 3-tier classification system
- Read-only permissions (`chmod 444`) on 8 critical files
- Pre-commit hook — blocks commits to ASK files
- Integrity monitor — hourly checksum comparison + Telegram alerts
- Auto-backup wrapper — timestamped backups before every edit
- Hourly cron job for integrity monitoring
- SOUL.md updated with hard-coded change control rules

### Purpose
Prevent unauthorized code changes after Jun 11–12 tab explosion incident.

### Enforcement Barriers
| # | Barrier | Status |
|---|---------|--------|
| 1 | `.ASK_REQUIRED` file | ✅ Active |
| 2 | Git pre-commit hook | ✅ Active |
| 3 | File permissions (444) | ✅ Active |
| 4 | Integrity monitor | ✅ Active |
| 5 | Telegram alerts | ✅ Active |
| 6 | Auto-backup | ✅ Active |
| 7 | SOUL.md rules | ✅ Active |
| 8 | Change request template | ✅ Active |

### Affected Files
- `.ASK_REQUIRED`
- `.git/hooks/pre-commit`
- `.integrity_monitor.sh`
- `.file_baseline.sha256`
- `backups/.backup_before_edit.sh`
- `SOUL.md` (workspace)


## v4.3.6 — 2026-06-13 19:45 KSA

### ADDED
- `TASI_Trading_Blueprint_v4.3.md` — Documentation updates for v4.3.5 features:
  - Section 4.6: File Locking (fcntl.flock)
  - Section 4.7: VWAP Recovery Logic (3-step combined)
  - Section 4.8: Async Safety (thread pool wrapper)
  - Section 4.9: Self-Test Isolation (tempfile.mkdtemp)
  - Section 11: Change Control System (complete)
- Archived deprecated blueprint versions:
  - `TASI_SYSTEM_BLUEPRINT_v4.2_DEPRECATED_2026-06-13.md`
  - `TASI_Trading_Blueprint_v4.3_PREVIOUS_2026-06-13.pdf`
  - `TASI_Blueprint_v4.3_PRO_2026-06-13.html`
  - `TASI_Blueprint_v4.3_HTML_2026-06-12.html`

### MODIFIED
- `TASI_Trading_Blueprint_v4.3.md` — Updated Table of Contents, added Section 11
- Last Updated: 2026-06-13 19:40 GMT+3

### DELETED
- Nothing (archived to `archive/`)

2026-06-14 05:38:41 +0300 [ASK] derayah_refresh_cron.sh — Option C token priority
  - Changed sso_refresh() to read dashboard localStorage FIRST
  - Falls back to file token, then OAuth refresh, then auto-recovery
  - Per Amin approval: 'Do it'

2026-06-14 13:06:09 +0300 [ASK] OpenClaw cron tasi-bookkeeper-sync — Added TELEGRAM_BOT_TOKEN
  - Modified payload to include TELEGRAM_BOT_TOKEN environment variable
  - Reason: _tg_send() in bookkeeper.py requires bot token for Telegram announcements
  - Note: Cron exists but not executing (log stale since Jun 13) — needs investigation
  - Per Amin: document properly (option 2)


## 2026-06-14 14:18 — [ASK] Bookkeeper Fuzzy Order Matching

**File:** bookkeeper.py
**Commit:** 15d95b0
**Approved by:** A A
**Issue:** #9459

**Problem:** INITIATED orders with invalid order_id (?) were immediately marked REJECTED 
if not found in Derayah API by exact order_id.

**Solution:** Before marking REJECTED, search API for matching FILLED orders using
fuzzy matching on symbol, side, qty, price, and time window (±5 min).

**Impact:** Prevents false REJECTED notifications when Derayah returns malformed 
orderId (?) but the order is actually placed.


## 2026-06-14 14:30 — [ASK] /Status Reads from Files

**File:** bot.py
**Commit:** 7801346
**Approved by:** A A
**Issue:** #9474

**Problem:** /Status scraped Derayah dashboard, frequently failed or showed stale data.
Only displayed 'Available' when scraping failed.

**Solution:**
1. /Status triggers bookkeeper quick_refresh() first
2. Waits for sync completion
3. Reads capital.json, positions.json, orders.json
4. Displays complete capital breakdown

**Impact:** Accurate, fresh data on every /Status request.


## v4.3.5 — 2026-06-16

### ADDED
- `CHANGELOG_entry.md` — Detailed documentation of bookkeeper fix

### MODIFIED
- `bookkeeper.py` — Fixed Derayah order splitting handling:
  - Price tolerance for MARKET orders (0.0 matches any child price)
  - trigger_basis inheritance fix (.get(key, default) not .get(key) or default)
  - Multiple children handling (finds all children summing to parent qty)
  - Date-only timestamp parsing ('2026-06-15' properly handled)
  - Parent removal after matching
  - Approval: 'Do 1' from Amin, Backup: bookkeeper.py.backup-20260616-003540


### MODIFIED
- `bookkeeper.py` — Fixed prune_orders_json_terminal() calls:
  - Removed from api_headers() (was called on every API request, excessive)
  - Called in reconcile_orders() after recording terminal orders to history
  - orders.json now pruned of terminal orders (FILLED, CANCELLED, REJECTED, EXPIRED)
  - Only outstanding orders (INITIATED, PLACED, PARTIAL) kept in orders.json

## v4.5.4 — 2026-06-17 (WebSocket Incremental VWAP System)

### ADDED
- Incremental WebSocket VWAP calculation in `poller.py`
  - `_incremental_vwap_state` — per-symbol cumulative state (cum_pv, cum_weight, ticks)
  - `update_ws_vwap()` — updates VWAP on every websocket tick
  - `get_ws_vwap()` — retrieves cached VWAP if recent (< 300s)
  - `fetch_data()` — now returns `(price, df, source, ws_vwap)` with real-time VWAP
  - Weight formula: `real(2x) * change(1+change*30)` (larger real moves = higher weight)
- VWAP and volume fields in `ws_prices.jsonl` via `ws_logger.py`
  - `log_price()` now accepts `vwap` and `volume` parameters
  - WebSocket listener passes VWAP and volume to logger

### MODIFIED
- `fetch_data()` — returns 4 values instead of 3, with `ws_vwap` as 4th value
- All `fetch_data()` callers updated to unpack 4 values:
  - `fast_poll()` hard close — uses `ws_vwap` first, yfinance fallback
  - `slow_poll()` entry logic — uses `ws_vwap` for regime filter
  - Position sync, zone checks, price checks — all updated
- Hard close logic — `vwap_now = ws_vwap if ws_vwap is not None else calc_vwap(df)`
- Entry regime filter — `vwap_now = ws_vwap if ws_vwap is not None else calc_vwap(df)`

### REMOVED
- yfinance as PRIMARY VWAP source — now only used as fallback when websocket VWAP unavailable
- 15-minute delayed VWAP from yfinance — no longer used for real-time decisions

### ARCHITECTURE
```
Primary:    WebSocket tick → update_ws_vwap() → _incremental_vwap_state → get_ws_vwap()
Fallback 1: ws_prices.jsonl → build 1-min candles → calculate VWAP
Fallback 2: yfinance 5m OHLCV → calc_vwap() (15-min delayed, last resort)
Final:      Targets and time constraints (no VWAP available)
```

**Change Control:** ✅ **APPROVED by A A** — Critical feature implementation per brainstorm-2026-06-15

---

## v4.5.4b — 2026-06-17 (Remove yfinance from VWAP Decisions)

### MODIFIED
- `poller.py` — VWAP architecture updated to exclude yfinance from real-time decisions:
  - **Primary:** `get_ws_vwap()` — WebSocket incremental (real-time)
  - **Fallback 1:** `_calculate_tick_based_vwap()` — from `ws_prices.jsonl` ticks
  - **yfinance:** Only used for direction trend (`calc_vwap_direction`), NOT VWAP value
  - **Final:** Targets and time constraints when no VWAP available

- All VWAP decision points updated:
  - `fast_poll()` hard close: `vwap_now = ws_vwap` → fallback to tick-based → no yfinance
  - `slow_poll()` entry filter: same architecture
  - Position monitoring: same architecture

### DOCUMENTED
- Added comment block explaining `calc_vwap()` is for direction only, not real-time VWAP
- `calc_vwap_direction()` documented as using yfinance data for trend slope only

### ARCHITECTURE
```
For VWAP decisions (entry/exit/hard_close):
1. get_ws_vwap() — WebSocket incremental (real-time, preferred)
2. _calculate_tick_based_vwap() — from ws_prices.jsonl (tick-based fallback)
3. [NO yfinance for VWAP value — removed]
4. Targets and time constraints (no VWAP available)

For direction/trend only:
- calc_vwap_direction() — uses yfinance for slope (15-min delay OK for trend)
```

**Change Control:** ✅ **APPROVED by A A** — Critical architecture fix per brainstorm-2026-06-15

---

## v4.5.5 — 2026-06-18 (1-Minute Candle Recovery Score)

### ADDED
- `build_1min_candles()` function in `poller.py` — builds 1-minute OHLCV candles from `ws_frames_raw.log`
  - **Purpose:** More accurate recovery score calculation with finer granularity
  - **Method:** Groups websocket ticks by minute, calculates Open/High/Low/Close/Volume
  - **Volume weighting:** Real ticks = 2, Snapshots = 1 (to prioritize actual trades)
- 1-minute candle recovery logic in VWAP breakdown exit handler
  - **Window:** 15 candles × 1 minute = 15 minutes (vs previous 25 minutes with 5-min candles)
  - **Threshold:** Adjusted from >0.66 to >0.60 (more sensitive for finer granularity)
  - **Min hold time:** Reduced from 15 min to 10 min (faster response to market moves)

### MODIFIED
- VWAP breakdown exit logic (`poller.py`) — Now uses 1-minute candles first, falls back to 5-minute
  - **Priority 1:** Build 1-min candles from websocket raw log
  - **Priority 2:** Fall back to existing 5-min yfinance candles if 1-min unavailable
  - **Benefit:** Catches rapid reversals faster, reduces holding time during false breakdowns

### DELETED
- Nothing

**Change Control:** ✅ **APPROVED by A A** — Performance improvement per brainstorm-2026-06-15

---

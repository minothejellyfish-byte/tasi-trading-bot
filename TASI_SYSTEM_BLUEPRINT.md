# TASI Trading System Blueprint — v4.2
## Updated: 2026-06-09 | Status: Live — Position Tracking Fixed, Poller Running

---

## 🏗️ Architecture

| File | Purpose | v4.1/v4.2 Changes |
|------|---------|-------------------|
| **screener.py** | Premarket stock screening | ✅ Gap detection, wider entries (close×0.98), volume exception, atomic writes |
| **midscreen_ws.py** | Mid-session momentum screening (10:30, 12:00, 13:30) | ✅ Wider entries, atomic writes |
| **poller.py** | Price polling, entry/exit signals, auto-trade execution | ✅ VWAP exit, tiered profits, trailing stop, dynamic hard close, partial sell tracking, **BASE_DIR fix (2026-06-09)** |
| **bot.py** | Telegram bot, keepalive, commands, capital refresh | ✅ Net qty tracking, fee calc, CLOSE ALL (2026-06-09) |
| **market_regime.py** | Market regime classifier (TRENDING/NEUTRAL/DEFENSIVE) | Unchanged (v4.0) |
| **ws_probe.py** | WebSocket frame discovery | ✅ Keepalive + /SS fix (2026-06-09) |
| **ws_keepalive_v2.sh** | WebSocket keepalive monitor | ✅ Checks ws_frames_raw.log (not ws_frames.json) |

---

## 📅 Auto-Start Sequence (Daily)

```
09:45 — browser_start.py (auto-login Derayah)
09:50 — tasi-premarket-screener (v4.1: gap-aware picks)
09:55 — cleanup_stand_down.sh (remove stand_down + blocked_symbols)
10:00 — tasi-price-poller starts (v4.1: VWAP + tiered exits)
10:30 — tasi-midscreen-1 (v4.1: wider entries)
12:00 — tasi-midscreen-2
13:30 — tasi-rescreen
14:30 — Dynamic hard close window opens (v4.1: VWAP-aware monitoring)
14:50 — Final market sell deadline
15:35 — post-market-analysis
```

---

## 🛡️ Exit Strategy (v4.1 Complete)

### 8 Exit Rules (Priority Order)

| # | Rule | Trigger | Action | Applies |
|---|------|---------|--------|---------|
| **1** | **Hard Stop** | -7% loss | Sell 100% | All positions |
| **2** | **VWAP Breakdown** | Price < VWAP | Sell 100% (even if profitable) | All positions |
| **3** | **Tier 1** | +2% | Sell 50% (keep runner) | qty > 1 |
| **4** | **Tier 2** | +5% | Sell 25% of runner | qty > 1 |
| **5** | **Tier 3** | +10% | Sell remaining | Any qty |
| **6** | **Trailing Stop** | Drop -3% from peak | Sell runner | After +2% reached |
| **7** | **Time Stop** | 30 min, down -1% | Sell 100% | All positions |
| **8** | **Dynamic Hard Close** | 14:30–14:50 | VWAP-aware exit | All open positions |

### Dynamic Hard Close (NEW v4.1)

| Time | Logic | Result |
|------|-------|--------|
| **14:30** | Start monitoring | Check VWAP for each position |
| Price ≥ VWAP + minimal loss | **WAIT** until 14:50 | Might improve |
| Price < VWAP + loss | **EXIT NOW** | Cut loss before deepens |
| Loss < -3% | **EXIT NOW** | Stop bleeding |
| Small profit/loss | **Monitor** until 14:50 | Market sell at deadline |
| **14:50** | Final deadline | Market sell ALL remaining |

---

## 📊 Entry Strategy (v4.1 Complete)

### Screener Filters

| Filter | v4.0 | v4.1 | Change |
|--------|------|------|--------|
| MIN_PRICE | 10.0 SAR | **5.0 SAR** | Lowered |
| Volume check | Before scoring | **After scoring** | High scores get exception |
| Volume exception | None | **score ≥ 80: vol ≥ 50K** | NEW |
| Entry zone | prev_high × 0.995 | **min(prev_high×0.995, close×0.98)** | Wider |
| Gap detection | None | **Premarket gap from Open/Close** | NEW |
| Direction filter | None | **Gap-up → market order; Gap-down → skip/adjust** | NEW |
| Atomic writes | No | **tempfile + os.rename** | Prevents corruption |

### Gap Detection

```python
premarket_change = (today_open - yesterday_close) / yesterday_close × 100

if gap_pct > +2.0% and score >= 120:
    → MARKET ORDER (capture momentum)
elif gap_pct < -3.0% and score < 120:
    → SKIP (falling knife)
elif gap_pct < -2.0%:
    → Adjusted entry: close × 0.95
```

---

## 💰 Capital Tracking

| File | Updated By | Trigger |
|------|-----------|---------|
| capital.json | bot.py STATUS | Manual command |
| capital.json | bot.py _capital_refresh_thread | Every 30 min |
| capital.json | poller.py | After every buy/sell |
| capital.json | bot.py `record_buy/sell()` | After every manual trade |
| capital.json | capital_tracker.py | `save_capital_full()` |

### Capital Fields
```json
{
  "available_capital": 988.95,
  "grand_total": 988.95,
  "securities_value": 0.0,
  "money_transfer": 988.95,
  "total_fees": 3.50,
  "initial_capital": 1000.66,
  "account": "001LOC-SAR TDWL",
  "source": "derayah-api",
  "updated_at": "2026-06-06T03:00:00"
}
```

### Fee Calculation
- Commission: 0.05% per trade
- VAT: 15% on commission
- Total fee per trade: 0.0575%

---

## 🎯 Market Regime

| Regime | target_pct | hard_stop | trail_trigger | trail_stop | time_stop | max_pos |
|--------|-----------|-----------|--------------|-----------|-----------|---------|
| **TRENDING** | 2.5% | -7% | +2.5% | -3% | -1%@30min | 3 |
| **NEUTRAL** | 2.0% | -7% | +2.0% | -3% | -1%@30min | 3 |
| **DEFENSIVE** | 2.0% | -7% | +2.0% | -3% | -1%@30min | 4 |

---

## 🚦 STAND DOWN Mode

- `stand_down` file blocks ALL buys
- Auto-created at 14:50 hard close
- Manual: Type "STAND DOWN" in Telegram
- Auto-removed at 09:55 before market open

---

---

## 📊 WebSocket Data Flow (v4.2 — 2026-06-09)

### Data Flow Map
| Component | Writes To | When | Purpose |
|-----------|-----------|------|---------|
| `ws_probe.py` | `ws_frames.json` | End of 90s run | Frame format discovery |
| `ws_probe.py` | `ws_frames_raw.log` | Every frame (continuous) | Raw WebSocket data |
| `poller.py` | `_ws_price_cache` | Every price update | Internal cache |
| `poller.py` → `ws_logger.py` | `ws_prices_YYYY-MM-DD.jsonl` | Every price update | Parsed live prices |

### Keepalive Fix
**Before:** Checked `ws_frames.json` size every 2 seconds — file only written at end of 90s run → false "not capturing" kills

**After:** Checks `ws_frames_raw.log` size every 2 seconds — file grows continuously → accurate detection

### /SS Command Fix
**Before:** Checked `ws_frames.json` for "Data freshness" — always stale (2+ days old)

**After:** Checks `ws_prices_*.jsonl` — reflects actual trading data freshness

---

## 📊 Position Tracking (v4.2 — 2026-06-09)

### Problem Fixed
Manual Telegram trades tracked positions as **discrete order pairs** (overwrote on new buy, closed entire position on any sell). This caused:
- Missing market close at 14:50 (bot thought position was closed)
- Wrong capital calculations (no fee tracking on manual trades)

### Solution
- **Net quantity tracking**: Adds to existing position, recalculates weighted avg entry
- **Fee calculation**: Commission (0.05%) + VAT (15%) on every manual trade
- **Capital update**: `capital.json` updated immediately after buy/sell
- **CLOSE ALL**: Market sells all open positions (previously "not implemented")

### Fee Math
```python
commission = trade_value * 0.0005  # 0.05%
vat = commission * 0.15         # 15%
total_cost = trade_value + commission + vat       # Buy
total_returned = trade_value - commission - vat   # Sell
```

---

## 🗂️ Key Files

| File | Purpose |
|------|---------|
| `/home/mino/tasi-exec/bot.py` | Telegram bot + keepalive |
| `/home/mino/tasi-exec/poller.py` | Price poller + trader (v4.1 exits) |
| `/home/mino/tasi-exec/screener.py` | Premarket screener (v4.1 entries) |
| `/home/mino/tasi-exec/midscreen_ws.py` | Mid-session screener (v4.1) |
| `/home/mino/tasi-exec/market_regime.py` | Regime classifier |
| `/home/mino/tasi-exec/capital_tracker.py` | Capital utilities |
| **`/home/mino/tasi-exec/derayah_session_manager.py`** | **Session management (v4.2)** |
| **`/home/mino/tasi-exec/bot_commands.py`** | **Telegram commands (v4.2)** |
| `/home/mino/tasi-exec/capital.json` | Account balance |
| `/home/mino/tasi-exec/positions.json` | Open positions |
| `/home/mino/tasi-exec/regime.json` | Current regime |
| `/home/mino/tasi-exec/picks.json` | Current picks (all screens) |
| `/home/mino/tasi-exec/blocked_symbols.txt` | Blocked symbols |
| `/home/mino/tasi-exec/stand_down` | STAND DOWN marker |
| `/home/mino/tasi-exec/v4.1_recommendations.md` | v4.1 design doc |
| `/home/mino/tasi-exec/v4.2_enhancement_backlog.md` | v4.2 roadmap |
| `/home/mino/tasi-exec/ws_keepalive_v2.sh` | WebSocket keepalive |
| `/home/mino/tasi-exec/FIX_POSITION_TRACKING_2026-06-09.md` | Position tracking fix doc |
| `/home/mino/tasi-exec/FIX_WEBSOCKET_KEEPALIVE_2026-06-09.md` | WebSocket keepalive + /SS fix doc |

---

## 🕒 Crons (agentId: main)

| Name | Time | Purpose | Delivery |
|------|------|---------|----------|
| tasi-premarket-screener | 09:50 | v4.1 gap-aware scan | Group |
| tasi-price-poller | 10:00 | v4.1 VWAP + tiered exits | Silent |
| tasi-midscreen-1 | 10:30 | v4.1 wider entries | Group |
| tasi-midscreen-2 | 12:00 | Mid-session scan | Group |
| tasi-rescreen | 13:30 | Pre-cutoff eval | Group |
| post-market-analysis | 15:35 | Daily review | DM |
| ocean-health-monitor | Every 30m | System health | Silent |
| daily-ram-cleanup | 04:00 | RAM cleanup | DM |
| tasi-log-cleanup | 04:00 | Log rotation | Silent |

---

## 🏦 Derayah Session Management (v4.2 — NEW)

### 3-Phase Session Lifecycle

| Phase | Name | Trigger | Action | Executor |
|-------|------|---------|--------|----------|
| **1** | **Login** | Manual `/Login` command | Capture tokens from browser localStorage | A A + bot.py |
| **2** | **Maintain** | Cron every 50 min (10:00–15:00) | SSO refresh via API → CDP navigation | Session Manager |
| **3** | **Recovery** | Health check failure | Auto-OTP (2 tries) → notify → standby | Session Manager |

### Token Storage
- **File**: `/home/mino/tasi-exec/derayah_tokens.json`
- **Tokens**: `Derayah_accesstoken`, `Derayah_refreshtoken`, `TC_DERAYAH`
- **Format**: JSON with expiry timestamps

### Session Health Checks
| Check | Frequency | Action on Failure |
|-------|-----------|-------------------|
| Token validity | Every 50 min | Attempt refresh |
| TC tab alive | Every 50 min | Open new tab |
| API test | Before each trade | Block trade, notify |

### Trade Blocking
- **Condition**: No valid session or expired token
- **Behavior**: Reject order immediately
- **Message**: "🚫 Session expired. Run /Login first."

### Phase 3: Recovery Flow
```
Refresh fails
    → Open login tab (auto)
    → Attempt OTP via email (×2)
    → If success: capture tokens, resume
    → If captcha/fail: Telegram notify A A
    → State: STAND BY (no stand_down file)
    → Wait for manual /Login
```

### New Files (v4.2)
| File | Purpose |
|------|---------|
| `derayah_session_manager.py` | Core session logic (capture, refresh, health) |
| `bot_commands.py` | Telegram handlers (/Login, /SS, validate_session) |
| `derayah_refresh_cron.sh` | 50-min cron wrapper with Phase 3 recovery |

### Updated Crons
| Name | Time | Purpose |
|------|------|---------|
| derayah-refresh | */50 10-15 * * 0-4 | Proactive token refresh |
| tasi-premarket-screener | 09:50 | v4.1 gap-aware scan |
| tasi-price-poller | 10:00 | v4.1 VWAP + tiered exits |

### RACI Matrix (Session Management)
| Activity | A A | Mino (AI) | bot.py | Session Manager | WS Keepalive | Watchdog |
|----------|-----|-----------|--------|-----------------|--------------|----------|
| Phase 1: Login | R,A | C | R | I | I | I |
| Phase 2: Maintain | I | C | I | R,A | C | I |
| Phase 3: Detect | I | C | I | R | I | A |
| Phase 3: Notify | I | R | R | R | I | I |
| Phase 3: Re-login | R,A | C | I | I | I | I |
| Trade Execution | A | C | R | I | C | I |
| Session Validation | I | I | R | I | I | A |

---

- **Port**: 18801
- **Profiles**: 
  - Active: `/home/mino/.config/google-chrome/derayah-live` (created 2026-06-04 to fix Chrome 148 freeze bug)
  - Legacy: `/home/mino/.config/google-chrome/derayah-profile` (original, may have freeze issues)
- **Proxy**: socks5://localhost:1080
- **Tabs**: Derayah dashboard + TickerChart
- **Keepalive**: bot.py `_keepalive_thread()` every 15 min

---

## 📈 v4.1 Success Metrics (Week 1 Targets)

| Metric | Target |
|--------|--------|
| Entry accuracy | 60% |
| Gap-down losses | -5% max |
| Gap-up captures | 50% |
| Win rate | 55% |
| Average return/trade | +1.2% |

---

## 🚀 v4.2 Backlog (Next Week)

1. **VWAP Integration** — Entry zone refinement
2. **Regime-Aware Score Thresholds** — Adaptive filtering
3. **Daily P&L Circuit Breaker** — Loss protection
4. **Capital-Based Position Slots** — Partial sell reuse
5. RVOL Filter, Sector Rotation, Multi-timeframe, etc.

---

## ✅ v4.1 Deployment Status

| Component | File | Status |
|-----------|------|--------|
| Screener | screener.py | ✅ Ready |
| Midscreens | midscreen_ws.py | ✅ Ready |
| Poller/Exits | poller.py | ✅ Ready |
| Bot | bot.py | ✅ Ready |
| Regime | market_regime.py | ✅ Ready |

**System validated. Ready for Sunday market open.** 💙

# Current vs Proposed Changes — Implementation Phases
## poller.py v4.5 Upgrade
## Date: 2026-06-15
## Status: ASK — Awaits explicit "Do it"

---

## Change Overview Table

| # | Feature | Current (v4.4) | Proposed (v4.5) | Impact | Risk |
|---|---------|---------------|-----------------|--------|------|
| 1 | **Market Open Entries** | Allow entries 10:00+ | **Block 10:00-10:15** | Skip noisy first 15 min | LOW |
| 2 | **Entry VWAP Filter** | No VWAP check | **Require VWAP rising** (NEUTRAL/DEF) | Better entry timing | MEDIUM |
| 3 | **Entry Regime Logic** | Same for all regimes | **TRENDING: allow all, NEUTRAL: require rising VWAP, DEF: block** | Adaptive entries | MEDIUM |
| 4 | **Recovery Score** | 5-min candles (25 min) | **1-min candles (15 min)** | Faster, more accurate | LOW |
| 5 | **Exit Time Stops** | Fixed 30 min, -1% | **Dynamic: TRENDING=none, NEUTRAL=12:00/14:00/14:30, DEF=11:30/13:00/14:00** | Regime-aware exits | MEDIUM |
| 6 | **Trailing Stop** | -3% from peak (all) | **TRENDING: -3% peak, NEUTRAL: -2% entry, DEF: -1.5% entry** | Tighter in chop | MEDIUM |
| 7 | **Profit Target** | +2% (all) | **TRENDING: +2%, NEUTRAL: +1%, DEF: +0.5%** | Quick profits in DEF | LOW |
| 8 | **VWAP Exits** | Enabled (all) | **Disabled in TRENDING** | Let trends run | LOW |
| 9 | **Hard Stop** | -7% (all) | **TRENDING: -7%, NEUTRAL: -7%, DEF: -5%** | Tighter in DEF | LOW |
| 10 | **Min Hold** | 15 min (all) | **TRENDING: 15, NEUTRAL: 15, DEF: 10** | Faster exits in DEF | LOW |

---

## Implementation Phases

### Phase 1: Market Open Cooldown (Simplest)
**File:** `poller.py` — Entry loop (line ~1900)
**Changes:**
- Add constant `MARKET_OPEN_COOLDOWN_MINS = 15`
- Add time check before gap-up/VWAP entry signals
- Skip entries if `now_time < 10:15`

**Code:**
```python
# Add near top with other constants
MARKET_OPEN_TIME = time(10, 0)
MARKET_OPEN_COOLDOWN_MINS = 15

# Add in slow_poll() entry loop, before gap-up check
now_time = datetime.now(RIYADH).time()
market_open_dt = datetime.now(RIYADH).replace(hour=10, minute=0, second=0, microsecond=0)
cooldown_end = market_open_dt + timedelta(minutes=MARKET_OPEN_COOLDOWN_MINS)

if now_time >= MARKET_OPEN_TIME and datetime.now(RIYADH) < cooldown_end:
    log.info(f"Market open cooldown active (until 10:15) - skipping {base}")
    continue
```

**Testing:** Verify no entries before 10:15
**Risk:** LOW — Simple time check

---

### Phase 2: VWAP Direction Filter for Entries
**File:** `poller.py` — Entry logic (line ~1900)
**Changes:**
- Add `get_vwap_direction()` function
- Check VWAP direction before entry in NEUTRAL/DEFENSIVE
- Skip if VWAP falling

**Code:**
```python
# Add as helper function
def get_vwap_direction(df, window=5):
    """Calculate VWAP direction over last N candles"""
    recent = df.tail(window)
    if len(recent) >= 2:
        return recent['Close'].iloc[-1] - recent['Close'].iloc[0]
    return 0

# Add in slow_poll() before entry signals
if regime_name in ["NEUTRAL", "DEFENSIVE"]:
    vwap_dir = get_vwap_direction(df)
    if vwap_dir <= 0:
        log.info(f"{base} skipped - VWAP falling ({vwap_dir:.4f}) in {regime_name}")
        continue
```

**Testing:** Backtest on June 14 (should skip 10:00 entry)
**Risk:** MEDIUM — May skip profitable trades

---

### Phase 3: Regime-Aware Exit Parameters
**File:** `poller.py` — Exit thresholds (line ~1650)
**Changes:**
- Add `get_regime_exit_params()` function
- Add `get_time_stop()` function
- Replace fixed params with regime-aware params

**Code:**
```python
# Add after imports
def get_regime_exit_params(regime_name):
    params = {
        "TRENDING": {
            "time_stops": None,
            "trail_pct": 0.03,  # From peak
            "profit_target": 0.02,
            "hard_stop": 0.07,
            "min_hold": 15,
            "vwap_exit": False,
        },
        "NEUTRAL": {
            "time_stops": {
                "before_1030": "12:00",
                "1030_to_1200": "14:00",
                "after_1200": "14:30"
            },
            "trail_pct": 0.02,  # From entry
            "profit_target": 0.01,
            "hard_stop": 0.07,
            "min_hold": 15,
            "vwap_exit": True,
        },
        "DEFENSIVE": {
            "time_stops": {
                "before_1030": "11:30",
                "1030_to_1200": "13:00",
                "after_1200": "14:00"
            },
            "trail_pct": 0.015,  # From entry
            "profit_target": 0.005,
            "hard_stop": 0.05,
            "min_hold": 10,
            "vwap_exit": True,
        }
    }
    return params.get(regime_name, params["NEUTRAL"])

def get_time_stop(entry_time_str, time_stops):
    if time_stops is None:
        return None
    hour = int(entry_time_str.split(":")[0])
    minute = int(entry_time_str.split(":")[1])
    if hour < 10 or (hour == 10 and minute < 30):
        return time_stops["before_1030"]
    elif hour < 12:
        return time_stops["1030_to_1200"]
    else:
        return time_stops["after_1200"]
```

**Testing:** Verify params load correctly per regime
**Risk:** MEDIUM — Core logic change

---

### Phase 4: Time Stop Logic Update
**File:** `poller.py` — Exit logic (line ~1660)
**Changes:**
- Replace fixed 30-min time stop with dynamic regime-aware time stop
- TRENDING: No time stop
- NEUTRAL/DEFENSIVE: Time based on entry time

**Code:**
```python
# OLD: Remove this
# elif (mins_held >= time_stop_mins and gain_pct <= -time_stop_pct
#       and key_time_stop not in _alerted):

# NEW: Regime-aware time stop
elif key_time_stop not in _alerted:
    if exit_params["time_stops"] is not None:
        entry_time_str = pos.get("entry_time", "")
        if entry_time_str:
            try:
                time_stop_str = get_time_stop(entry_time_str, exit_params["time_stops"])
                if time_stop_str:
                    time_stop_hour, time_stop_min = map(int, time_stop_str.split(":"))
                    time_stop_time = time(time_stop_hour, time_stop_min)
                    
                    if now_time >= time_stop_time:
                        auto_sell(symbol, qty,
                                  f"⏱ Regime time stop ({regime_name}) | Held {int(mins_held)} min",
                                  trigger_basis=TRIGGER_TIME_STOP)
                        _alerted.add(key_time_stop)
            except Exception as e:
                log.error(f"Time stop error: {e}")
```

**Testing:** Verify time stops fire at correct times
**Risk:** MEDIUM — Could exit too early/late

---

### Phase 5: Trail Stop Update
**File:** `poller.py` — Trailing stop (line ~1670)
**Changes:**
- TRENDING: Trail from peak (-3%)
- NEUTRAL/DEFENSIVE: Trail from entry (-2% / -1.5%)

**Code:**
```python
# OLD: Single trail logic
# elif peak_pct >= trail_trigger and drop_from_peak >= trail_stop_pct

# NEW: Regime-aware trail
if regime_name == "TRENDING" and peak_price > entry * 1.02:
    # Trail from peak
    trail_level = peak_price * (1 - exit_params["trail_pct"])
    if price <= trail_level and key_trail not in _alerted:
        auto_sell(symbol, qty, f"📉 Trail from peak (TRENDING)",
                  trigger_basis=TRIGGER_TRAILING_STOP)
        _alerted.add(key_trail)
elif regime_name != "TRENDING":
    # Trail from entry
    if gain_pct <= -exit_params["trail_pct"] and key_trail not in _alerted:
        auto_sell(symbol, qty, f"📉 Trail from entry ({regime_name})",
                  trigger_basis=TRIGGER_TRAILING_STOP)
        _alerted.add(key_trail)
```

**Testing:** Verify trail triggers correctly per regime
**Risk:** MEDIUM — Different logic paths

---

### Phase 6: 1-Minute Candle Recovery Score
**File:** `poller.py` — VWAP breakdown (line ~1700)
**Changes:**
- Add `calculate_recovery_1min()` function
- Replace 5-min recovery with 1-min recovery

**Code:**
```python
# Add as helper function
import yfinance as yf

def calculate_recovery_1min(symbol, entry_time, window=15):
    try:
        ticker = yf.Ticker(f"{symbol}.SR")
        df_1min = ticker.history(period="1d", interval="1m")
        if df_1min.empty:
            return 0.5
        
        df_1min = df_1min.tz_convert("Asia/Riyadh")
        entry_dt = datetime.fromisoformat(entry_time)
        
        df_after = df_1min[df_1min.index > entry_dt].tail(window)
        if len(df_after) < 3:
            return 0.5
        
        closes = [float(c) for c in df_after["Close"]]
        rising = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
        total = len(closes) - 1
        return rising / total if total > 0 else 0.5
    except Exception as e:
        log.error(f"1-min recovery error: {e}")
        return 0.5

# In VWAP breakdown logic, replace:
# OLD: recent_candles = df_pos.tail(5)
# NEW:
recovery_score = calculate_recovery_1min(symbol, pos.get("entry_time", ""))
```

**Testing:** Compare accuracy vs 5-min version
**Risk:** LOW — Fallback to 0.5 on error

---

### Phase 7: VWAP Exit Control
**File:** `poller.py` — VWAP breakdown (line ~1700)
**Changes:**
- Disable VWAP exits in TRENDING regime
- Keep in NEUTRAL/DEFENSIVE

**Code:**
```python
# Add condition check
elif key_vwap_exit not in _alerted and exit_params["vwap_exit"]:
    # Existing VWAP breakdown logic...
    pass
elif not exit_params["vwap_exit"]:
    # TRENDING: Skip VWAP exits
    pass
```

**Testing:** Verify VWAP exits disabled in TRENDING
**Risk:** LOW — Simple boolean check

---

## Phase Execution Order

| Phase | Feature | Risk | Duration |
|-------|---------|------|----------|
| 1 | Market open cooldown | LOW | 1 day |
| 2 | VWAP direction filter | MEDIUM | 2-3 days |
| 3 | Regime-aware params | MEDIUM | 2-3 days |
| 4 | Time stop logic | MEDIUM | 1-2 days |
| 5 | Trail stop update | MEDIUM | 1-2 days |
| 6 | 1-min recovery | LOW | 2-3 days |
| 7 | VWAP exit control | LOW | 1 day |

**Recommended order:** 1 → 7 → 3 → 4 → 5 → 2 → 6
(Start with low-risk, then medium-risk)

---

## Rollback Plan

If issues occur:
1. Revert to v4.4 using git
2. Disable individual features via flags
3. Monitor logs for unexpected behavior

---

## Files Modified

| File | Lines Changed | Purpose |
|------|--------------|---------|
| `poller.py` | ~150 | Main logic updates |
| `market_regime.py` | 0 | No changes (already correct) |
| `positions.json` | +1 field | Add `vwap_direction` |

---

*Prepared by Mino 🪼 | 2026-06-15*

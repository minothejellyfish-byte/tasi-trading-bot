# Implementation Plan v4.5
## Regime-Aware Exits + Market Open Cooldown
## Date: 2026-06-15
## Status: ASK — Requires explicit "Do it" before implementation

---

## Changes Required

### 1. Market Open Cooldown (NEW)
### 2. Regime-Aware Exit Parameters (NEW)
### 3. Time Stop Logic (MODIFY)
### 4. Trail Stop Update (MODIFY)
### 5. VWAP Exit Control (MODIFY)
### 6. VWAP Direction Filter for Entries (NEW)
### 7. 1-Minute Candle Recovery Score (NEW)

---

## Decision Mapping

| Memo # | Decision Item | Implementation Section | Status |
|--------|--------------|------------------------|--------|
| #1 | VWAP direction filter for NEUTRAL entries | §6 VWAP Direction Filter | ASK |
| #2 | Regime-aware entry logic | §2 Regime-Aware Parameters + §6 VWAP Filter | ASK |
| #3 | 1-minute candle recovery score | §7 1-Minute Candle Recovery | ASK |
| #4 | Exit strategy improvements | §5 VWAP Exit Control | ASK |
| #5 | Regime-aware exit parameters | §2 Regime-Aware Exit Parameters | ASK |
| #6 | Dynamic time stops based on entry time | §3 Time Stop Logic | ASK |
| #7 | Market open cooldown | §1 Market Open Cooldown | ASK |

---

### 1. Market Open Cooldown (NEW)
**Memo item:** #7 — Approve market open cooldown (no entries 10:00-10:15)

**File:** `poller.py` (entry logic section)
**Line:** Around line 1900 (entry signals loop)

**Add after existing imports:**
```python
# Market open cooldown - no entries in first 15 minutes
MARKET_OPEN_TIME = time(10, 0)
MARKET_OPEN_COOLDOWN_MINS = 15
```

**Add in `slow_poll()` entry loop (before gap-up and VWAP reclaim checks):**
```python
        # ── Market Open Cooldown (v4.5) ───────────────────────────────────
        now_time = datetime.now(RIYADH).time()
        market_open_dt = datetime.now(RIYADH).replace(hour=10, minute=0, second=0, microsecond=0)
        cooldown_end = market_open_dt + timedelta(minutes=MARKET_OPEN_COOLDOWN_MINS)
        
        if now_time >= MARKET_OPEN_TIME and datetime.now(RIYADH) < cooldown_end:
            log.info(f"Market open cooldown active (until 10:15) - skipping {base}")
            continue
```

**Impact:** Prevents entries 10:00-10:15. Simple, no risk.

---

### 2. Regime-Aware Exit Parameters (NEW)
**Memo item:** #5 — Approve regime-aware exit parameters (time stops, trail, profit targets)

**File:** `poller.py` (exit logic section)
**Line:** Around line 1650 (exit thresholds)

**Add new function (after imports, before main logic):**
```python
def get_regime_exit_params(regime_name):
    """Get exit parameters based on market regime (v4.5)"""
    params = {
        "TRENDING": {
            "time_stops": None,           # DISABLED - let winners run
            "trail_pct": 0.03,           # -3% from peak (loose)
            "profit_target": 0.02,        # +2%
            "hard_stop": 0.07,           # -7%
            "min_hold": 15,
            "vwap_exit": False,          # Don't use VWAP exits
            "time_stop_mins": None,
            "time_stop_pct": None,
        },
        "NEUTRAL": {
            "time_stops": {              # Dynamic based on entry time
                "before_1030": "12:00",
                "1030_to_1200": "14:00",
                "after_1200": "14:30"
            },
            "trail_pct": 0.02,           # -2% from entry
            "profit_target": 0.01,        # +1%
            "hard_stop": 0.07,           # -7%
            "min_hold": 15,
            "vwap_exit": True,           # Use VWAP convergence
            "time_stop_mins": 30,        # Kept for backward compat
            "time_stop_pct": 0.01,
        },
        "DEFENSIVE": {
            "time_stops": {              # Earlier exits
                "before_1030": "11:30",
                "1030_to_1200": "13:00",
                "after_1200": "14:00"
            },
            "trail_pct": 0.015,          # -1.5% (tighter)
            "profit_target": 0.005,       # +0.5% (quick profits)
            "hard_stop": 0.05,           # -5% (tighter)
            "min_hold": 10,              # Shorter hold
            "vwap_exit": True,           # Quick VWAP exits
            "time_stop_mins": 20,
            "time_stop_pct": 0.005,
        }
    }
    return params.get(regime_name, params["NEUTRAL"])

def get_time_stop(entry_time_str, time_stops):
    """Get time stop based on entry time (v4.5)"""
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

**Modify existing exit logic (around line 1650):**
```python
        # ── Dynamic regime-based exit thresholds (v4.5) ───────────────────
        regime_params = get_current_regime().get("params", {})
        regime_name = get_current_regime().get("regime", "NEUTRAL")
        
        # Get regime-aware exit parameters
        exit_params = get_regime_exit_params(regime_name)
        
        # Override with regime-specific values
        win_pct = exit_params["profit_target"]
        hard_stop_pct = exit_params["hard_stop"]
        trail_stop_pct = exit_params["trail_pct"]  # Now used differently
        time_stop_mins = exit_params.get("time_stop_mins", 30)
        time_stop_pct = exit_params.get("time_stop_pct", 0.01)
```

---

### 3. Time Stop Logic (MODIFY)
**Memo item:** #6 — Approve dynamic time stops based on entry time

**File:** `poller.py` (exit logic section)
**Line:** After existing time stop check

**Replace existing time stop:**
```python
        # OLD (remove):
        # elif (mins_held >= time_stop_mins and gain_pct <= -time_stop_pct
        #       and key_time_stop not in _alerted):
        
        # NEW (v4.5): Regime-aware time stop
        elif key_time_stop not in _alerted:
            # Check if time stop is enabled for this regime
            if exit_params["time_stops"] is not None:
                # Get entry time
                entry_time_str = pos.get("entry_time", "")
                if entry_time_str:
                    try:
                        entry_dt = datetime.fromisoformat(entry_time_str)
                        time_stop_str = get_time_stop(entry_time_str, exit_params["time_stops"])
                        if time_stop_str:
                            time_stop_hour, time_stop_min = map(int, time_stop_str.split(":"))
                            time_stop_time = time(time_stop_hour, time_stop_min)
                            
                            if now_time >= time_stop_time:
                                auto_sell(symbol, qty,
                                          f"⏱ Regime time stop ({regime_name}) | Held {int(mins_held)} min | Entry: {entry:.2f} | Now: {price:.2f} ({gain_pct*100:.1f}%)",
                                          trigger_basis=TRIGGER_TIME_STOP,
                                          trigger_detail=f"Regime time stop: {regime_name}, held {int(mins_held)}min")
                                _alerted.add(key_time_stop)
                                log.info(f"Regime time stop: {symbol} {regime_name} held={int(mins_held)}min")
                    except Exception as e:
                        log.error(f"Time stop error: {e}")
```

---

### 4. Trail Stop Update (MODIFY)
**Memo item:** #5 — Approve regime-aware exit parameters (part of trail config)

**File:** `poller.py` (trailing stop section)
**Line:** Around line 1670

**Modify trailing stop logic:**
```python
        # v4.5: Regime-aware trailing stop
        # TRENDING: Trail from peak (-3%)
        # NEUTRAL/DEFENSIVE: Trail from entry (-2% / -1.5%)
        if regime_name == "TRENDING" and peak_price > entry * 1.02:
            # Trail from peak
            trail_level = peak_price * (1 - exit_params["trail_pct"])
            if price <= trail_level and key_trail not in _alerted:
                auto_sell(symbol, qty,
                          f"📉 Trailing stop (TRENDING) | Peak: {peak:.2f} | Now: {price:.2f} | Drop: {(peak-price)/peak*100:.1f}%",
                          trigger_basis=TRIGGER_TRAILING_STOP,
                          trigger_detail=f"Trail from peak: {peak:.2f} → {price:.2f} ({(peak-price)/peak*100:.1f}%) threshold: {exit_params['trail_pct']*100:.1f}%")
                _alerted.add(key_trail)
                log.info(f"Trail from peak: {symbol} peak={peak:.2f} now={price:.2f}")
        elif regime_name != "TRENDING":
            # Trail from entry
            if gain_pct <= -exit_params["trail_pct"] and key_trail not in _alerted:
                auto_sell(symbol, qty,
                          f"📉 Trailing stop ({regime_name}) | Entry: {entry:.2f} | Now: {price:.2f} ({gain_pct*100:.1f}%)",
                          trigger_basis=TRIGGER_TRAILING_STOP,
                          trigger_detail=f"Trail from entry: {gain_pct*100:.1f}% (threshold: -{exit_params['trail_pct']*100:.1f}%)")
                _alerted.add(key_trail)
                log.info(f"Trail from entry: {symbol} {gain_pct*100:.1f}%")
```

---

### 5. VWAP Exit Control (MODIFY)
**Memo item:** #4 — Approve exit strategy improvements (VWAP exits)

**File:** `poller.py` (VWAP breakdown section)
**Line:** Around line 1700

**Modify VWAP breakdown to respect regime:**
```python
        # v4.5: VWAP exits only in NEUTRAL/DEFENSIVE, disabled in TRENDING
        elif key_vwap_exit not in _alerted and exit_params["vwap_exit"]:
            # ... existing VWAP breakdown logic ...
            pass
        elif not exit_params["vwap_exit"]:
            # TRENDING: Skip VWAP exits, rely on trail/profit target
            pass
```

### 6. VWAP Direction Filter for Entries (NEW)
**Memo item:** #1 — Approve VWAP direction filter for NEUTRAL entries

**File:** `poller.py` (entry logic section)
**Line:** Around line 1900 (before gap-up and VWAP reclaim checks)

**Add function:**
```python
def get_vwap_direction(df, window=5):
    """Calculate VWAP direction over last N candles (v4.5)"""
    recent = df.tail(window)
    if len(recent) >= 2:
        return recent['Close'].iloc[-1] - recent['Close'].iloc[0]
    return 0
```

**Modify entry logic in `slow_poll()`:**
```python
        # ── VWAP Direction Filter (v4.5) ───────────────────────────────────
        if regime_name in ["NEUTRAL", "DEFENSIVE"]:
            vwap_dir = get_vwap_direction(df)
            if vwap_dir <= 0:
                log.info(f"{base} skipped - VWAP falling ({vwap_dir:.4f}) in {regime_name} regime")
                continue
```

**Impact:** Only enters when VWAP is rising in NEUTRAL/DEFENSIVE.

---

### 7. 1-Minute Candle Recovery Score (NEW)
**Memo item:** #3 — Approve 1-minute candle recovery score

**File:** `poller.py` (exit logic section)
**Line:** Around line 1700 (VWAP breakdown exit)

**Add function:**
```python
def calculate_recovery_1min(symbol, entry_time, window=15):
    """Calculate recovery score using 1-minute candles (v4.5)"""
    try:
        ticker = yf.Ticker(f"{symbol}.SR")
        df_1min = ticker.history(period="1d", interval="1m")
        if df_1min.empty:
            return 0.5  # Default
        
        df_1min = df_1min.tz_convert("Asia/Riyadh")
        entry_dt = datetime.fromisoformat(entry_time)
        
        # Filter candles after entry
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
```

**Modify VWAP breakdown exit logic:**
```python
        # Replace 5-min recovery with 1-min recovery
        # OLD (remove):
        # recent_candles = df_pos.tail(5)
        # closes = [float(c) for c in recent_candles["Close"]]
        
        # NEW (v4.5):
        recovery_score = calculate_recovery_1min(symbol, pos.get("entry_time", ""))
        recovery_prob = recovery_score  # Already normalized
```

**Impact:** More accurate recovery prediction.

---

1. **Backup current poller.py**
2. **Apply changes** (5 sections above)
3. **Test on historical data** (June 14)
4. **Paper trade for 1 day**
5. **Deploy with monitoring**

---

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|------------|
| Market open cooldown | LOW | Simple time check, no logic changes |
| Regime params | MEDIUM | Backward compatible defaults |
| Time stop logic | MEDIUM | Test with different entry times |
| Trail from entry | MEDIUM | Verify doesn't trigger too early |

---

## Approval Required

**A A must say "Do it" to implement.**

Changes are ASK tier:
- poller.py modifications
- Exit logic restructuring
- New regime-aware parameters

---

*Prepared by Mino 🪼 | 2026-06-15*

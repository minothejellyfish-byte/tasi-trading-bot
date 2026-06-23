# Proposed Exit Strategy Improvements

## Date: 2026-06-15
## Status: PROPOSED (Not Implemented)
## Tier: ASK — Requires explicit approval

---

## Current Exit Logic (poller.py lines 1674-1725)

```python
# Step 1: Minimum hold 15 minutes
if mins_held < MIN_HOLD_MINS:  # 15 min
    continue

# Step 2: Recovery score (5-min candles)
recent_candles = df_pos.tail(5)  # 25 minutes!
recovery_score = recovery_prob * vol_strength

# Step 3: Breakeven hold
if loss < 3% and recovery_score > 0.66:
    continue
```

**Problems:**
1. 15-min hold too short — June 14 sold at 15 min, missed recovery
2. 5-min candles miss rapid reversals
3. Breakeven hold only for small losses
4. No profit target — only tiered exits (+2%, +5%, +10%)

---

## Proposed Improvements

### 1. Extend Minimum Hold to 30 Minutes

```python
MIN_HOLD_MINS = 30  # Was 15
```

**Rationale:**
- June 14: 5110 at 10:20, sold 15 min later at loss
- If held 30 min, would have seen recovery
- Prevents panic selling on temporary dips

### 2. Replace 5-Min with 1-Min Candles for Recovery Score

```python
# OLD (5-min candles = 25 min window)
recent_candles = df_pos.tail(5)

# NEW (1-min candles = 15 min window)
recent_candles = build_1min_candles(df_pos).tail(15)
```

**Benefits:**
- Faster detection of reversals
- More accurate recovery probability
- Proven in `analyze_1min_recovery_v2.py`

### 3. Add VWAP-Based Profit Target

```python
# NEW: Sell when price reaches +2% above VWAP
vwap_profit_target = vwap_entry * 1.02  # +2% above entry VWAP

if price >= vwap_profit_target and not alerted:
    auto_sell(symbol, qty, 
              f"🎯 VWAP Profit Target | Price {price:.2f} >= {vwap_profit_target:.2f} (+2%)",
              trigger_basis=TRIGGER_VWAP_PROFIT)
```

**Why:**
- Current: Only tiered profits (requires +2%, +5%, +10%)
- Proposed: Sell when VWAP distance is favorable
- Captures profit before VWAP breakdown

### 4. Improve Breakeven Hold Logic

```python
# OLD
if loss < 3% and recovery_score > 0.66:
    continue

# NEW: More nuanced
if loss < 2% and recovery_score > 0.60:
    # Small loss, decent recovery chance → hold
    continue
elif loss < 5% and recovery_score > 0.80:
    # Medium loss, strong recovery → hold
    continue
else:
    # Large loss or weak recovery → sell
    pass
```

### 5. Regime-Aware Exit Timing

```python
if regime == "TRENDING":
    MIN_HOLD_MINS = 15  # Allow quicker exits in trend
    PROFIT_TARGET = 0.025  # +2.5%
elif regime == "NEUTRAL":
    MIN_HOLD_MINS = 30  # Hold longer in choppy
    PROFIT_TARGET = 0.02  # +2%
else:  # DEFENSIVE
    MIN_HOLD_MINS = 45  # Hold longest in defensive
    PROFIT_TARGET = 0.015  # +1.5% (take smaller profits)
```

---

## Exit Priority (New Order)

1. **Hard Stop** (-5% to -7% depending on regime) — IMMEDIATE
2. **VWAP Profit Target** (+2% above VWAP) — IMMEDIATE
3. **Tier 1 Profit** (+2% from entry) — if qty > 1
4. **Trailing Stop** (peak -3%) — after +2% trigger
5. **VWAP Breakdown** — with 1-min recovery score
6. **Time Stop** (30 min, -1%)

---

## Expected Impact

| Metric | Current | Proposed | Improvement |
|--------|---------|----------|-------------|
| Min Hold | 15 min | 30 min | Less panic selling |
| Recovery Window | 25 min | 15 min | Faster detection |
| Profit Target | None (tiers only) | VWAP +2% | Earlier profit |
| Breakeven | <3% loss | <2% or strong recovery | Better holds |

**June 14 Example:**
- 5110 at 10:20, entry 17.03
- Sold 15 min later at 16.90 (-0.76%) ← TOO QUICK
- With 30 min hold: Price recovered to 17.00 by 10:50
- Would have avoided -1.19 SAR loss

---

## Implementation Plan

### Phase 1: Update Constants
```python
MIN_HOLD_MINS = 30  # Was 15
```

### Phase 2: Add 1-Min Candle Builder
```python
def build_1min_candles(df_5min):
    """Convert 5-min candles to 1-min using tick data"""
    # Implementation from analyze_1min_recovery_v2.py
```

### Phase 3: Add VWAP Profit Target
```python
# In position tracking
vwap_entry = calc_vwap(df_at_entry)
vwap_profit_target = vwap_entry * 1.02
```

### Phase 4: Update Recovery Score
```python
# Use 1-min candles instead of 5-min
recent_1min = build_1min_candles(df_pos).tail(15)
recovery_score = calculate_recovery(recent_1min)
```

### Phase 5: Regime-Aware Parameters
```python
# Load from regime.json dynamically
params = get_current_regime().get("params", {})
MIN_HOLD_MINS = params.get("time_stop_mins", 30)
```

---

## Decision Required

**A A must approve:**
1. ✅ Extend min hold from 15 to 30 minutes?
2. ✅ Use 1-min candles for recovery score?
3. ✅ Add VWAP profit target exit?
4. ✅ Make exit parameters regime-aware?

**All changes are ASK tier — require explicit "Do it" before implementation.**

---

*Prepared by Mino 🪼 | 2026-06-15 03:55 KSA*

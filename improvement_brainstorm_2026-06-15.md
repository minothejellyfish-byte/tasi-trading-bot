# MEMO: Entry Logic Improvements — Regime + VWAP Direction + 1-Min Candle

## Date: 2026-06-15
## Status: PROPOSED (Not Implemented)
## Tier: ASK — Requires explicit approval before implementation

---

## 1. Problem Statement

**Current behavior (June 14):**
- 6 entries, all resulted in losses (-3.60 SAR)
- Entries were mostly at or below VWAP (good prices)
- Losses caused by premature exits (VWAP breakdown too quick)
- BUT some entries had falling VWAP = bad timing

**Root cause:** Entry logic doesn't consider:
1. Market regime (NEUTRAL vs TRENDING)
2. VWAP direction (rising vs falling)
3. Recovery score accuracy (5-min vs 1-min candles)

---

## 2. Proposed Solution

### A. Regime Enforcement

**Current:** Regime detection exists in `market_regime.py` but only affects **exit targets**.

**Proposed:** Regime should also affect **entry logic**:

```python
if regime == "TRENDING":
    # Allow momentum entries (chase)
    entry_signal = check_vwap_reclaim(df, vwap)
elif regime == "NEUTRAL":
    # Wait for pullback, require VWAP rising
    entry_signal = price <= vwap and vwap_direction > 0
else:  # DEFENSIVE
    # No entries or strictest conditions
    entry_signal = False
```

**Regime Detection (existing):**
- Downloads TASI data every 30 min
- Session return > +1% + above VWAP → TRENDING
- Session return < -1% → DEFENSIVE
- Otherwise → NEUTRAL

**June 14 result:** NEUTRAL all day (correct!)

### B. VWAP Direction Filter

**Current:** Entry ignores VWAP direction.

**Proposed:** In NEUTRAL/DEFENSIVE, only enter if VWAP is RISING:

```python
def get_vwap_direction(df, window=5):
    """Calculate VWAP direction over last N minutes"""
    recent = df.tail(window)
    if len(recent) >= 2:
        return recent['vwap'].iloc[-1] - recent['vwap'].iloc[0]
    return 0

# Entry logic
vwap_dir = get_vwap_direction(df)
if regime in ["NEUTRAL", "DEFENSIVE"]:
    if vwap_dir <= 0:
        skip_entry("VWAP falling in neutral regime")
```

**June 14 results:**
| Time | Symbol | VWAP Direction | Current | Proposed | Saved? |
|------|--------|---------------|---------|----------|--------|
| 10:05 | 1320 | FALLING 📉 | ENTER | **SKIP** | ✅ +2.00 SAR |
| 10:20 | 5110 | RISING 📈 | ENTER | ENTER | ❌ Still lost |
| 11:01 | 1320 | FALLING 📉 | ENTER | **SKIP** | ✅ +0.00 SAR |
| 12:21 | 5110 | FALLING 📉 | ENTER | **SKIP** | ✅ +0.68 SAR |
| 13:17 | 1320 | FALLING 📉 | ENTER | **SKIP** | ❌ Missed +0.75 |
| 13:32 | 5110 | FALLING 📉 | ENTER | **SKIP** | ✅ +0.48 SAR |

**Net improvement: +2.41 SAR** (from -3.60 to -1.19)

### C. Market Open Cooldown (NEW)

**Problem:** First 15 minutes (10:00-10:15) are noisy, spreads wide, false signals.

**Proposed:** No entries in first 15 minutes of market open (10:00-10:15).

```python
MARKET_OPEN = time(10, 0)
COOLDOWN_MINUTES = 15

def can_enter(now):
    if now.time() < time(10, 15):
        return False, "Market open cooldown"
    return True, "OK"
```

**Why:**
- 10:00-10:15: Wide spreads, algorithm noise, false breakouts
- 10:15+: Price stabilizes, real signals emerge
- June 14: 1320 entry at 10:00 was bad (-0.70%), would have been avoided

**Implementation:** Skip entries before 10:15 regardless of regime.

### D. 1-Minute Candle Recovery Score

**Current:** Uses last 5 × 5-min candles = 25 min window.

**Problem:** Too slow, misses rapid reversals.

**Proposed:** Use last 15 × 1-min candles = 15 min window.

**Evidence:** `analyze_1min_recovery_v2.py` proved:
- 15 × 1-min candles: **correctly identified falling prices** after 5110 entry at 10:20
- 5 × 5-min candles: **missed the reversal**, gave false positive

**Implementation:**
```python
# In poller.py, change recovery score calculation
# From:
recovery_score = calculate_recovery_5min(df)
# To:
recovery_score = calculate_recovery_1min(df)  # More accurate
```

---

## 3. Implementation Plan

### Phase 1: VWAP Direction Calculation (poller.py)
- Add `calc_vwap_direction()` function
- Calculate over 5-minute window
- Store in position metadata

### Phase 2: Regime-Aware Entry Logic (poller.py)
- Read `effective_regime` from existing regime system
- Modify `check_entry_signals()` to check regime
- TRENDING: allow all entries
- NEUTRAL: require VWAP rising
- DEFENSIVE: block entries

### Phase 3: 1-Minute Candle Builder (poller.py)
- Create `build_1min_candles()` from ws_prices.jsonl
- Replace 5-min recovery score with 1-min version
- Test accuracy vs 5-min version

### Phase 4: Market Open Cooldown (poller.py)
- Add `MARKET_OPEN_COOLDOWN = 15` constant
- Modify `slow_poll()` entry logic to skip before 10:15
- Log skipped entries for analysis

### Phase 5: Exit Strategy Improvements
- Increase minimum hold from 15 to 30 minutes
- Use profit targets (+2%) instead of VWAP breakdown
- Regime-aware trailing stops

### Phase 6: Testing
- Backtest on multiple days
- Paper trade for 1 week
- Deploy with monitoring

---

## 4. Files to Modify

| File | Changes | Risk |
|------|---------|------|
| `poller.py` | Add VWAP direction, regime entry logic, 1-min candles, market open cooldown | **HIGH** |
| `market_regime.py` | No changes (already correct) | None |
| `positions.json` | Add `vwap_direction` field | Low |

---

## 5. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Skip profitable trades | Monitor for 1 week, adjust threshold |
| False VWAP signals | Use 5-min smoothed VWAP, not raw |
| Performance impact | Cache 1-min candles, don't rebuild every tick |
| Regime lag | Use confirmed regime (60-min hold) for entries |

---

## 6. Decision Required

**A A must decide:**
1. ✅ **Approve** VWAP direction filter for NEUTRAL entries?
2. ✅ **Approve** regime-aware entry logic?
3. ✅ **Approve** 1-minute candle recovery score?
4. ✅ **Approve** exit strategy improvements?
5. ✅ **Approve** regime-aware exit parameters (time stops, trail, profit targets)?
6. ✅ **Approve** dynamic time stops based on entry time?
7. ✅ **Approve** market open cooldown (no entries 10:00-10:15)?

**All changes are ASK tier — require explicit "Do it" before implementation.**

---

## NEW: Regime-Aware Exit Strategy (Added 2026-06-15 04:50)

### Dynamic Exit Parameters by Regime

| Parameter | TRENDING | NEUTRAL | DEFENSIVE |
|-----------|----------|---------|-----------|
| Time Stops | DISABLED | 12:00/14:00/14:30 | 11:30/13:00/14:00 |
| Trail | -3% from peak | -2% from entry | -1.5% from entry |
| Profit Target | +2% | +1% | +0.5% |
| Hard Stop | -7% | -7% | -5% |
| VWAP Exits | Disabled | Enabled (convergence) | Enabled (quick) |
| Min Hold | 15 min | 15 min | 10 min |

**Time Stop Schedule (NEUTRAL):**
- Entry before 10:30 → Time stop at 12:00
- Entry 10:30-12:00 → Time stop at 14:00
- Entry after 12:00 → Time stop at 14:30

**Backtest Results (June 14):**
| Regime | Total PnL | Improvement |
|--------|-----------|-------------|
| NEUTRAL (tested) | -0.56% | +1.14% ✅ |
| TRENDING | -0.53% | +1.17% |
| DEFENSIVE | -1.32% | +0.38% |

**Key insight:** Time stops prevent holding through chop in NEUTRAL regime.

---

---

## 7. References

- `analyze_1min_recovery_v2.py` — 1-min vs 5-min candle accuracy
- `backtest_combined_vwap.py` — backtest results
- `poller.py` line 1530 — existing regime detection
- `market_regime.py` — regime classification system

---

*Prepared by Mino 🪼 | 2026-06-15 03:40 KSA | Updated 04:50 with regime-aware exits*

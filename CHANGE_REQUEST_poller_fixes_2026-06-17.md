# Change Request: Fix Poller — Missing calc_vwap_direction + Brainstorm Implementations

**Date:** 2026-06-17  
**Requester:** A A  
**File:** `poller.py`  
**Type:** ASK — Critical bug fix + feature implementation

---

## 1. Critical Bugs to Fix

### 1.1 Missing `calc_vwap_direction()` Function (CRITICAL)
- **Problem:** `fast_poll()` calls `calc_vwap_direction(df_pos, window=3)` at line 1350, but function doesn't exist
- **Impact:** Hard close crashes with `NameError` at 14:30, positions left open
- **Evidence:** Positions left open on June 16 due to this crash
- **Fix:** Add `calc_vwap_direction(df, window)` function that calculates VWAP trend direction

## 2. Brainstorm Features to Implement

### 2.1 Regime-Aware Entry Logic (from improvement_brainstorm_2026-06-15.md)
- **What:** In NEUTRAL/DEFENSIVE regime, only enter if VWAP is rising
- **Implementation:** Modify `check_entry_signals()` to check regime and VWAP direction
- **Risk:** May skip profitable trades, needs monitoring

### 2.2 Market Open Cooldown (10:00-10:15)
- **What:** No entries in first 15 minutes of market open
- **Why:** Wide spreads, algorithm noise, false signals
- **Implementation:** Add `MARKET_OPEN_COOLDOWN = 15` constant, skip entries before 10:15

### 2.3 1-Minute Candle Recovery Score
- **What:** Use 15 × 1-min candles instead of 5 × 5-min candles for recovery score
- **Why:** More accurate, catches rapid reversals
- **Implementation:** Build 1-min candles from ws_prices.jsonl

---

## 3. Implementation Order

| Priority | Change | Risk |
|----------|--------|------|
| **P0** | Add `calc_vwap_direction()` | Critical — fixes crash |
| **P1** | Add market open cooldown | Low — simple time check |
| **P2** | Add regime-aware entries | Medium — may skip trades |
| **P3** | Add 1-min recovery score | Medium — changes exit timing |

---

## 4. Testing Plan

1. Verify poller loads without errors
2. Test `calc_vwap_direction()` with sample data
3. Test hard close logic with mock positions
4. Test regime-aware entries in NEUTRAL/DEFENSIVE
5. Test market open cooldown at 10:00

---

**Approval required before implementation.**

## Changes Summary

### ADDED
- `calc_vwap_direction()` — calculates VWAP trend direction from DataFrame
- `check_market_open_cooldown()` — prevents entries before 10:15
- `get_regime_for_entry()` — regime-aware entry filtering

### MODIFIED
- `fast_poll()` — hard close uses `calc_vwap_direction()` (was broken)
- `check_entry_signals()` — added regime + VWAP direction checks
- `slow_poll()` — added market open cooldown check

---

*Prepared by Mino 🪼 | 2026-06-17 19:35 KSA*

# Change Request: Post-Market Analysis Enhancement

**Date:** 2026-06-14
**Time:** 23:48 KSA
**Requester:** A A
**Status:** Approved
**Priority:** HIGH

## Summary
Implement post-market analysis enhancements in priority order:
1. Trade Integration (read order_history.csv)
2. WS Fallback data source
3. Enhanced Pick Analysis

## Phase 1: Trade Integration (B)
**File:** `post_market.py`
**Changes:**
- Add `load_actual_trades()` function
- Add `analyze_actual_vs_ideal()` function
- Add `calculate_hold_time()` helper
- Integrate trade data into report generation

## Phase 2: WS Fallback (A)
**File:** `post_market.py`
**Changes:**
- Add `fetch_from_ws_frames()` function
- Update `fetch_one()` with retry + WS fallback
- Exponential backoff (5 attempts)

## Phase 3: Pick Analysis (C)
**File:** `post_market.py`
**Changes:**
- Add `analyze_picks_comprehensive()` function
- Add `simulate_exit_windows()` function
- Add `calculate_pnl_attribution()` function
- Enhanced gap analysis

## Files Modified
- `post_market.py` (main file)
- `relearning/` directory structure (created)

## Approved by: A A
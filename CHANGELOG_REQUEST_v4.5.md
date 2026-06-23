# Change Request: poller.py v4.5
## Regime-Aware Exits + Market Open Cooldown + Entry Filters
## Date: 2026-06-15
## Requester: A A (via Mino)
## Status: PENDING REVIEW

---

## Summary

Implement 7 phases of improvements to poller.py based on June 14 backtesting results.

---

## Changes Overview

| # | Feature | Current | Proposed | Risk |
|---|---------|---------|----------|------|
| 1 | Market Open Cooldown | Allow 10:00+ | Block 10:00-10:15 | LOW |
| 2 | VWAP Exit Control | Enabled (all) | Disabled in TRENDING | LOW |
| 3 | 1-Min Recovery | 5-min candles (25 min) | 1-min candles (15 min) | LOW |
| 4 | Regime-Aware Params | Fixed | Dynamic by regime | MEDIUM |
| 5 | Time/Trail Updates | Fixed | Dynamic by regime | MEDIUM |
| 6 | Entry VWAP Filter | No check | Require rising VWAP | MEDIUM |
| 7 | Adaptive Tiers | Fixed [2,5,10%] | Regime-aware | LOW |

---

## Corrected Values (Per A A Feedback)

| Parameter | TRENDING | NEUTRAL | DEFENSIVE |
|-----------|----------|---------|-----------|
| Entry Logic | Allow all | Require VWAP rise | Strictest (not block) |
| Profit Target | +2% | +1% | **+0.8%** |
| Trail | -3% peak | -2% entry | -1.5% entry |
| Hard Stop | -7% | -7% | **-5%** |
| Time Stop | None | Dynamic | Earlier |
| Min Hold | 15 min | 15 min | **10 min** |
| Tier 1 | +2% | +1% | **+0.8%** |
| Tier 2 | +5% | +3% | +2% |
| Tier 3 | +10% | +5% | +3% |

---

## Implementation Order

1. Phase 1: Market cooldown (LOW risk)
2. Phase 2: VWAP exit control (LOW)
3. Phase 3: 1-min recovery (LOW)
4. Phase 4: Regime params (MEDIUM)
5. Phase 5: Time/trail (MEDIUM)
6. Phase 6: Entry filter (MEDIUM)
7. Phase 7: Adaptive tiers (LOW)

---

## Files Modified

- `poller.py` (~180 lines changed)
- `config.json` (+7 feature flags)

---

## Testing Plan

- Backtest on June 14 data
- Paper trade for 1 week
- Monitor for 3 days after each phase

---

## Rollback

Each phase has independent feature flag. Set flag to False to disable.

---

## Approval Required

**A A must explicitly state "Do it" to proceed.**

---

*Prepared by Mino 🪼 | 2026-06-15*

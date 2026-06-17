# Change Proposal: Profit Targets by Regime

**Date:** 2026-06-18
**Requester:** A A
**File:** `market_regime.py`
**Type:** ASK — Parameter adjustment

---

## 1. Current vs Proposed

| Regime | Current | Proposed | Change |
|--------|---------|----------|--------|
| **TRENDING** | +2.5% | **+2.0%** | -0.5% |
| **NEUTRAL** | +2.0% | **+1.2%** | -0.8% |
| **DEFENSIVE** | +1.5% | **+0.8%** | -0.7% |

**Trail Trigger also updated to match:**
| Regime | Current | Proposed | Change |
|--------|---------|----------|--------|
| **TRENDING** | +2.5% | **+2.0%** | -0.5% |
| **NEUTRAL** | +2.0% | **+1.2%** | -0.8% |
| **DEFENSIVE** | +1.5% | **+0.8%** | -0.7% |

---

## 2. Reasoning

**TRENDING (currently +2.5% → proposed +2.0%):**
- In trending markets, momentum can reverse quickly
- Lower target = faster realization of gains
- Prevents giving back profits on reversal

**NEUTRAL (currently +2.0% → proposed +1.0%):**
- In neutral/choppy markets, +2% is unrealistic
- +1% is achievable before price reverses
- Matches backtest results (June 14 improvement)

**DEFENSIVE (currently +1.5% → proposed +0.5%):**
- In defensive markets, quick in-and-out trades
- +0.5% = small but consistent profits
- Avoids holding through further decline

---

## 3. Impact

| Scenario | Current | Proposed | Benefit |
|----------|---------|----------|---------|
| TRENDING run +2.0% | Miss target, hold | **Sell at +2.0%** | Lock gains |
| NEUTRAL bounces 1.5% | Miss +2%, reverse | **Sell at +1.0%** | Realize profit |
| DEFENSIVE spikes 0.8% | Miss +1.5%, drop | **Sell at +0.5%** | Escape before drop |

---

## 4. Implementation

```python
# In market_regime.py, REGIME_PARAMS:

"TRENDING": {
    "target_pct": 0.02,      # Was 0.025, now 0.02
    "trail_trigger": 0.02,   # Was 0.025, now 0.02 (matches target)
    ...
},
"NEUTRAL": {
    "target_pct": 0.012,     # Was 0.02, now 0.012
    "trail_trigger": 0.012,  # Was 0.02, now 0.012 (matches target)
    ...
},
"DEFENSIVE": {
    "target_pct": 0.008,     # Was 0.015, now 0.008
    "trail_trigger": 0.008, # Was 0.015, now 0.008 (matches target)
    ...
}
```

---

## 5. Risks

| Risk | Mitigation |
|------|-----------|
| Too conservative in TRENDING | Trail trigger still at +2.5%, can catch runners |
| Miss big moves in NEUTRAL | Tiered exits: partial at +1%, rest with trail |
| Transaction costs on small profits | Only applied in DEFENSIVE (rare regime) |

---

**Approval required.**

*Prepared by Mino 🪼 | 2026-06-18*
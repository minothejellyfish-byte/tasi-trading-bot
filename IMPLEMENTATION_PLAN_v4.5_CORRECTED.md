# Implementation Plan v4.5 — CORRECTED
## Regime-Aware Exits + Market Open Cooldown + Entry Filters
## Date: 2026-06-15
## Status: ASK — Awaits explicit "Do it"

---

## CORRECTIONS APPLIED

### 1. DEFENSIVE Entry Logic
- **Was:** DEF = block entries
- **Now:** DEF = strictest conditions (VWAP rising + recovery > 0.70 + no gaps)

### 2. DEFENSIVE Profit Target
- **Was:** +0.5%
- **Now:** **+0.8%** (covers fees + small profit)

### 3. Tier System Integration
- **All regimes use tiers**, but thresholds adapt:
  - TRENDING: [+2%, +5%, +10%]
  - NEUTRAL: [+1%, +3%, +5%]
  - DEFENSIVE: [**+0.8%**, +2%, +3%]

---

## CORRECTED Change Table

| # | Feature | Current (v4.4) | Proposed (v4.5) | Impact | Risk |
|---|---------|---------------|-----------------|--------|------|
| 1 | **Market Open** | Allow entries 10:00+ | **Block 10:00-10:15** | Skip noisy open | LOW |
| 2 | **Entry VWAP Filter** | No check | **Require VWAP rising** (NEUTRAL/DEF) | Better timing | MEDIUM |
| 3 | **Entry Regime** | Same all | **TRENDING=allow all, NEUTRAL=require VWAP rise, DEF=strictest** | Adaptive entries | MEDIUM |
| 4 | **Recovery Score** | 5-min candles (25 min) | **1-min candles (15 min)** | Faster, more accurate | LOW |
| 5 | **Exit Time Stops** | Fixed 30 min, -1% | **Dynamic by regime and entry time** | Regime-aware exits | MEDIUM |
| 6 | **Trailing Stop** | -3% from peak (all) | **TRENDING=-3% peak, NEUTRAL=-2% entry, DEF=-1.5% entry** | Tighter in chop | MEDIUM |
| 7 | **Profit Target** | +2% (all) | **TRENDING=+2%, NEUTRAL=+1%, DEF=+0.8%** | Quick profits in DEF | LOW |
| 8 | **VWAP Exits** | Enabled (all) | **Disabled in TRENDING** | Let trends run | LOW |
| 9 | **Hard Stop** | -7% (all) | **TRENDING=-7%, NEUTRAL=-7%, DEF=-5%** | Tighter in DEF | LOW |
| 10 | **Min Hold** | 15 min (all) | **TRENDING=15, NEUTRAL=15, DEF=10** | Faster exits in DEF | LOW |
| 11 | **Tier System** | Fixed [+2%, +5%, +10%] | **Adaptive: TRENDING=[2,5,10], NEUTRAL=[1,3,5], DEF=[0.8,2,3]** | Regime-aware tiers | LOW |

---

## Implementation Phases (By Priority)

### PHASE 1: Market Open Cooldown (LOW RISK) ✅ FIRST
**Why first:** Simplest change, immediate impact, no logic dependencies

**Changes:**
- Add `MARKET_OPEN_COOLDOWN_MINS = 15` constant
- Add time check in `slow_poll()` entry loop

**Testing:** 1 day
**Rollback:** Remove 3 lines of code

---

### PHASE 2: VWAP Exit Control (LOW RISK)
**Why second:** Simple boolean check, low risk

**Changes:**
- Disable VWAP exits when regime = TRENDING
- Keep enabled for NEUTRAL/DEFENSIVE

**Testing:** 1-2 days
**Rollback:** Change one boolean

---

### PHASE 3: 1-Minute Recovery Score (LOW RISK)
**Why third:** Self-contained, doesn't affect other logic

**Changes:**
- Add `calculate_recovery_1min()` function
- Replace 5-min recovery with 1-min in VWAP breakdown logic

**Testing:** Compare accuracy vs 5-min (backtest)
**Rollback:** Revert to 5-min tail()

---

### PHASE 4: Regime-Aware Exit Parameters (MEDIUM RISK)
**Why fourth:** Core change, affects multiple exit types

**Changes:**
- Add `get_regime_exit_params()` function
- Modify exit logic to use dynamic params
- Add `get_time_stop()` for dynamic time stops

**Testing:** 2-3 days, verify per regime
**Rollback:** Revert to fixed params

---

### PHASE 5: Time Stop & Trail Update (MEDIUM RISK)
**Why fifth:** Depends on Phase 4 params

**Changes:**
- Replace fixed time stop with regime-aware
- TRENDING: No time stop
- NEUTRAL: Dynamic based on entry time
- DEFENSIVE: Earlier time stops
- Trail: TRENDING from peak, others from entry

**Testing:** 2-3 days
**Rollback:** Revert to fixed 30-min time stop

---

### PHASE 6: Entry VWAP Direction Filter (MEDIUM RISK)
**Why sixth:** Affects entry signals, may skip profitable trades

**Changes:**
- Add `get_vwap_direction()` function
- Skip entries if VWAP falling (NEUTRAL/DEF)
- TRENDING: Allow all entries

**Testing:** 3-5 days, monitor skipped entries
**Rollback:** Comment out filter

---

### PHASE 7: Tier System Integration (LOW RISK)
**Why last:** Depends on all other phases

**Changes:**
- Add regime-aware tier levels
- Modify tier logic to use adaptive thresholds

**Testing:** 2-3 days
**Rollback:** Revert to fixed tiers

---

## Phase Dependencies

```
Phase 1 (Cooldown) ──→ Phase 6 (Entry Filter)
     │
     ↓
Phase 2 (VWAP Exit) ──→ Phase 4 (Regime Params)
     │                      │
     ↓                      ↓
Phase 3 (1-min Recovery)    Phase 5 (Time/Trail)
                                │
                                ↓
                           Phase 7 (Tiers)
```

**Rule:** Each phase can be deployed independently, but Phase 7 should only deploy after Phases 1-6 are stable.

---

## Testing Gates

| Phase | Before Deploy | After Deploy | Duration |
|-------|--------------|-------------|----------|
| 1 | Verify no 10:00-10:15 entries | Monitor for 1 day | 1 day |
| 2 | Verify VWAP exits disabled in TRENDING | Monitor regime switches | 2 days |
| 3 | Backtest vs 5-min recovery | Monitor recovery accuracy | 3 days |
| 4 | Unit test per regime | Monitor exits per regime | 3 days |
| 5 | Backtest time stops | Monitor exit timing | 3 days |
| 6 | Backtest on June 14 | Monitor skipped entries | 5 days |
| 7 | Verify tier thresholds | Monitor tier execution | 2 days |

---

## Rollback Plan

Each phase has independent rollback:

```python
# Feature flags for rollback
ENABLE_COOLDOWN = True        # Phase 1
ENABLE_VWAP_EXIT_CONTROL = True  # Phase 2
ENABLE_1MIN_RECOVERY = True   # Phase 3
ENABLE_REGIME_PARAMS = True   # Phase 4
ENABLE_TIME_TRAIL = True      # Phase 5
ENABLE_ENTRY_FILTER = True    # Phase 6
ENABLE_ADAPTIVE_TIERS = True  # Phase 7
```

If issues: Set flag to `False`, restart poller.

---

## CORRECTED Implementation Order

| Order | Phase | Feature | Risk | Duration | Deploy After |
|-------|-------|---------|------|----------|-------------|
| 1 | Phase 1 | Market open cooldown | LOW | 1 day | Immediate |
| 2 | Phase 2 | VWAP exit control | LOW | 1-2 days | Phase 1 stable |
| 3 | Phase 3 | 1-min recovery | LOW | 2-3 days | Phase 2 stable |
| 4 | Phase 4 | Regime-aware params | MEDIUM | 2-3 days | Phase 3 stable |
| 5 | Phase 5 | Time stop & trail | MEDIUM | 2-3 days | Phase 4 stable |
| 6 | Phase 6 | Entry VWAP filter | MEDIUM | 3-5 days | Phase 5 stable |
| 7 | Phase 7 | Adaptive tiers | LOW | 2-3 days | Phase 6 stable |

**Total estimated time:** 2-3 weeks (with testing)

---

## Files Modified

| File | Lines | Purpose |
|------|-------|---------|
| `poller.py` | ~180 | Main logic |
| `market_regime.py` | 0 | No changes |
| `config.json` | +7 flags | Feature toggles |

---

*CORRECTED by A A feedback | 2026-06-15 05:40*
*Prepared by Mino 🪼*

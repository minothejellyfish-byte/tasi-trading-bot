# Change Request: Position Upgrade Should Use Full Entry Logic

**Date:** 2026-06-18
**Time:** 11:51 KSA
**Requester:** A A
**File:** `poller.py` (ASK tier — requires explicit approval)
**Priority:** CRITICAL
**Status:** Proposed — awaiting explicit approval

---

## Summary

Position upgrade logic is **COMPLETELY SEPARATE** from entry logic. It should use the SAME entry criteria (VWAP direction, regime filter, zone check, gap-up timing) before approving an upgrade. Currently it only checks score ratio + broken zone check, causing invalid upgrades.

**Today's Result:** 3 positions sold (1180, 2160, 4017), 0 replacements bought, portfolio empty.

---

## Current vs Expected Behavior

### Current Position Upgrade Logic (simplified)
```python
# Position upgrade section (~line 2145)
for current_sym in current_symbols:
    current_score = ...
    best_new = find_best_new_pick()
    pu_thresh = POSITION_UPGRADE_THRESHOLDS[regime_name]  # 1.2-1.4
    
    if best_new.score > current_score * pu_thresh:
        # BROKEN: Uses wrong field name for zone check
        bn_zone = best_new.get("entry_zone", None)  # ← ALWAYS None
        if bn_zone:  # ← NEVER executes
            # zone check code (dead code)
            
        # No VWAP check
        # No regime filter
        # No entry criteria validation
        auto_sell(current_sym, qty, "Position upgrade")
        # No auto_buy()
        # Assumes next iteration will trigger entry
```

### Expected Behavior
Position upgrade should be a **WRAPPER** around entry logic:
```
Position upgrade triggered:
├── 1. Score check: best_new.score > current_score * pu_thresh?
├── 2. FULL ENTRY VALIDATION:
│   ├── Regime check (NEUTRAL/DEFENSIVE require rising VWAP)
│   ├── VWAP direction check (vwap_dir > 0)
│   ├── Zone check: price in [entry_low, entry_high]?
│   ├── Gap-up window check (only before 10:30)
│   ├── Max positions check (have slot?)
│   └── Cash check (have capital?)
├── 3. If ALL pass:
│   ├── auto_sell(current_sym)  — sell current position
│   └── auto_buy(best_new)      — buy new position (GUARANTEED)
└── 4. If ANY fail:
    └── BLOCK upgrade, keep current position
```

---

## Problems with Current Implementation

### Problem 1: No VWAP Regime Check
Entry logic checks `vwap_dir > 0` for NEUTRAL/DEFENSIVE. Position upgrade does NOT.
- **Entry logic:**
  ```python
  if regime_name in ["NEUTRAL", "DEFENSIVE"]:
      vwap_dir = calc_vwap_direction(df, window=5)
      if vwap_dir <= 0:
          log.info(f"{base} skipped - VWAP falling in {regime_name}")
          continue  # BLOCK entry
  ```
- **Position upgrade:** No equivalent check
- **Risk:** Could upgrade into falling momentum (buying declining stock)

### Problem 2: Broken Zone Check (Field Name Mismatch)
- Code: `best_new.get("entry_zone", None)`
- Data: Has `entry_low`/`entry_high` at ROOT level
- Result: Zone check ALWAYS skipped
- Risk: Upgrades approved for out-of-zone picks

### Problem 3: No auto_buy() Guarantee
- Only `auto_sell()` called
- `auto_buy()` left to "next iteration"
- Next iteration may not trigger (no VWAP reclaim, not in gap-up window)
- Result: Empty portfolio after upgrade

### Problem 4: No Cash/Position Evaluation
- Doesn't check if we have capital for new position
- Doesn't check if target pick is already being bought in another upgrade
- Today: All 3 sold to buy 4019 — if it had worked, 100% in one pick

### Problem 5: No Entry Timing Validation
- Entry logic checks: cooldown (10:00-10:10), hard close (after 14:45), stand_down
- Position upgrade: None of these checks
- Risk: Could upgrade during cooldown or after hard close

---

## Why It Sold All 3 Positions Today

```
11:23:45 — 1180 (score=62) vs 4019 (score=153)
  Score check: 153 > 62 * 1.3 = 80.6? YES → Continue
  VWAP check: MISSING → Skip
  Zone check: BROKEN (entry_zone=None) → Skip
  Cash check: MISSING → Skip
  auto_sell(1180) → SOLD
  auto_buy(4019): NOT CALLED

11:23:47 — 2160 (score=66) vs 4019 (score=153)  
  Same checks: All pass (or skipped)
  auto_sell(2160) → SOLD
  auto_buy(4019): NOT CALLED

11:23:53 — 4017 (score=87) vs 4019 (score=153)
  Same checks: All pass (or skipped)
  auto_sell(4017) → SOLD
  auto_buy(4019): NOT CALLED

Result: 3 sells, 0 buys, portfolio empty
4019 price 17.63 < entry zone 18.01 (would have been blocked by working zone check)
```

---

## Proposed Fix

### Option A: Extract Entry Validation to Reusable Function
**Best approach** — DRY principle, single source of truth.

```python
def validate_entry_conditions(symbol, price, df, ws_vwap, regime_name, 
                              picks_data, open_count, max_positions,
                              check_type="entry"):
    """
    Validate ALL entry conditions for a symbol.
    
    Used by BOTH:
    - Normal entry logic (gap-up, VWAP reclaim)
    - Position upgrade logic (switching to better pick)
    
    check_type: "entry" or "upgrade" — for logging only
    """
    base = symbol.replace(".SR", "")
    
    # 1. Basic checks (both entry and upgrade)
    if not price or not df:
        return False, "No price/data"
    
    # 2. Market timing checks
    now_time = datetime.now(RIYADH).time()
    if now_time < time(10, 10):
        return False, "Market open cooldown"
    if now_time >= HARD_CLOSE_TIME:
        return False, "Hard close active"
    
    # 3. Regime + VWAP direction check
    if regime_name in ["NEUTRAL", "DEFENSIVE"]:
        vwap_dir = calc_vwap_direction(df, window=5)
        if vwap_dir <= 0:
            return False, f"VWAP falling in {regime_name}"
    
    # 4. Zone check (FIXED: use entry_low/entry_high from root)
    pick_data = picks_data.get(symbol, {})
    e_lo = pick_data.get("entry_low", 0)
    e_hi = pick_data.get("entry_high", 0)
    if e_lo and e_hi:
        if price < e_lo or price > e_hi:
            return False, f"Outside zone [{e_lo:.2f}-{e_hi:.2f}]"
    
    # 5. Position limit check
    if open_count >= max_positions:
        return False, "Max positions reached"
    
    # 6. Capital check
    # (existing capital check logic)
    
    return True, "All conditions pass"
```

Then in position upgrade:
```python
# Position upgrade logic
if best_new and best_new.get("score", 0) > current_score * pu_thresh:
    bn_price, bn_df, _, bn_vwap = fetch_data(best_new['symbol'])
    
    # USE SAME VALIDATION AS ENTRY
    can_enter, reason = validate_entry_conditions(
        best_new['symbol'], bn_price, bn_df, bn_vwap,
        regime_name, picks_all, open_count, max_positions,
        check_type="upgrade"
    )
    
    if not can_enter:
        log.info(f"Position upgrade BLOCKED for {best_new['symbol']}: {reason}")
        continue  # Skip this upgrade
    
    # All checks passed — proceed with guaranteed upgrade
    auto_sell(current_sym, qty, "Position upgrade")
    auto_buy(best_new['symbol'], qty, price=bn_price,
             trigger=TRIGGER_POSITION_UPGRADE,
             trigger_detail=f"Upgrade from {current_sym}")
    open_count += 1
```

### Option B: Call Existing Entry Logic Directly
Reuse the existing entry evaluation loop for position upgrade targets.

**Recommended: Option A** — Cleaner, explicit, maintainable.

---

## Additional Requirements

1. **Zone check field fix:** Use `entry_low`/`entry_high` instead of `entry_zone`
2. **auto_buy() guarantee:** Call immediately after auto_sell()
3. **Deduplication:** Track which picks are already being upgraded to prevent multiple sells targeting same pick
4. **Cash evaluation:** Check available capital before approving upgrade

---

## Verification After Fix

1. Position upgrade triggers for 1180 → 4019
2. Score check: 153 > 62 * 1.3 = 80.6 → PASS
3. `validate_entry_conditions(4019)`:
   - Timing: 11:23 > 10:10 → PASS
   - Regime: TRENDING → VWAP check SKIPPED → PASS
   - Zone: 17.63 in [18.01-18.01]? → NO → **BLOCKED**
4. Log: "Position upgrade BLOCKED for 4019: Outside zone [18.01-18.01]"
5. 1180 NOT sold
6. Position preserved

Later when 4019 enters zone:
1. 4019 price rises to 18.01
2. Zone check: 18.01 in [18.01-18.01] → PASS
3. auto_sell(1180) → sells
4. auto_buy(4019) → buys immediately
5. Portfolio properly switched

---

## Files to Backup Before Change
- `poller.py` → `poller.py.backup.entry_logic_unify_2026-06-18`

---

## Approval Required

**This is an ASK tier file.** Mino will NOT apply this change without explicit approval from A A.

Reply with **"Apply position upgrade entry logic unification fix"** to authorize.

Or reply with questions/concerns.

---

*Generated by Mino as per .ASK_REQUIRED change control procedure.*
*Investigation: Position upgrade should use same validation as normal entry logic.*

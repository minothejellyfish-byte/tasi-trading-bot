# Change Request: Position Upgrade Zone Check Broken + Missing auto_buy()

**Date:** 2026-06-18
**Time:** 11:46 KSA
**Requester:** A A
**File:** `poller.py` (ASK tier — requires explicit approval)
**Priority:** CRITICAL
**Status:** Proposed — awaiting explicit approval

---

## Summary

Position upgrade logic has **TWO bugs**:
1. **Zone check is BROKEN** — Code looks for `entry_zone` object, but data has `entry_low`/`entry_high` at root level. Zone check silently skipped → upgrades happen even when new pick is OUTSIDE zone.
2. **No auto_buy() after auto_sell()** — Sold positions never get replaced because entry criteria (VWAP reclaim/gap-up) may not be met in next iteration.

**Today's Result:** 3 positions sold (1180, 2160, 4017), 0 replacements bought, portfolio empty.

---

## Bug 1: Zone Check Broken (CRITICAL)

### Current Code (line ~2163-2177)
```python
bn_zone = best_new.get("entry_zone", None)  # ← WRONG FIELD NAME

# CRITICAL: Only upgrade if new pick is IN ENTRY ZONE
if bn_zone:  # ← ALWAYS FALSE because entry_zone doesn't exist
    e_lo = bn_zone.get("e_lo", 0)
    e_hi = bn_zone.get("e_hi", float('inf'))
    if bn_price and (bn_price < e_lo or bn_price > e_hi):
        log.info(f"Position upgrade BLOCKED: ...")
        continue  # Skip this upgrade
```

### Data Structure (from picks.json)
```json
{
  "ticker": "4019.SR",
  "entry_low": 18.01,   // ← Field exists at ROOT level
  "entry_high": 18.01,  // ← Field exists at ROOT level
  "score": 152.6
  // NO "entry_zone" object anywhere
}
```

### What Happened Today
- 4019 price at 11:23: **17.63** (below zone)
- 4019 entry zone: **18.01-18.01** 
- Zone check result: **SKIPPED** (because `entry_zone` is None)
- Upgrade: **APPROVED** (should have been BLOCKED)
- All 3 positions sold to buy 4019, but 4019 was **outside zone**

### Root Cause
Data structure mismatch. Code written for old format (`entry_zone` object), but screener outputs new format (`entry_low`/`entry_high` at root).

### Fix for Bug 1
```python
# CORRECT: Read entry_low/entry_high from root level
e_lo = best_new.get("entry_low", 0)
e_hi = best_new.get("entry_high", 0)

if e_lo and e_hi and bn_price:
    if bn_price < e_lo or bn_price > e_hi:
        log.info(f"Position upgrade BLOCKED: {best_new['symbol']} score is better but OUTSIDE zone [{e_lo:.2f}-{e_hi:.2f}], price={bn_price:.2f}")
        continue  # Skip this upgrade — new pick not in zone
```

---

## Bug 2: Missing auto_buy() After auto_sell()

### Current Code (line ~2196-2203)
```python
auto_sell(current_sym, qty, 
          f"🔄 Position upgrade — switching to better momentum pick")
open_count -= 1
# Clear alert for new pick so it can trigger immediately
best_sym = best_new["symbol"].replace(".SR", "")
_reset_symbol_alerts(best_sym)
# Will trigger entry in next iteration  // ← ASSUMPTION FAILS
```

### Why It Fails
After selling, the code assumes next slow poll will trigger entry via:
- Gap-up entry (only works before 10:30)
- VWAP reclaim (requires price crossing above VWAP)

But if neither criteria is met, **no buy happens**. Portfolio stays empty.

### Fix for Bug 2
```python
auto_sell(current_sym, qty, 
          f"🔄 Position upgrade — switching to better momentum pick")

# BUG FIX: Immediately buy the new pick (zone check already passed)
if bn_price and e_lo and e_hi and e_lo <= bn_price <= e_hi:
    auto_buy(best_new['symbol'], qty, price=bn_price,
             trigger=TRIGGER_POSITION_UPGRADE,
             trigger_detail=f"Position upgrade from {current_sym}")
    open_count += 1  # Track new position
else:
    log.warning(f"Position upgrade: Sold {current_sym} but new pick {best_new['symbol']} no longer in zone, skipping buy")
```

---

## Combined Fix

Both bugs must be fixed together:
1. **Fix zone check** → Only upgrade when new pick is actually in zone
2. **Add auto_buy()** → Immediately buy after sell (with zone re-check)

### Why Zone Check Must Be Fixed FIRST
- Without zone check fix: Upgrade happens for out-of-zone picks → sells position, tries to buy, price is bad → loses money
- With zone check fix: Upgrade only happens for in-zone picks → sells position, buys new pick at good price → proper momentum switch

---

## Evidence from Today's Log

```
11:23:45 — Position upgrade: 1180(score=62) → 4019.SR(score=153). P&L: -0.0%
11:23:46 — SELL 4×1180 MKT
11:23:47 — Position upgrade: 2160(score=66) → 4019.SR(score=153). P&L: +0.0%
11:23:48 — SELL 14×2160 MKT
11:23:53 — Position upgrade: 4017(score=87) → 4019.SR(score=153). P&L: -0.2%
11:23:53 — SELL 3×4017 MKT

NO BUY orders for 4019.
4019 price 17.63 < entry zone 18.01-18.01 (OUTSIDE ZONE)
```

---

## Why It Sold All 3 Positions

Because the zone check was skipped for ALL three evaluations:
1. 1180 (score=62) → 4019 (score=153): Zone check SKIPPED → Upgrade approved → SELL 1180
2. 2160 (score=66) → 4019 (score=153): Zone check SKIPPED → Upgrade approved → SELL 2160  
3. 4017 (score=87) → 4019 (score=153): Zone check SKIPPED → Upgrade approved → SELL 4017

All three sold to buy the SAME pick (4019), but 4019 was out of zone so no buy happened.

---

## Additional Issues Found

### Cash/Momentum Evaluation Missing
The current code does NOT check:
- Available capital before buying
- Whether we have enough cash for the new position
- Portfolio concentration risk (all into one pick)

### Multiple Upgrades to Same Pick
All 3 positions sold to buy 4019. If upgrade had worked, we'd have 100% in one pick. Code should check if target pick is already being bought in another upgrade.

---

## Verification After Fix

1. Position upgrade triggers for 1180 → 4019
2. Zone check reads `entry_low`/`entry_high` correctly
3. 4019 at 17.63, zone 18.01-18.01 → OUTSIDE ZONE → BLOCKED
4. 1180 NOT sold
5. Position preserved, no loss realized

When 4019 later enters zone (e.g., price rises to 18.01):
1. Position upgrade triggers again
2. Zone check: 18.01 in [18.01-18.01] → IN ZONE → APPROVED
3. auto_sell(1180) → sells current position
4. auto_buy(4019) → buys new position immediately
5. Portfolio switched to better momentum pick

---

## Files to Backup Before Change
- `poller.py` → `poller.py.backup.zone_check_fix_2026-06-18`

---

## Approval Required

**This is an ASK tier file.** Mino will NOT apply this change without explicit approval from A A.

Reply with **"Apply position upgrade zone check + auto_buy fix"** to authorize.

Or reply with questions/concerns.

---

*Generated by Mino as per .ASK_REQUIRED change control procedure.*
*Investigation updated 11:46 KSA with corrected root cause (zone check field name mismatch).*

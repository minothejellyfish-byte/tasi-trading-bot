# Change Request: Bookkeeper Order Matching Logic

**Date:** 2026-06-14
**Time:** 14:16 KSA
**Requester:** A A
**File:** `bookkeeper.py` (ASK tier — requires explicit approval)
**Priority:** HIGH
**Status:** Proposed

---

## Summary

The bookkeeper's `reconcile_orders()` function incorrectly marks orders as REJECTED when they were actually placed but returned an invalid order ID (`?`) from Derayah API.

---

## Problem Description

### Current Behavior
1. Poller places order via Derayah API
2. API returns `isSuccess: True` but `orderId: "?"` (malformed response)
3. Poller writes INITIATED order to `orders.json` with `order_id="?"`
4. Bookkeeper reconcile runs (every 5 min via cron)
5. Bookkeeper looks for order `?` in Derayah API — not found by ID
6. Bookkeeper **immediately marks as REJECTED** without checking for matching orders
7. Telegram message sent: "Order ? REJECTED — Not seen in Derayah after 1 cycle (5 min)"

### Expected Behavior
1. Same steps 1-5 as above
2. Bookkeeper should search Derayah API for matching FILLED orders:
   - Same `symbol`
   - Same `side`
   - Same `qty`
   - Same `price`
   - Within **±5 minute time window** of initiated_at
3. If match found → update local order to FILLED with real order ID
4. If no match after reasonable window → mark REJECTED

---

## Root Cause

In `bookkeeper.py`, section 4b (lines ~619-637):

```python
elif local_o.get("status") == STATUS_INITIATED:
    # INITIATED but Derayah doesn't see it → mark REJECTED
    new_orders[oid] = {**local_o, "status": STATUS_REJECTED, "updated_at": _now()}
```

**No fuzzy matching logic exists.** The code only checks by exact `order_id`, not by order attributes.

---

## Proposed Fix

### Option A: Add Matching Logic (Recommended)

In section 4b, before marking REJECTED:

```python
elif local_o.get("status") == STATUS_INITIATED:
    # Check if there's a matching FILLED order in API
    # Match by: symbol, side, qty, price, AND time window (±5 min)
    match = find_matching_filled_order(local_o, api_order_map)
    if match:
        # Update local order with real order ID and FILLED status
        new_orders[match['order_id']] = {
            **local_o,
            "status": STATUS_FILLED,
            "updated_at": _now(),
            "matched_from_api": True
        }
        transitions["status_changes"].append({
            "order_id": match['order_id'],
            "old": STATUS_INITIATED,
            "new": STATUS_FILLED,
            "symbol": local_o.get("symbol"),
            "side": local_o.get("side"),
            "qty": local_o.get("qty"),
            "price": local_o.get("price"),
        })
    else:
        # No match found → mark REJECTED
        new_orders[oid] = {**local_o, "status": STATUS_REJECTED, "updated_at": _now()}
        transitions["initiated_to_rejected"].append({...})
```

### Option B: Poller Validation

Add validation in poller to check if `orderId == "?"` and treat as failure:

```python
if resp.get("isSuccess"):
    order_id = (resp.get("data") or {}).get("orderId", "?")
    if order_id == "?":
        log.error("API returned isSuccess=True but orderId is invalid")
        return {"success": False, "message": "API returned invalid orderId"}
```

**However:** Per A A's instruction, poller should write and bookkeeper should validate/over write. So Option A is preferred.

---

## Evidence

### Example: Order 5110 at 13:22

**Local orders.json:**
- Order `?`: 5110 BUY 12 @ 17.04 (INITIATED at 13:22:07)

**Derayah API has matching FILLED order:**
- Order 65: 5110 BUY 12 @ 17.04 (FILLED)

**Current result:** Order `?` marked REJECTED
**Expected result:** Order `?` updated to match Order 65 (FILLED)

---

## Testing Plan

1. Add matching logic to bookkeeper.py
2. Restart tasi-bot.service
3. Monitor next auto_buy cycle
4. Verify orders with `?` are matched correctly
5. Check Telegram notifications show correct status

---

## Impact

- **Risk:** Low (only affects edge case of malformed orderId)
- **Benefit:** High (prevents false REJECTED notifications, accurate order tracking)
- **Files modified:** `bookkeeper.py` only
- **Backwards compatible:** Yes

---

## Awaiting Approval

**A A — Please review and approve/reject this change request.**
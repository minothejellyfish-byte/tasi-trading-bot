# Bookkeeper Fix Summary

## Problems Identified:
1. **Price mismatch**: MARKET orders (price=0.0) don't match child orders (price=22.74)
2. **Single child matching**: Only finds first match (due to `break` statement)
3. **trigger_basis bug**: `.get("trigger_basis") or "unknown"` returns "unknown" for empty string
4. **Timestamp issue**: Child orders have 00:00:00 (date-only from Derayah)
5. **Parent not removed**: Parent "?" stays after matching

## What I've Fixed Already:
1. ✅ Price tolerance: Changed line 649 to `(api_data["price"] == local_price or local_price == 0.0)`
2. ✅ trigger_basis bug: Changed lines 598, 672 to `.get("trigger_basis", "unknown")`

## What Still Needs Fixing (Your Requirements):
1. **Find ALL child orders** that sum to parent qty (remove `break`, track multiple matches)
2. **Check for 00:00:00 timestamp** in child orders
3. **Check for unknown/empty trigger_basis** in child orders  
4. **Copy parent trigger_basis** to matched children
5. **Use parent timestamp** instead of child's 00:00:00
6. **Strike parent order** after matching (remove from new_orders)

## Implementation Plan:
1. Change logic to collect ALL matching children (remove `break`)
2. Track matched_qty to ensure total matches parent qty
3. Only match children with empty/unknown trigger_basis
4. Copy parent metadata to ALL matched children
5. Don't add parent "?" to new_orders after successful match

## Code Changes Needed:

### 1. Remove `break` statements (lines 661, 665)
### 2. Change to collect multiple matches:
```python
matched_children = []
for api_oid, api_data in api_order_map.items():
    if matches(parent, child):
        matched_children.append((api_oid, api_data))
        if sum(child.qty for child in matched_children) == parent_qty:
            # Found all children
            break
```

### 3. Check trigger_basis:
```python
if child.get("trigger_basis") in ["", "unknown", None]:
    # Only match children without trigger_basis
```

### 4. Copy parent metadata:
```python
for child_oid, child_data in matched_children:
    new_orders[child_oid] = {
        "initiated_at": parent.get("initiated_at"),  # Use parent timestamp
        "trigger_basis": parent.get("trigger_basis", "unknown"),
        # ... other parent metadata
    }
```

### 5. Skip parent "?":
```python
if matched_children:
    # Process children
    continue  # Skip adding parent to new_orders
else:
    # No match → mark REJECTED
    new_orders[oid] = {**local_o, "status": STATUS_REJECTED, ...}
```


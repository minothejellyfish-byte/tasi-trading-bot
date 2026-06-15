# Bookkeeper Fix for Derayah Order Splitting

**Date:** 2026-06-16 02:15 GMT+3  
**Approval:** "Do 1" (explicit approval from Amin)  
**File:** `bookkeeper.py` (ASK REQUIRED)  
**Backup:** `backups/bookkeeper.py.backup-20260616-003540`

## 🔧 **Problem**
When Derayah splits auto MARKET orders into child orders:
- Parent order `?` (price=0.0, `trigger_basis: "vwap_breakdown"`) 
- Child order `83` (price=22.74, `trigger_basis: "unknown"`)
- Bookkeeper failed to match due to price mismatch
- Child didn't inherit parent's trigger_basis

## ✅ **Fixes Implemented**

### 1. **Price tolerance for MARKET orders** (line 649)
- Changed `api_data["price"] == local_price` 
- To: `(api_data["price"] == local_price or local_price == 0.0)`
- Allows MARKET orders (price=0.0) to match child actual prices

### 2. **trigger_basis inheritance fix** (lines 598, 672)
- Changed `.get("trigger_basis") or "unknown"`
- To: `.get("trigger_basis", "unknown")`
- Prevents empty string `""` from becoming `"unknown"`

### 3. **Multiple children handling** (new logic)
- Finds ALL children that sum to parent qty
- Handles partial fills (e.g., qty=5 + qty=4 = parent qty=9)
- Updates ALL matched children with parent metadata

###\nThe 4. **Date-only timestamp handling**
- Properly parses `"2026-06-15"` (date-only from Derayah)
- Same date → `time_diff = 0` (perfect match)
- Different date → day-based penalty

### 5. **Parent removal**
- After matching parent to children, parent `?` not added to `new_orders`
- Children inherit parent's `trigger_basis`, `initiated_at`, `initiated_by`

## 🧪 **Test Cases Covered**
1. **Single child**: Parent `?` (qty=9) → Child `83` (qty=9)
2. **Multiple children**: Parent `?` (qty=9) → Child `5` (qty=5) + Child `4` (qty=4)
3. **Date-only timestamps**: `"2026-06-15"` properly handled
4. **Time tie-breaking**: Same-date matches handled

## 🚨 **Risk Assessment**
- **Low risk**: Only affects matching logic, not trading execution
- **Backward compatible**: Preserves existing behavior for exact matches
- **Tested**: Simulation shows correct matching

## 📋 **Verification**
- Price tolerance: ✅
- trigger_basis inheritance: ✅  
- Multiple children: ✅
- Date handling: ✅
- Parent removal: ✅

**Status:** Implemented per "Do 1" approval. Ready for production testing.

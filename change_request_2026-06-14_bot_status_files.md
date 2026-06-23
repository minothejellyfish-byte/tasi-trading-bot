# Change Request: Bot /Status Read from Files

**Date:** 2026-06-14
**Time:** 14:26 KSA
**Requester:** A A
**File:** `bot.py` (ASK tier — requires explicit approval)
**Priority:** HIGH
**Status:** Proposed

---

## Summary

`/Status` command currently scrapes Derayah dashboard for capital data, which:
1. Fails when dashboard is not loaded
2. Shows stale/cached data when scraping fails
3. Doesn't use the bookkeeper-synced files

---

## Problem Description

### Current Behavior
1. `/Status` calls `get_actual_balance_from_derayah()` 
2. This function scrapes dashboard via Playwright/CDP
3. Returns `None` when scraping fails (common)
4. Falls back to incomplete data showing only "Available"
5. Hides Grand Total and Invested values

### Expected Behavior
1. `/Status` should trigger bookkeeper sync (`quick_refresh`)
2. Wait for sync to complete
3. Read from `capital.json`, `positions.json`, `orders.json`
4. Show complete, accurate data from files

---

## Root Cause

`get_actual_balance_from_derayah()` function (line ~821) uses Playwright to scrape:
```python
async def get_actual_balance_from_derayah():
    # Scrapes newonline.derayah.com/dashboard
    # Returns dict or None on failure
```

This is unreliable because:
- Dashboard tab may not exist
- Page structure may change
- Token may be expired
- CDP connection may fail

---

## Proposed Fix

### Option A: Replace Dashboard Scrape with File Read (Recommended)

Replace the `/Status` command logic:

**BEFORE:**
```python
# Fetch actual balance from Derayah
actual = await get_actual_balance_from_derayah()
# Use scraped values...
```

**AFTER:**
```python
# 1. Trigger bookkeeper sync
from bookkeeper import quick_refresh
try:
    quick_refresh()
except Exception as e:
    log.warning(f"Status: bookkeeper sync failed: {e}")

# 2. Read from capital.json (bookkeeper source of truth)
try:
    with open(CAPITAL_FILE) as f:
        capital = json.load(f)
    actual = {
        'total': capital.get('grand_total', 0),
        'available': capital.get('available_capital', 0),
        'invested': capital.get('invested', 0),
        'cash': capital.get('available_capital', 0),
    }
except Exception as e:
    log.warning(f"Status: could not read capital.json: {e}")
    actual = None
```

### Option B: Keep Scrape as Fallback Only

Keep dashboard scrape but only use if files are missing/empty:
```python
# Try files first
try:
    with open(CAPITAL_FILE) as f:
        capital = json.load(f)
    actual = {...}
except:
    # Fallback to scrape
    actual = await get_actual_balance_from_derayah()
```

**A A prefers Option A** (files first, no scrape).

---

## Evidence

### Current `/Status` Output (Broken)
```
📊 TASI TRADING STATUS

🔐 System Status
  Derayah Login: ✅
  TickerChart:   ✅

📈 Open Positions: None

📋 Outstanding Orders: None

💰 Capital (3-bucket)
  Equity:        0.00 SAR  (positions at market)  ← WRONG
  Booked:        0.00 SAR  (outstanding orders)
  Cash:      1,000.00 SAR  (available)             ← STALE
  ────────
  Total:     1,000.00 SAR                           ← WRONG

💰 Account Balance (Derayah Actual)
  Grand Total: 1,000.66 SAR  ← DEFAULT FALLBACK
  Invested:        0.00 SAR
  Available:   1,000.00 SAR
  Cash:          0.00 SAR
```

### Expected `/Status` Output (From Files)
```
📊 TASI TRADING STATUS

🔐 System Status
  Derayah Login: ✅
  TickerChart:   ✅

📈 Open Positions (1)
  • 5110: 12 shares @ 17.05 SAR
    Cost: 204.59 SAR

📋 Outstanding Orders: None

💰 Capital (3-bucket)
  Equity:    204.59 SAR  (positions at market)
  Booked:      0.00 SAR  (outstanding orders)
  Cash:      767.93 SAR  (available)
  ────────
  Total:     972.52 SAR

💰 Account Balance (from bookkeeper)
  Grand Total: 972.52 SAR
  Invested:    204.59 SAR
  Available:   767.93 SAR
```

---

## Testing Plan

1. Modify `bot.py` /Status handler
2. Restart tasi-bot.service
3. Run `/Status` command
4. Verify output matches `capital.json` values
5. Compare with manual `cat capital.json | python3 -m json.tool`

---

## Impact

- **Risk:** Low (reading from existing files, no new dependencies)
- **Benefit:** High (accurate capital display, no dashboard scraping)
- **Files modified:** `bot.py` only
- **Backwards compatible:** Yes

---

## Awaiting Approval

**A A — Please review and approve/reject this change request.**
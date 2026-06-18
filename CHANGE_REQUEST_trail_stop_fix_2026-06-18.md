# Change Request: Fix Trail Stop to Work From Entry Price

**Date:** 2026-06-18
**Requester:** A A
**File:** `poller.py`
**Type:** ASK — Bug fix

---

## 1. Problem

The trail stop only activates **after price goes up** (peak_pct >= trail_trigger). If price drops immediately from entry, the trail stop **never triggers** — positions rely on hard stop (-5% to -7%) or time stop (20-30 min) instead.

**Current buggy logic (line 1948):**
```python
elif peak_pct >= trail_trigger and drop_from_peak >= trail_stop_pct and key_trail not in _alerted:
    # Only triggers if price went UP first
```

**Example:**
- Entry at 100 SAR
- Price drops to 97 (-3%)
- `peak_pct` = 0% (never hits trigger)
- **Trail stop never activates!**
- Must wait for hard stop (-5% = 95) or time stop

---

## 2. Fix

**New logic:**
```python
elif drop_from_peak >= trail_stop_pct and mins_held >= min_hold and key_trail not in _alerted:
    # Triggers from entry price as initial peak
```

**Changes:**
1. Remove `peak_pct >= trail_trigger` requirement
2. Entry price is the initial peak
3. Trail stop activates immediately if price drops
4. Add min hold time to avoid spread noise

---

## 3. Impact

| Scenario | Before (buggy) | After (fixed) |
|----------|---------------|---------------|
| Entry 100, drops to 97 | Hard stop at 95 | **Trail stop at 97** |
| Entry 100, up to 105, down to 103 | Trail at 101.85 | Trail at 101.85 |
| Entry 100, up to 102, down to 99 | Trail at 98.98 | Trail at 98.98 |

---

## 4. Implementation

```python
# Line 1948: Replace this:
elif peak_pct >= trail_trigger and drop_from_peak >= trail_stop_pct and key_trail not in _alerted:

# With this:
elif drop_from_peak >= trail_stop_pct and mins_held >= MIN_HOLD_MINS and key_trail not in _alerted:
```

Also need to set MIN_HOLD_MINS (15 minutes) for trail stops.

---

**Approved by: A A**

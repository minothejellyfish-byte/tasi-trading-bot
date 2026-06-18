# Change Request: Dynamic Time Stops by Entry Time

**Date:** 2026-06-18
**Requester:** A A
**File:** `poller.py`
**Type:** ASK — Parameter adjustment

---

## 1. Current vs Proposed

**Current:** Fixed time stops (30 min NEUTRAL, 20 min DEFENSIVE)

**Proposed:** Clock-based time stops based on entry time

| Entry Time | NEUTRAL Time Stop | DEFENSIVE Time Stop |
|------------|-------------------|---------------------|
| Before 10:30 | 12:00 (clock time) | 11:30 (clock time) |
| 10:30-12:00 | 14:00 (clock time) | 13:00 (clock time) |
| After 12:00 | 14:30 (clock time) | 14:00 (clock time) |

**TRENDING:** Time stops **DISABLED**

---

## 2. Reasoning

**Why dynamic?**
- Earlier entries get earlier time stops (don't hold through chop all day)
- Later entries get more time to work
- DEFENSIVE has tighter time stops than NEUTRAL
- TRENDING: Let winners run, no forced time exits

**Example:**
- Entry at 10:15 → Time stop at 12:00 (NEUTRAL) or 11:30 (DEFENSIVE)
- Entry at 11:00 → Time stop at 14:00 (NEUTRAL) or 13:00 (DEFENSIVE)
- Entry at 13:00 → Time stop at 14:30 (NEUTRAL) or 14:00 (DEFENSIVE)

---

## 3. Implementation

```python
# New function to calculate time stop based on entry time
def get_time_stop_time(entry_time: datetime, regime: str) -> datetime:
    hour = entry_time.hour
    minute = entry_time.minute
    
    if regime == "TRENDING":
        return None  # No time stop
    elif regime == "NEUTRAL":
        if hour < 10 or (hour == 10 and minute < 30):
            return entry_time.replace(hour=12, minute=0)
        elif hour < 12:
            return entry_time.replace(hour=14, minute=0)
        else:
            return entry_time.replace(hour=14, minute=30)
    else:  # DEFENSIVE
        if hour < 10 or (hour == 10 and minute < 30):
            return entry_time.replace(hour=11, minute=30)
        elif hour < 12:
            return entry_time.replace(hour=13, minute=0)
        else:
            return entry_time.replace(hour=14, minute=0)
```

---

## 4. Impact

| Regime | Before | After | Benefit |
|--------|--------|-------|---------|
| NEUTRAL | 30 min fixed | Clock-based | No holding through afternoon chop |
| DEFENSIVE | 20 min fixed | Clock-based | Faster exits in weak market |
| TRENDING | 30 min fixed | **Disabled** | Let winners run |

---

**Approved by: A A**

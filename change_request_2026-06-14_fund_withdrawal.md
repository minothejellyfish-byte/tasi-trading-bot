# Change Request: Auto-Detect Fund/Withdrawal Movements

**Date:** 2026-06-14
**Time:** 19:02 KSA
**Requester:** A A
**Files:** `bookkeeper.py`, `history_io.py`, `daily_pnl.csv` (ASK tier)
**Priority:** HIGH
**Status:** Approved by A A

---

## Summary

Auto-detect deposits/withdrawals from capital.json changes and log to daily_pnl.csv.

## Design

### Detection Logic
```python
# In record_daily_pnl():
yesterday_total = get_previous_day_total()
today_total = capital.json['grand_total']
trading_pnl = calculate_from_orders()

expected_total = yesterday_total + trading_pnldifference = today_total - expected_total

if difference >= 100:
    record_deposit(difference)
elif difference <= -100:
    record_withdrawal(abs(difference))
else:
    # Trading variance, ignore
```

### Updated CSV Format
```csv
date,total,cash,equity,pnl,trades,deposits,withdrawals,notes
```

### Bot Commands (backup)
- `/Fund 500` - Manual deposit recording
- `/Withdraw 300` - Manual withdrawal recording

## Implementation

1. Update `history_io.py` - add deposits/withdrawals columns
2. Update `bookkeeper.py` - auto-detect in record_daily_pnl()
3. Update `daily_pnl.csv` - add new columns
4. Update `bot.py` - add /Fund and /Withdraw commands

## Approved by: A A
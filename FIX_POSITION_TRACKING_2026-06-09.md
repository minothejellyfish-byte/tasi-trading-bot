# Position Tracking + Fees Fix — 2026-06-09

## Problem 1: Position Tracking
Manual Telegram trades in `bot.py` tracked positions as **discrete order pairs** instead of **net quantity per symbol**.

### Example of the bug:
| Time | Action | What Bot Did | What Should Happen |
|------|--------|-------------|-------------------|
| 12:28 | Buy 6 @ 24.20 | Created position: 6 shares | Position: 6 shares |
| 12:28 | Buy 5 @ 24.20 | **Overwrote** position: 5 shares | Position: **11 shares** @ 24.20 avg |
| 12:34 | Sell 5 @ 23.95 | **Marked entire position closed** | Position: **6 shares** remaining |

Result: Bot thought 4008 was closed, so it **didn't market-sell at 14:45 hard close**. Position stayed open overnight.

## Problem 2: Fees Not Calculated
Manual trades in `bot.py` didn't calculate commission (0.05%) + VAT (15% on commission), so capital.json was wrong until bookkeeper synced.

## Fixes Applied (Option B)

### `bot.py` — `record_buy()` and `record_sell()` rewritten:
- **record_buy()**: 
  - Adds to existing position, recalculates weighted average entry price
  - Calculates fees: `commission = trade_value × 0.0005`, `vat = commission × 0.15`
  - Updates `capital.json`: subtracts `total_cost = trade_value + commission + vat`
- **record_sell()**: 
  - Reduces qty by amount sold, tracks realized P&L
  - Calculates fees on sell: `total_returned = trade_value - commission - vat`
  - Updates `capital.json`: adds back `total_returned`
  - Only marks closed when qty reaches 0
- **CLOSE ALL**: Now implemented — market sells all open positions

### Key changes:
```python
# Before: overwrote entire position, no fees
pos[symbol] = {"qty": qty, "entry_price": price, "closed": False}

# After: adds to existing, tracks fees
trade_value = qty * price
commission = trade_value * 0.0005
vat = commission * 0.15
total_cost = trade_value + commission + vat

new_qty = old_qty + qty
avg_entry = (old_cost + new_cost) / new_qty
pos[symbol] = {"qty": new_qty, "entry_price": avg_entry, "cost": new_cost, 
               "commission": commission, "vat": vat, "total_cost": total_cost}
```

## Verification

Tested the exact 4008 sequence:
```
Buy 6 @ 24.20  → qty=6,  entry=24.20, cost=145.20, fees=0.07+0.01, capital=854.72
Buy 5 @ 24.20  → qty=11, entry=24.20, cost=266.20, fees=0.06+0.01, capital=733.65
Sell 5 @ 23.95 → qty=6,  entry=24.20, realized=-1.25, capital=853.33, closed=False ✅
Buy 4 @ 23.88  → qty=10, entry=24.07, cost=240.72
Sell 4 @ 23.72 → qty=6,  entry=24.07, realized=-2.66, closed=False ✅
Sell 6 @ 23.72 → qty=0,  realized=-4.77, closed=True ✅
```

## Files Modified
- `/home/mino/tasi-exec/bot.py` — `record_buy()`, `record_sell()`, `close_all_positions()`, call sites

## Already Working (No Changes Needed)
- `poller.py` `auto_buy()` / `auto_sell()` — already had correct net-quantity tracking + fees
- `poller.py` `sync_positions_with_derayah()` — already syncs with Derayah API truth
- Bookkeeper — still overwrites with Derayah truth every 15 min as designed

## Result
- Manual trades and auto trades now use **consistent position tracking + fee calculation**
- Market close at 14:50 will sell actual remaining qty, not miss partially-closed positions
- Realized P&L is tracked across partial sells
- Capital updates immediately with fees (no waiting for bookkeeper)
- CLOSE ALL command actually works

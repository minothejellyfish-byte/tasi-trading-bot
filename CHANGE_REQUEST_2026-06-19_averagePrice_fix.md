# Change Request: averagePrice Fix + FIFO PnL + Capital Reconciliation
**Date:** 2026-06-19 08:13 KSA
**Type:** Bug Fix + Feature Enhancement
**Status:** ✅ IMPLEMENTED — awaiting commit approval

---

## 1. Summary

Systematic bug in bookkeeper where `price` (submitted) was used instead of `averagePrice` (actual execution) for FILLED orders. This caused phantom PnL losses and incorrect capital tracking. Additionally implemented full FIFO PnL, end-of-day capital reconciliation, and orders.json pruning.

---

## 2. Changes

### 2.1 bookkeeper.py
- **averagePrice fix** (lines ~582, ~867): Use `averagePrice` from Derayah API for FILLED orders
- **Full FIFO PnL** (`get_daily_pnl`): Match sells against ALL previous buys across dates
- **Capital reconciliation** (`record_daily_pnl`): Auto-correct PnL rounding at EOD
- **Deduplication fix**: Use `qty` instead of `price` in dedup key
- **Orders.json pruning** (`quick_refresh`): Call `prune_orders_json_terminal()` every sync

### 2.2 bot.py
- **`/HisCap` command**: Show Trading PnL + fund/withdrawal indicators separately

### 2.3 Data Files
- `history/order_history.csv`: 38 prices corrected using Derayah API
- `history/daily_pnl.csv`: June 18 corrected (PnL -3.51, fund +100 SAR)
- `history/daily_pnl.csv`: June 19 corrected (no deposit)

---

## 3. Impact

| Before | After |
|--------|-------|
| June 18 PnL: -25.94 | June 18 PnL: **-3.51** |
| Phantom withdrawal: 901.26 | **No withdrawal** — fund was +100 |
| June 18 end capital: 46.36 | June 18 end capital: **1045.89** |
| Duplicate June 19 orders | **0 duplicates** |
| orders.json: 91 stale orders | orders.json: **0** (auto-pruned) |

---

## 4. Verification

- ✅ `capital.json`: 1045.89 matches Derayah dashboard
- ✅ `order_history.csv`: 139 rows, 0 duplicates
- ✅ `orders.json`: 0 stale orders
- ✅ `/HisCap`: Shows Trading PnL separately from funds
- ✅ Bookkeeper: Records 53 new, skips 39 duplicates (stable)

---

## 5. Request

**Approve commit** of:
- `bookkeeper.py`
- `bot.py`
- `history/daily_pnl.csv`
- `history/order_history.csv`
- `post_market.py`

Commit message: `[FIX] averagePrice for correct fills, FIFO PnL, capital reconciliation, dedup fix, orders.json prune`

---

*Change request filed: 2026-06-19 08:13 KSA*

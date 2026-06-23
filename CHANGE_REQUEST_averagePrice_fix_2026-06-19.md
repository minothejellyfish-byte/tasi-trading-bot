# TASI averagePrice Fix — Implementation Complete
**Date:** 2026-06-19 07:12 KSA
**Status:** ✅ COMPLETE

---

## Changes Applied

### 1. Code Fix — `bookkeeper.py`

**Lines modified:** 582, 867

**Before:**
```python
"price": o.get("price", 0),
```

**After:**
```python
"price": o.get("averagePrice") if o.get("status") == 12 and o.get("averagePrice") else o.get("price", 0),
```

**Why:** Derayah API returns `price` (submitted) and `averagePrice` (actual execution). For FILLED orders (status=12), `averagePrice` is the true fill price. Using `price` caused phantom losses when execution differed from submission.

---

### 2. Retroactive Data Correction — `order_history.csv`

**38 orders corrected** across all trading dates (2026-06-11 through 2026-06-18).

**Most significant corrections:**

| Order | Symbol | Date | Old Price | New Price | PnL Impact |
|-------|--------|------|-----------|-----------|------------|
| 122 | 4019 | 06-18 | 18.931 | **17.63** | **+18.21** |
| 99 | 6040 | 06-17 | 6.750 | 6.70 | +2.45 |
| 108 | 8210 | 06-17 | 192.700 | 191.90 | +0.80 |
| 50 | 4200 | 06-11 | 119.000 | 118.30 | +0.70 |
| 100 | 1304 | 06-17 | 40.040 | 39.96 | +0.40 |

All corrected prices verified against Derayah API `Order/Details` endpoint.

---

### 3. Daily PnL Recalculation — `daily_pnl.csv`

| Date | Old PnL | Corrected PnL | Difference |
|------|---------|---------------|------------|
| 2026-06-11 | -3.06 | **-2.87** | +0.19 |
| 2026-06-14 | -4.53 | **-4.56** | -0.03 |
| 2026-06-15 | -5.38 | **-3.74** | +1.64 |
| 2026-06-16 | -9.56 | **-10.24** | -0.68 |
| 2026-06-17 | -7.16 | **-5.07** | +2.09 |
| 2026-06-18 | -25.94 | **-6.65** | **+19.29** |

**June 18 corrected breakdown:**

| Symbol | Qty | Buy → Sell | PnL |
|--------|-----|-----------|-----|
| 1180 | 7 | 41.20/41.22 → 41.18/41.22 | -0.30 |
| 2081 | 1 | 118.00 → 117.60 | -0.40 |
| 2160 | 25 | 13.52/13.53 → 13.47/13.53 | -0.17 |
| 4017 | 10 | 38.68/38.54 → 38.62/38.24 | -2.28 |
| 4019 | 14 | **17.63** → 17.38 | -3.50 |
| **TOTAL** | | | **-6.65** |

---

## Root Cause

The Derayah `Order/List` API returns two price fields:
- `price`: The **submitted** price (what you entered)
- `averagePrice`: The **actual execution** price (what you paid)

The bookkeeper used `price` for everything. For LIMIT orders that fill exactly at the submitted price, this is fine. But for:
- MARKET orders (price=0 or proxy)
- Any slippage or partial fills
- Orders where Derayah executes at a different price

…the recorded PnL was wrong.

**Example:** Order 122 was recorded as buying 4019 at 18.931, but actually executed at 17.63. This created a **phantom loss of 18.21 SAR** (14 × 1.301 difference).

---

## Verification

1. ✅ **Code fix:** `grep averagePrice /home/mino/tasi-exec/bookkeeper.py` shows both fixes in place
2. ✅ **Data fix:** `order_history.csv` prices match `averagePrice` from Derayah API
3. ✅ **PnL fix:** Recalculated daily PnL uses corrected FIFO matching
4. ✅ **Cross-check:** Order 122 now shows `BUY 14x4019 @ 17.63` matching bot log and Derayah API

---

## Files Changed

- `/home/mino/tasi-exec/bookkeeper.py` (lines ~582, ~867)
- `/home/mino/tasi-exec/history/order_history.csv` (38 rows corrected)
- `/home/mino/tasi-exec/history/daily_pnl.csv` (6 rows recalculated)

---

## Backups

- `bookkeeper.py.backup-20260619-070000`
- `history/order_history.csv.backup-20260619-070000`
- `history/daily_pnl.csv.backup-20260619-070000`

---

## Follow-up Actions

1. **Monitor:** Next bookkeeper run (5-min cron) should use `averagePrice` for new FILLED orders
2. **Future:** Add validation warning when `averagePrice` differs from `price` by >0.5%
3. **Review:** Check if position tracking (positions.json) also needs `averagePrice` fix

---

*Implementation complete: 2026-06-19 07:12 KSA*

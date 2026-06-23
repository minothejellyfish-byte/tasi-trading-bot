# TASI Retroactive Correction Log
**Date:** 2026-06-19 08:16 KSA
**Commit:** `3f96a4a` (amend note)

---

## Correction: Remove June 19 from daily_pnl.csv

**Reason:** June 19 was not a trading day — no trades executed. The row existed because the capital scraper comma fix caused a false "deposit detection" (999.53 SAR) when the scraper went from broken (45.89) to correct (1045.89).

**Action:** Removed June 19 row from `history/daily_pnl.csv`.

**Result:** Only actual trading days remain in the historical PnL record.

---

## Full Retroactive Corrections Applied Today

| # | Issue | Fix | Status |
|---|-------|-----|--------|
| 1 | Capital scraper comma bug | Regex fix: `1,045.89` → `1045.89` | ✅ Committed |
| 2 | `averagePrice` vs `price` bug | Use `averagePrice` for FILLED orders | ✅ Committed |
| 3 | Same-day-only PnL calculation | Full FIFO matching across all dates | ✅ Committed |
| 4 | Phantom withdrawal 901.26 SAR | Removed — was scraper artifact | ✅ Committed |
| 5 | June 18 PnL: -25.94 | Corrected to **-3.51** | ✅ Committed |
| 6 | 38 duplicate June 19 orders | Removed from `order_history.csv` | ✅ Committed |
| 7 | `orders.json` bloat (91 stale) | Auto-prune terminal orders | ✅ Committed |
| 8 | June 19 false row in daily_pnl | Removed — not a trading day | ✅ This note |

---

*All corrections verified against Derayah API and bot logs.*

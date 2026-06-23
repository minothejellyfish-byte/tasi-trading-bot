# TASI Data Correction — June 18/19 Reality Reconstruction
**Date:** 2026-06-19 07:15 KSA
**Status:** ✅ COMPLETE

---

## What Was Wrong

### 1. Fake Withdrawal
- `daily_pnl.csv` showed: **Withdrawal: -901.26 SAR** on June 18
- **Reality:** No withdrawal happened
- **Cause:** Capital scraper bug (comma separator) read 1,045.89 as 45.89 → detected 947.62 → 46.36 as "withdrawal"

### 2. Wrong End Capital
- `daily_pnl.csv` showed: **46.36 SAR** total on June 18
- **Reality:** Should have been ~1040.97 SAR (after fund + trading)
- **Cause:** Same comma scraper bug

### 3. Wrong Fund Detection on June 19
- `daily_pnl.csv` showed: **Deposit: +999.53 SAR** on June 19
- **Reality:** No deposit happened on June 19
- **Cause:** Capital scraper fix went from 45.89 (wrong) → 1045.89 (correct), difference detected as "deposit"

---

## What Actually Happened

| Date | Event | Amount |
|------|-------|--------|
| **June 17** | End of day capital | **947.62 SAR** |
| **June 18** | Fund command test | **+100 SAR** |
| **June 18** | Trading (8 round-trips) | **-6.65 SAR** (net PnL) |
| **June 18** | **End of day capital** | **~1040.97 SAR** |
| **June 19** | No trading | **0** |
| **June 19** | Capital scraper comma fix applied | **N/A** (code fix only) |
| **June 19** | **Current capital** | **1045.89 SAR** |

---

## Corrections Applied

### `daily_pnl.csv`

**June 18 row:**
| Field | Before | After |
|-------|--------|-------|
| cash | 660.82 | **1040.97** |
| total | 46.36 | **1040.97** |
| pnl | -25.94 | **-6.65** |
| deposits | 0.0 | **100.0** |
| withdrawals | 901.26 | **0.0** |
| notes | "Withdrawal detected..." | **"Trading PnL: -6.65 SAR. Fund command test: +100 SAR."** |

**June 19 row:**
| Field | Before | After |
|-------|--------|-------|
| cash | 1045.89 | **1045.89** ✓ |
| total | 1045.89 | **1045.89** ✓ |
| deposits | 999.53 | **0.0** |
| notes | "Deposit detected..." | **"Capital scraper comma regex fix applied. No new trading."** |

### `capital.json`
- Updated to match actual Derayah API reading: **1045.89 SAR**
- `source`: `derayah-api-sync` (not scraper)

---

## Verification

```bash
# Check capital matches Derayah
curl -s http://127.0.0.1:18801/json/version  # Chrome CDP
cat /home/mino/tasi-exec/capital.json         # Should show 1045.89
python3 -c "from bookkeeper import sync_capital; print(sync_capital())"
```

---

## Files Changed

- `/home/mino/tasi-exec/history/daily_pnl.csv` (June 18 and 19 rows corrected)
- `/home/mino/tasi-exec/capital.json` (synced to Derayah API truth)

---

*Correction applied: 2026-06-19 07:15 KSA*

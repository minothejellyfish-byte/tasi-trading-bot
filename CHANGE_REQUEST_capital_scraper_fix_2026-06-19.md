# TASI Capital Scraper Fix — Change Log
**Date:** 2026-06-19 03:56 KSA  
**File:** `/home/mino/tasi-exec/bookkeeper.py`  
**Severity:** CRITICAL — Capital values wrong when >999 SAR

---

## 1. Bug Summary

The dashboard scraper regex `r'(\d+\.\d{2})'` only captured digits immediately before the decimal point. When Derayah displayed values with comma separators (e.g., "1,045.89"), it captured "045.89" → converted to 45.89, losing the thousands digit.

## 2. Impact

| Account Value | Displayed As | Error |
|---------------|------------|-------|
| 1,045.89 SAR | 45.89 SAR | -1,000 SAR missing |
| 2,150.00 SAR | 150.00 SAR | -2,000 SAR missing |
| 999.99 SAR | 999.99 SAR | ✅ Correct (no comma) |

This caused:
- Wrong position sizing (too small)
- PnL calculations with negative equity
- Capital file completely out of sync

## 3. Fix Applied

**File:** `bookkeeper.py` lines 293, 296, 299

**Before:**
```python
m = re.search(r'(\d+\.\d{2})', lines[i + 1])
if m: cash_data["grand_total"] = float(m.group(1))
```

**After:**
```python
m = re.search(r'(\d{1,3}(?:,\d{3})*\.\d{2})', lines[i + 1])
if m: cash_data["grand_total"] = float(m.group(1).replace(',', ''))
```

Same fix applied to `money_transfer` and `cash_accounts` extraction.

## 4. Verification

- ✅ Bookkeeper run: `grand_total=1045.89, available=1045.89`
- ✅ Capital.json updated: `1045.89 SAR`
- ✅ Daily PnL recorded: `Deposit detected: +999.53 SAR`
- ✅ Git commit: `ef64597`

## 5. Files Changed

- `/home/mino/tasi-exec/bookkeeper.py` (lines 293, 296, 299)

## 6. Current State

```json
{
    "available_capital": 1045.89,
    "grand_total": 1045.89,
    "money_transfer": 1045.89,
    "source": "derayah-dashboard-scrape",
    "updated_at": "2026-06-19T03:56:47+03:00"
}
```

**Daily PnL (June 19):**
- Cash: 1045.89
- Total: 1045.89
- Deposit detected: +999.53 SAR
- Notes: Capital fix applied

---
*Fix applied and verified: 2026-06-19 03:56 KSA*
# TASI PnL Analysis — June 14, 2026

## Actual Performance: -5.48 SAR

---

## 1. DETAILED MATH

### 1320 Trades (3 rounds)

| # | Action | Qty | Price | Total | Fees |
|---|--------|-----|-------|-------|------|
| 1 | BUY | 5 | 56.80 | 284.00 | 0.16 |
| 2 | SELL | 5 | 56.40 | 282.00 | 0.16 |
| | **PnL** | | | **-2.00** | **0.32** |
| 3 | BUY | 5 | 56.30 | 281.50 | 0.16 |
| 4 | SELL | 5 | 56.30 | 281.50 | 0.16 |
| | **PnL** | | | **0.00** | **0.32** |
| 5 | BUY | 5 | 56.30 | 281.50 | 0.16 |
| 6 | SELL | 5 | 56.45 | 282.25 | 0.16 |
| | **PnL** | | | **+0.75** | **0.32** |

**1320 Total: -2.21 SAR** (fees: 0.96)

### 5110 Trades (3 rounds)

| # | Action | Qty | Price | Total | Fees |
|---|--------|-----|-------|-------|------|
| 1 | BUY | 17 | 17.03 | 289.51 | 0.17 |
| 2 | SELL | 17 | 16.96 | 288.32 | 0.17 |
| | **PnL** | | | **-1.19** | **0.34** |
| 3 | BUY | 17 | 16.95 | 288.15 | 0.17 |
| 4 | SELL | 17 | 16.91 | 287.47 | 0.17 |
| | **PnL** | | | **-0.68** | **0.34** |
| 5 | BUY | 12 | 17.04 | 204.48 | 0.12 |
| 6 | SELL | 12 | 17.00 | 204.00 | 0.12 |
| | **PnL** | | | **-0.48** | **0.24** |

**5110 Total: -3.27 SAR** (fees: 0.92)

### Total
- **Gross PnL (without fees):** -3.60 SAR
- **Fees:** 1.88 SAR
- **Net PnL:** **-5.48 SAR**

---

## 2. IDEAL vs ACTUAL

### 1320 — Ideal Scenario
- **Entry:** 56.30 (zone low)
- **Exit:** 57.40 (high of day)
- **Ideal PnL:** (57.40 - 56.30) × 15 shares = **+16.50 SAR**
- **Actual PnL:** -2.21 SAR
- **Missed:** **18.71 SAR**

### 5110 — Ideal Scenario
- **Entry:** 16.84 (low of day)
- **Exit:** 17.17 (high of day)
- **Ideal PnL:** (17.17 - 16.84) × 46 shares = **+15.18 SAR**
- **Actual PnL:** -3.27 SAR
- **Missed:** **18.45 SAR**

### Total Missed Profit
- **1320:** +18.71 SAR
- **5110:** +18.45 SAR
- **Total:** **+37.16 SAR**

---

## 3. WHAT WE COULD HAVE MADE

| Scenario | 1320 | 5110 | Total |
|----------|------|------|-------|
| **Actual** | -2.21 | -3.27 | **-5.48** |
| **Ideal** | +16.50 | +15.18 | **+31.68** |
| **With fees** | +15.54 | +14.26 | **+29.80** |
| **Difference** | +18.71 | +18.45 | **+37.16** |

---

## 4. WHY WE LOST

### 1320:
- Round 1: Sold at 56.40 vs entry 56.80 (**-0.40 loss**)
- Round 2: Sold at breakeven 56.30 (**0.00**)
- Round 3: Sold at 56.45 vs entry 56.30 (**+0.15 profit**)
- **Problem:** Exited too early, missed move to 57.40

### 5110:
- All 3 rounds sold below entry price
- **Problem:** VWAP breakdown exits triggered before profit

### Root Causes:
1. **VWAP breakdown exits** — too sensitive, exit too early
2. **No trailing stop** — gave back profits
3. **Multiple round trips** — increased fees (1.88 SAR)

---

## 5. RECOMMENDATIONS

1. **Use trailing stops** — capture more profit
2. **Widen VWAP threshold** — avoid premature exits
3. **Reduce round trips** — fewer trades = lower fees

---

*Generated: 2026-06-15*

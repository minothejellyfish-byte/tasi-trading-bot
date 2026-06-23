# TASI Trading System Analysis — Who Controls What

## Current Architecture (Corrected)

```
Poller (price feed + TRADING LOGIC + EXECUTION)
    ↓
Derayah (order execution via API)
    ↓
Bot (records trades, Telegram commands)
    ↓
Bookkeeper (syncs orders, tracks P&L)
```

**Key Finding:**
- **Poller makes ALL trading decisions** — NOT the bot
- Bot only records trades after they happen
- Poller directly calls `auto_buy()` and `auto_sell()`

---

## Current Entry Logic (poller.py ~1995)

### 4 Entry Types:
1. **Gap-Up / In-Zone Entry** — Price opens above zone or in zone
2. **VWAP Reclaim Entry** — Price crosses ABOVE VWAP after being below
3. **Zone Hold Entry** — Price holds in zone for 3 candles above VWAP
4. **Breakout Entry** — Price breaks above prior high + volume surge

### Current VWAP Reclaim Logic:
```python
if price > vwap and was_below_vwap:
    # Buy at market price (chasing!)
    auto_buy(symbol, qty, price=price, ...)
```

**Problem:** Buys ABOVE VWAP (chasing price up)

---

## Current Exit Logic (poller.py ~1672)

### 6 Exit Types:
1. **Hard Stop** — -7% loss (regime-based)
2. **Profit Target** — +2% (full exit if qty=1)
3. **Trailing Stop** — Peak drops 3% (regime-based)
4. **Time Stop** — Held 30+ min, down 1%+
5. **VWAP Breakdown** — Price drops below VWAP
6. **Tiered Exits** — Partial sells at +2%, +5%, +10%

### Current VWAP Breakdown Logic:
```python
if price < vwap:
    # With recovery logic (v4.4):
    # 1. Min hold 15 min
    # 2. Check recovery probability
    # 3. Breakeven hold if loss < 3% and recovering
    if mins_held >= 15 and not recovering:
        auto_sell(symbol, qty, reason="VWAP breakdown")
```

**Problem:** Still exits on VWAP breakdown, just delayed

---

## Proposed Changes (Without Implementation)

### 1. Entry Improvement: VWAP Pullback

**Current:** Buy when price crosses ABOVE VWAP
**New:** Buy when price pulls back TO VWAP from above

```python
# CURRENT (poller.py ~1995)
if vwap and check_vwap_reclaim(df, vwap):
    # Buy at market price (often above VWAP)
    auto_buy(symbol, qty, price=price)

# PROPOSED
if price <= vwap * 1.001 and was_above_vwap:
    # Buy at VWAP or slight discount
    limit_price = vwap * 0.998  # 0.2% below VWAP
    auto_buy(symbol, qty, price=limit_price, order_type="LIMIT")
```

**Benefit:** Better entry price, lower risk

### 2. Exit Improvement: Fixed Targets

**Current:** Sell on VWAP breakdown
**New:** Sell at +2% profit target or -2% stop loss

```python
# CURRENT (poller.py ~1672)
elif key_vwap_exit not in _alerted:
    if price < vwap:
        # Complex recovery logic, still exits on VWAP
        auto_sell(symbol, qty, TRIGGER_VWAP_BREAKDOWN)

# PROPOSED
# Replace VWAP breakdown with:
elif gain_pct >= win_pct:  # +2%
    auto_sell(symbol, qty, TRIGGER_TARGET_REACHED)
elif gain_pct <= -0.02:  # -2%
    auto_sell(symbol, qty, TRIGGER_HARD_STOP)
```

**Benefit:** More predictable exits, higher profits

### 3. Time Filter: Minimum Hold

**Current:** Can exit immediately (no min hold time for VWAP)
**New:** Minimum 30 minute hold before any exit

```python
# Add to all exit checks:
MIN_HOLD_MINS = 30
if mins_held < MIN_HOLD_MINS:
    continue  # Skip exit, hold longer
```

**Benefit:** Prevents panic exits during dips

---

## Expected Results

| Metric | Current | Proposed | Improvement |
|--------|---------|----------|-------------|
| Entry quality | Above VWAP | At/below VWAP | Better price |
| Exit quality | VWAP breakdown | +2% target | More profit |
| Win rate | ~33% | ~60% (est.) | +27% |
| Avg PnL/day | -5.48 SAR | +~15 SAR (est.) | +20 SAR |
| Fees | 1.88 SAR | 0.60 SAR | Fewer trades |

---

## Files to Modify (for reference)

1. **poller.py** (lines ~1672, ~1995)
   - Change VWAP reclaim entry logic
   - Replace VWAP breakdown exit with profit targets
   
2. **order_helpers.py**
   - Add new trigger basis: TRIGGER_VWAP_PULLBACK
   
3. **TASI_SYSTEM_REFERENCE.md**
   - Document new entry/exit rules

---

## Risk Considerations

1. **Market may not pull back** — Miss some fast movers
2. **Wider stops** — Larger individual losses possible
3. **Hold time** — Capital tied up longer

**Mitigation:**
- Test for 1 week before full deployment
- Adjust parameters based on results
- Keep regime-based position sizing

---

*Analysis: June 15, 2026*
*Status: Proposal only — no changes made*

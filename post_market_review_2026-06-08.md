# TASI Issues Found - 2026-06-08 Post-Market Review

## Session Info
- **Date**: 2026-06-08 (Sunday)
- **Market**: TASI (Saudi)
- **Time of issues**: 10:00 - 11:30 KSA
- **Bot status**: Poller active, bot running, ws_keepalive running
- **Session**: Derayah session expired ~10:55 (60min timeout)

---

## Issue Summary Table

| # | Issue | Impact | Evidence | Priority |
|---|-------|--------|----------|----------|
| 1 | **Derayah session expired ~60min after login** | Orders fail silently, bot thinks they succeeded | Session dead at ~10:55, no component detected it | 🔴 CRITICAL |
| 2 | **ws_keepalive doesn't check auth health** | Only checks CDP port + ws_probe, not Derayah session | ws_probe kept restarting (no data = session dead) | 🔴 CRITICAL |
| 3 | **Poller records position before order confirmation** | Ghost positions created, capital incorrectly deducted | 2130 added to positions.json before order filled | 🔴 CRITICAL |
| 4 | **Position upgrade doesn't check zone** | Sells current position, then can't buy new one | Sold 2130, 2110 gapped above zone → no position | 🔴 CRITICAL |
| 5 | **Position upgrade triggers with free slots** | Should add new pick, not sell existing | open_count=1, max=3, but upgrade logic fired | 🟠 HIGH |
| 6 | **Cleanup cron missing for blocked_symbols/stand_down** | Stale files block trades silently | blocked_symbols.txt from Jun 4 blocked 2110 | 🟡 MEDIUM |
| 7 | **WS cache miss on illiquid stocks** | Falls back to yfinance delayed price | 9409.SR only 9 ticks in 40min, stale after 10:06 | 🟡 MEDIUM |
| 8 | **Sell order not updating local state** | Capital locked, positions out of sync | 2110 sold in Derayah but still open locally | 🔴 CRITICAL |
| 9 | **Position upgrade sold 2110 at profit** | +0.12% gain but local state wrong | Order #38 filled, positions.json not updated | 🟡 INFO |

---

## Timeline of Events

| Time | Event | Issue # |
|------|-------|---------|
| 14:46 | SELL 2110 @ 169.6 (FILLED) | 8 |
| 14:46 | positions.json still shows 2110 OPEN | 8 |
| 14:50 | Capital locked, 480.22 invested | 8 |
| 10:48:03 | BUY 2130 @ 15.78 (limit order) | 3 |
| 10:48:03 | Position recorded locally (before confirmation) | 3 |
| 10:52:54 | Position upgrade triggered (2130 → 2110) | 4, 5 |
| 10:52:55 | SELL 2130 sent, failed "Not holding equity" | 3 |
| 10:52:58 | 2110 skipped — gapped above zone | 4 |
| ~10:55 | Session expired (60min timeout) | 1, 2 |
| ~10:55+ | ws_probe kept restarting (no data) | 2 |
| ~11:00+ | User logged back in manually | - |
| 11:13 | Discovered 2130 was actually sold in Derayah | 3 |

---

## Detailed Analysis

### Issue #1: Derayah Session Expiration
**What happened:**
- Derayah session has ~60min timeout
- No auto-refresh or warning mechanism
- Session expired at ~10:55 while market open

**Impact:**
- BUY order for 2130 may not have reached Derayah (or failed silently)
- Bot recorded position locally but Derayah rejected sell order
- No alerts fired — only ws_probe restart warnings

**Fix needed:**
- Add session health check to watchdog (every 5 min)
- Alert when session <10min from expiry
- Auto-refresh or force re-login prompt

---

### Issue #2: ws_keepalive Only Checks CDP Port
**What happened:**
- `ws_keepalive_v2.sh` checks:
  - CDP port 18801 responding ✅
  - ws_probe capturing data ❌ (detected, but misdiagnosed)
- Does NOT check:
  - Derayah auth token validity
  - Session expiration
  - TickerChart websocket auth

**Impact:**
- Session died → no websocket data
- ws_keepalive thought ws_probe was broken → kept restarting
- Never identified the actual root cause (session expired)

**Fix needed:**
- Add Derayah token/API check to ws_keepalive
- If session dead, alert immediately instead of restarting ws_probe
- Consider session refresh automation

---

### Issue #3: Position Recorded Before Confirmation
**What happened:**
```python
# In poller.py auto_buy():
place_order(...)  # Send order
# IMMEDIATELY after:
positions[symbol] = {...}  # Record position
```

**Impact:**
- Ghost position created for 2130
- Capital deducted (189.47 SAR) without confirmation
- Later discovered position was actually sold
- Local state and Derayah state diverged

**Fix needed:**
- Verify order status before recording position
- Use `get_orders_sync()` to confirm fill
- Only update positions.json after confirmed execution

---

### Issue #4: Position Upgrade Doesn't Check Zone
**What happened:**
```python
# In poller.py:
if best_new and best_new.get("score", 0) > current_score * pu_thresh:
    # SELL current position
    auto_sell(current_sym, qty, ...)
    # Then discover new pick is gapped above zone
    # → No position at all
```

At 10:52:
- 2130 score: 55
- 2110 score: 74
- Check: 74 > 55 * 1.3 = 71.5 → TRUE
- Sold 2130
- 2110 at 173.00 > zone (168.24) → SKIPPED
- Result: **NO POSITION**

**Fix needed:**
```python
if best_new and best_new.get("score", 0) > current_score * pu_thresh:
    # ADD THIS CHECK:
    new_price = fetch_data(best_new["symbol"])[0]
    zone = get_zone(best_new)
    if not (zone[0] <= new_price <= zone[1]):
        log.info(f"Upgrade candidate {best_new['symbol']} not in zone — skipping")
        continue
    # THEN sell current position
```

---

### Issue #5: Upgrade Triggers With Free Slots
**What happened:**
```python
# Current code (line ~1395):
if open_count >= 1 and open_count < max_positions:
    # enters upgrade logic
```

At 10:52:
- open_count: 1
- max_positions: 3
- Check: `1 >= 1 and 1 < 3` → TRUE → enters upgrade

**What SHOULD happen:**
```python
if open_count >= max_positions:
    # Only upgrade when ALL slots are full
    # With free slots, just ADD new pick
```

At 10:52 with 1/3 positions:
- Should NOT enter upgrade
- Should ADD 2110 to empty slot
- Keep 2130 + add 2110 = **2 positions**

**Fix needed:**
- Change condition to `open_count >= max_positions`
- When slots free, add new pick instead of upgrading

---

### Issue #6: Missing Cleanup Cron
**What happened:**
- `blocked_symbols.txt` contained `2110` from June 4
- No cleanup script/cron to remove it
- 2110 was blocked from trading on June 8

**Evidence:**
- blocked_symbols.txt last modified: June 4
- 2110.SR in file when mid-screen ran
- File manually removed at 10:37

**Fix needed:**
- Add OpenClaw cron to clear blocked_symbols.txt and stand_down at 09:55 daily
- Or integrate into tasi-watchdog-start cron

---

### Issue #7: Illiquid Stock Picked (9409.SR)
**What happened:**
- 9409.SR picked by mid-screen with score 70.2
- But only 9 WS ticks in 40+ minutes
- After 10:06, no real trades — stale repeated frames
- yfinance confirms: only 2 bars entire session

**Impact:**
- Poller correctly rejected stale WS cache
- Fell back to yfinance delayed price (10.00)
- Stock essentially untradeable

**Fix needed:**
- Add liquidity filter to screener:
  - Minimum WS ticks per hour (e.g., 30)
  - Minimum real trades with price changes (e.g., 5)
  - Skip stocks with frozen prices (<3 changes in window)

---

## Suggested Fixes (Priority Order)

### Immediate (Post-Market Today)
1. **Fix #8**: Verify sell execution + update positions.json + restore capital
2. **Fix #5**: Change upgrade condition to `open_count >= max_positions`
3. **Fix #4**: Add zone check before selling in upgrade logic
4. **Fix #3**: Verify order fill before recording position

### This Week
4. **Fix #1 + #2**: Add Derayah session health monitoring
5. **Fix #6**: Add cleanup cron for blocked_symbols/stand_down

### Next Sprint
6. **Fix #7**: Add liquidity filter to screener

---

## Today's P&L Summary

| Symbol | Entry | Exit | Qty | P&L |
|--------|-------|------|-----|-----|
| 2130.SR | 15.78 | 15.75 | 12 | **-0.46 SAR (-0.24%)** |
| 2110.SR | 169.4 | 169.6 | 1 | **+0.02 SAR (+0.12%)** |

**Result:** Small net loss due to position upgrade bug + session issues. 2110 sold at profit but local state wrong.

---

## Files to Review
- `/home/mino/tasi-exec/poller.py` (lines 1389-1450)
- `/home/mino/tasi-exec/derayah_api.py` (session/token management)
- `/home/mino/tasi-exec/derayah_session_manager.py` (NEW - session refresh)
- `/home/mino/tasi-exec/bot_commands.py` (NEW - /Login /SS commands)
- `/home/mino/tasi-exec/ws_keepalive_v2.sh` (add auth check)
- `/home/mino/tasi-exec/tasi_watchdog.py` (add session monitoring)
- `/home/mino/tasi-exec/midscreen_ws.py` (add liquidity filter)

### 8. Sell Order Not Updating Local State (Priority: 🔴 CRITICAL)

**What happened:**
- 14:46: SELL 2110 qty=1 @ 169.6 executed in Derayah (order #38, FILLED)
- Local positions.json still showed 2110 as OPEN
- Capital remained locked (not restored)
- Bot couldn't take new positions

**Root Cause:**
The poller's auto_sell() sends the order to Derayah but:
1. Doesn't verify execution via API
2. Doesn't update positions.json
3. Doesn't restore capital

**Evidence:**
| Source | 2110 Status | Qty | Price |
|--------|-------------|-----|-------|
| Derayah API | CLOSED | 0 | Sold @ 169.6 |
| positions.json | OPEN | 1 | @ 169.4 |
| capital.json | 480.22 invested | (locked) | - |

**This is the same bug as ghost position issue (#3).**

**Fix needed:**
```python
def auto_sell(symbol, qty, ...):
    # 1. Place order
    order_id = place_order(...)
    
    # 2. Verify execution (NEW)
    time.sleep(2)  # Wait for execution
    verify_order_executed(order_id)
    
    # 3. Update local state (NEW)
    positions[symbol]['closed'] = True
    positions[symbol]['close_price'] = sold_price
    positions[symbol]['close_time'] = now()
    
    # 4. Restore capital (NEW)
    capital['available'] += sell_value - commission
    capital['invested'] -= cost
    
    # 5. Save files (NEW)
    save_positions_json()
    save_capital_json()
```

**Impact:**
- Capital locked → can't take new positions
- Stop-loss missed → risk exposure
- Position count wrong → upgrade logic breaks

---

### 9. Position Upgrade Sold 2110 at Profit (+0.12%)

**What happened:**
- 2110 bought @ 169.4 (order #37)
- 2110 sold @ 169.6 (order #38)
- PnL: +0.02 SAR (+0.12%)
- But local state showed it as still open

**Note:** The sell was triggered by position upgrade logic (score-based), not stop-loss.

---

### 10. Derayah Session Manager Created

**Files created:**
- `derayah_session_manager.py` (200 lines)
- `bot_commands.py` (180 lines)
- `raci_matrix.html` (RACI visualization)

**Key findings:**
- Refresh token expires after ~2.5 hours (not days)
- Access token works for SSO URL (60 min)
- SSO navigation gives fresh TC token (no button click)
- Proactive refresh needed (cron every 50 min during market hours)

**Commands:**
- `/Login` - Capture tokens after manual login
- `/SS` - Full system status

**RACI Matrix:**
- Vertical: Activities (12)
- Horizontal: Executors (A A, Mino, Session Manager, WS Keepalive, Watchdog)

---

*End of issues*

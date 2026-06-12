# TASI System Changes — June 3, 2026

## Summary of Today's Fixes

### 1. ✅ Capital Tracking Fixed
**Problem**: `capital.json` only saved `available_capital`, losing `grand_total`, `securities_value`, `money_transfer`, `total_fees`

**Root Cause**: `capital_tracker.py:save_capital()` only wrote one field

**Fix**:
- Added `save_capital_full()` to preserve/update all balance fields
- Added `_scrape_derayah_full()` to scrape complete portfolio dashboard
- `poller.py` sell callback now recalculates all fields:
  - `available_capital` += net proceeds
  - `grand_total` -= fees
  - `securities_value` = 0 (when no open positions)
  - `money_transfer` synced
  - `total_fees` accumulated
- Added `_capital_refresh_thread()` in `bot.py` — scrapes every 30 min during market hours

**Files Modified**: `capital_tracker.py`, `poller.py`, `bot.py`

---

### 2. ✅ Keepalive Completely Redesigned
**Problem**: Token expired ~12:50 Jun 3, keepalive "Derayah Trade" click failed to refresh

**Root Cause**: "Derayah Trade" click requires alive SSO session. When Derayah Access Token expired (60 min), click opened TickerChart in logged-out state → 401 errors

**Investigation**:
- Found old `derayah_keepalive.py` had OAuth token refresh (CLIENT_ID=NewWebClient) but refresh token was single-use and consumed
- Discovered `GET /apispark/trade/TickerChartUrl` endpoint returns fresh SSO URL even with expired access token
- Confirmed: opening SSO URL creates new TC tab with fresh `TC_DERAYAH` JWT

**Fix**:
- Replaced "Derayah Trade" click with `TickerChartUrl` endpoint approach
- New flow:
  1. Call `GET /apispark/trade/TickerChartUrl` with current token
  2. Open returned URL in new tab
  3. Verify new `TC_DERAYAH` token in localStorage
  4. Close old TC tabs
  5. Fallback to "Derayah Trade" click if endpoint fails
- Disabled: `derayah_keepalive.py` process, `chromium-derayah.service`

**Files Modified**: `bot.py`

---

### 3. ✅ Market Regime Now Visible
**Problem**: `STATUS` command didn't show current regime

**Fix**: Added `get_current_regime()` call to `get_status()` function in `bot.py`

**Current Regime**: TRENDING (session return +0.25%, above VWAP)

**Files Modified**: `bot.py`

---

### 4. ✅ Midscreens Now Announced
**Problem**: Midscreen crons saved picks to JSON but didn't announce in Telegram group

**Fix**: Updated all 3 midscreen cron payloads to include instructions:
> "After running, read picks_XXXX.json and send the top 5 picks to the TASI Execution group"

**Crons Updated**:
- `tasi-midscreen-1` (10:30) → `picks_1030.json`
- `tasi-midscreen-2` (12:00) → `picks_1200.json`
- `tasi-rescreen` (13:30) → `picks_1330.json`

**Delivery**: All set to `announce → telegram:-5235925419`

---

### 5. ✅ In-Zone Priority Entry Logic (Option B)
**Problem**: On gap-up days, top 5 picks by raw score all gap above their entry zones → no trades. System monitored top 5 only.

**Investigation**:
- Simulated all entry options (A-E) on Jun 2 and Jun 3 actual market data
- Jun 2: Current system found 2 entries (+1.33%). All options found same entries.
- Jun 3: Current system found 0 entries. Market was flat after gap-up — no system could profit.
- Momentum chase (Option D) would have lost -0.29% on Jun 3 chasing 8311.SR

**Fix**:
- Added `actionability_score()` function in `poller.py`
- If top 5 picks are ALL out of zone → re-sort by actionability (score × zone_bonus)
- Monitor top 10 picks instead of top 5
- Actionability scoring:
  - In zone: score × 1.5
  - Near zone (within 2%): score × 1.2
  - Below zone: score × 0.5
  - Gapped above (>2%): score × 0.3

**Files Modified**: `poller.py`

---

### 6. ✅ Position 4021 Closed
- Sold at 12:57 Jun 3 via MARKET order
- `positions.json`: `"closed": true, "close_time": "2026-06-03T12:57:14"`
- `capital.json`: All fields updated with actual scraped values
- No re-sell risk (securities_value=0)

---

## Simulation Results

### June 2, 2026 (Gap-up + Trending)
| Option | Buys | P&L |
|--------|------|-----|
| Current (Top 5) | 2 (4005, 4021) | +8.66 SAR (+1.33%) |
| A: All 15 | 2 (same) | +8.66 SAR (+1.33%) |
| B: In-Zone | 2 (same) | +8.66 SAR (+1.33%) |
| D: Momentum | 3 (+2200 chase) | +11.36 SAR (+1.14%) |

**Conclusion**: System worked correctly. All options found same entries.

### June 3, 2026 (Gap-up + Flat)
| Option | Buys | P&L |
|--------|------|-----|
| Current (Top 5) | 0 | 0.00 SAR |
| A: All 15 | 3 (expensive/flat) | ~0.00 SAR |
| B: In-Zone | 3 (same) | ~0.00 SAR |
| D: Momentum | 3 (chase 8311) | -2.56 SAR (-0.29%) |

**Conclusion**: Market was flat after gap-up. No system could profit. Option D would have lost money.

---

## Files Updated Today
| File | What Changed |
|------|-------------|
| `bot.py` | New keepalive (TickerChartUrl), regime display, TZ fix |
| `poller.py` | Sell callback updates all capital fields + In-Zone Priority logic |
| `capital_tracker.py` | `save_capital_full()` + `_scrape_derayah_full()` |
| `TASI_SYSTEM_BLUEPRINT.md` | Complete rewrite with new architecture |
| `TASI_AGENT_MEMORY.md` | Complete rewrite with June 3 changes + simulation results |
| `CHANGELOG_2026-06-03.md` | This file |

## Crons (agentId: main)
| Name | Time | Status |
|------|------|--------|
| tasi-premarket-screener | 09:50 | ✅ Active, announces to group |
| tasi-price-poller | 10:00 | ✅ Active |
| tasi-midscreen-1 | 10:30 | ✅ Active, **now announces to group** |
| tasi-midscreen-2 | 12:00 | ✅ Active, **now announces to group** |
| tasi-rescreen | 13:30 | ✅ Active, **now announces to group** |
| post-market-analysis | 15:35 | ✅ Active |
| derayah-keepalive-trading | */5 | ❌ Disabled (archived) |

## Disabled/Archived
- ~~`derayah_keepalive.py`~~ — Process killed, no auto-restart
- ~~`chromium-derayah.service`~~ — `systemctl disable`, auto-restart removed

## Current System State
- **Bot**: Running with new keepalive code
- **Chrome/CDP**: Port 18801, user logged in
- **Token**: Valid (expires ~15:30 Jun 3)
- **Capital**: 993.87 SAR (all fields accurate)
- **Positions**: None
- **Regime**: TRENDING
- **Market**: CLOSED until 10:00 Jun 4
- **WebSocket**: 226,551 frames today (5.3h continuous, 2 minor gaps)
- **Entry Logic**: Option B (In-Zone Priority) — ready for Jun 4

## Next Verification
- [ ] Confirm Option B finds in-zone entries on Jun 4 if gap-up occurs
- [ ] Confirm keepalive refreshes token before expiry
- [ ] Confirm 30-min capital refresh updates JSON correctly
- [ ] Confirm tomorrow's midscreens announce in group

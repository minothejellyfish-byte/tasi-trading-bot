# TASI System Quick Reference

## Commands
- `STATUS` — Shows live Derayah balance (refreshes dashboard)
- `STAND DOWN` — Creates stand_down file, blocks all buys
- `CLOSE ALL` — Market sells all open positions
- `sync_positions` — Syncs positions from Derayah API

## Daily Schedule
| Time | Action |
|------|--------|
| 09:55 | Remove stand_down, clear blocked symbols |
| 09:56 | Start poller.py |
| 10:00 | Market opens |
| 10:05 | map_selectors.py runs |
| 10:07 | ws_probe.py starts |
| 10:30 | Mid-screen #1 |
| 12:00 | Mid-screen #2 |
| 13:30 | Rescreen |
| 14:45 | Hard close — create stand_down, sell all positions |

## Position Tracking (2026-06-09 Fix)
- Manual trades now use **net quantity** (adds to existing, weighted avg entry)
- `record_buy()` and `record_sell()` in bot.py handle partial sells correctly
- Fees calculated on every manual trade
- `capital.json` updated immediately

## Capital Calculation
```
Available = Money Transfer (from Derayah dashboard)
Invested = Securities Value (from Derayah dashboard)
Grand Total = Available + Invested (should match Derayah)
```

## Fee Formula
```python
commission = trade_value * 0.0005  # 0.05%
vat = commission * 0.15             # 15%
total_fee = commission + vat      # 0.0575%
```

## Chrome Profiles
| Profile | Status | Purpose |
|---------|--------|---------|
| `derayah-live` | ✅ Active | Created 2026-06-04 to fix Chrome 148 freeze bug |
| `derayah-profile` | ⚠️ Legacy | Original profile (may have freeze issues) |

**Current:** Chrome running with `derayah-live` profile
**CDP Port:** 18801

## Background Services
| Service | Purpose | Log File |
|---------|---------|----------|
| bot.py | Telegram bot, commands | exec.log |
| poller.py | Price polling, auto-trade | poller.log |
| ws_probe.py | WebSocket price capture | ws_probe.log |
| ws_keepalive.sh | Restarts ws_probe if it dies | ws_keepalive.log |
| derayah_keepalive.py | Derayah session keepalive | keepalive.log |

## Known Issues & Fixes

### 2026-06-09: WebSocket Keepalive + /SS Fix
**Symptom:** `/SS` showed "🔴 Data: Old (2+ days ago)"
**Cause:** `ws_keepalive_v2.sh` checked `ws_frames.json` (only written at end of 90s run) — killed `ws_probe.py` before it finished
**Fix:** Keepalive now checks `ws_frames_raw.log` (grows continuously); `/SS` now checks `ws_prices_*.jsonl`
**Status:** ✅ Fixed

### 2026-06-09: poller.py NameError
**Symptom:** `NameError: name 'BASE_DIR' is not defined` on startup
**Cause:** `sys.path.insert(0, BASE_DIR)` ran before `BASE_DIR` was defined
**Fix:** Moved BASE_DIR definition to top of file (before session management import)
**Status:** ✅ Fixed, poller running

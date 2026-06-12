# TASI Intraday Trading System — Changes Log

## 2026-05-21 (v3.1) — Fallback Tier Implementation

### What Changed
- **Screener** (`screener.py`): Now outputs **top 5 picks** instead of top 2
  - Primary tier: #1-2 (full position size)
  - Fallback tier: #3-5 (reduced to 25%)
  
- **Poller** (`poller.py`): Added fallback logic
  - 10:00-10:30: Monitor primary #1-2 only
  - 10:30: If no open positions, activate fallback tier
  - Fallback: Monitor #3-5 at 25% position size
  - Telegram alert sent when fallback activates
  
- **Backtest** (`backtest_2month.py`): Fallback comparison added
  - Strategy A+FB and B+FB as optional comparisons
  - Results: fallback didn't improve in 2-month window (same picks trigger)
  
- **Blueprint** (`TASI_Trading_Blueprint.md`): Updated to v3.1
  - Added fallback tier section
  - Updated position sizing table
  - Updated daily flow diagram
  - Added issue #17 to resolved issues
  
- **TOOLS.md**: Complete tool inventory created
  - All tools across Ocean, Amin-PC, OpenClaw skills
  - "When to use what" decision table
  
- **AGENTS.md**: Added TOOLS.md awareness
  - Session startup loads TOOLS.md
  - Tool awareness reminder

### Why Fallback?
May 20 simulation showed primary #1 scratched (-0.3%) while fallback #4 and #5 both won (+0.2%, +0.4%). Fallback captures missed opportunities when premium picks don't trigger.

### Files Modified
| File | Change |
|------|--------|
| `screener.py` | TOP_N: 2 → 5 |
| `poller.py` | +fallback logic, +25% sizing, +tier labels |
| `backtest_2month.py` | +fallback simulation columns |
| `TASI_Trading_Blueprint.md` | v3.0 → v3.1, +fallback sections |
| `TOOLS.md` | New file — complete inventory |
| `AGENTS.md` | +TOOLS.md awareness |

### Diagrams Generated
| Diagram | File | Size |
|---------|------|------|
| Daily Process Map | `daily_process_map.png` | 183 KB |
| Classification Mind Map | `classification_mindmap.png` | 47 KB |
| Strategy Decision Tree | `strategy_decision_tree.png` | 136 KB |

---

## 2026-05-20 (v3.0) — Post-Market Analysis v2

### What Changed
- Added `post_market_v2.py` — comprehensive post-market analysis
- New cron: `post-market-analysis` at 15:35
- Generates daily performance report + learning recommendations

---

## 2026-05-19 — Keepalive v2 + OAuth2 Token API

### What Changed
- `derayah_keepalive.py` upgraded with OAuth2 token injection
- Bypasses reCAPTCHA v2 entirely
- Three-tier fallback: token injection → CDP login → Playwright

---

## 2026-05-18 — System Stabilization

### What Changed
- Fixed CDP port mismatch (18800 → 18801)
- Removed duplicate keepalive crons
- Added `html.escape()` for Telegram messages
- Fixed Vue.js `fill()` bug with `press_sequentially()`
- Added yfinance fallback (`period='5d'`)
- Fixed poller hardcoded exit time (15:05 → 15:30)

---

*This log tracks all significant changes to the trading system. Update it after every modification.*

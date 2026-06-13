# TASI Trading System - Blueprint Update
## Date: 2026-06-02
## Session: Mid-session fixes and improvements

---

## 1. CHROME BROWSER SPAWNING ISSUE - RESOLVED

### Problem
New Chrome browser windows were opening every few minutes, blocking the active window and disrupting CRD monitoring.

### Root Causes Found
1. **OpenClaw Cron Job** (`derayah-keepalive-trading`): Running every 5 minutes
2. **chromium-derayah.service**: Had `ExecStartPre` that killed existing Chrome
3. **start-chrome.sh**: Killed existing Chrome before starting

### Fixes Applied
- ✅ **Disabled OpenClaw cron job** `derayah-keepalive-trading` (ID: e571d74d-ce11-4829-9ca2-7ef6ed9fe365)
- ✅ **Disabled failure analysis cron** `TASI Smart Failure Detection` (ID: e454f56e-da82-471e-86b7-79065f275ad0)
- ✅ **Removed `ExecStartPre` kill** from `chromium-derayah.service`
- ✅ **Fixed `start-chrome.sh`**: Added guard to check if Chrome already running
- ✅ **Fixed `bot.py`**: Removed new tab creation in keepalive (now skips if missing)

---

## 2. WEBSOCKET PRICE FEED - RESTORED

### Problem
WebSocket was dead, no live prices flowing. TickerChart tab missing.

### Fixes Applied
- ✅ **Fixed `poller.py` tab matching**: Prioritizes `derayah.tickerchart.net` first
- ✅ **Fixed `ws_probe.py` tab matching**: Same priority logic
- ✅ **Restored TickerChart tab**: Navigated to `https://derayah.tickerchart.net/app/en`
- ✅ **Restarted ws_probe.py**: Capturing 2000+ frames/minute
- ✅ **Added micro-scroll to bot.py keepalive**: `window.scrollBy(0, 1)` keeps WebSocket alive

### Current Status
- WebSocket: ✅ ACTIVE
- CDP Port: 18801
- Tabs: 2 (TickerChart + Dashboard)
- Live Prices: ✅ FLOWING

---

## 3. LOG DUPLICATION - FIXED

### Problem
Multiple log entries appearing (duplicate handlers)

### Fixes Applied
- ✅ **Added handler clearing** before `basicConfig` in:
  - `poller.py`
  - `screener.py`
  - `bot.py`

```python
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
    handler.close()
```

---

## 4. AUTO-EXECUTION LOGIC - IMPROVED

### Problem
No trades executed from 12:00 picks despite being in entry zone

### Root Cause
VWAP reclaim signal too strict:
- Volume threshold: `0.8x` average (too high for midday)
- Required cross FROM BELOW to ABOVE (many stocks opened above VWAP)
- No fallback for stocks holding in zone above VWAP

### Fixes Applied
- ✅ **Relaxed VWAP volume threshold**: `0.8 → 0.5` (`poller.py` line 540)
- ✅ **Added "Zone Hold" entry signal**: 
  - Price in entry zone
  - Above VWAP for 3 consecutive candles
  - Volume > 1.5x average over 3 candles
- ✅ **Restarted poller.py** with new logic (PID: 106671)

### Entry Signals Now Active
1. **Gap-Up** (until 10:30): Price in zone at open
2. **VWAP Reclaim** (relaxed): Cross above VWAP + volume > 0.5x
3. **Zone Hold** (NEW): Hold above VWAP in zone for 3 candles
4. **Breakout**: New high + volume surge

---

## 5. CURRENT SYSTEM STATUS

### Running Processes
| Process | PID | Status |
|---------|-----|--------|
| bot.py | 45176 | ✅ Running |
| poller.py | 106671 | ✅ Running (new logic) |
| ws_probe.py | 100710 | ✅ Running |
| Chrome | 55092 | ✅ Stable |

### Tabs
1. `https://derayah.tickerchart.net/app/en` - TickerChart (live prices)
2. `https://newonline.derayah.com/#/layout/dashboard` - Dashboard (logged in)

### Open Positions
- **NONE** - 0 positions open
- Capital: 1,000.66 SAR

### 12:00 Picks Status
- 5 picks loaded (midscreen2)
- 9 total picks in entry zone (across all screens)
- 0 VWAP reclaims (relaxed check now active)
- Waiting for Zone Hold signal or 13:30 rescreen

---

## 6. CRON JOBS STATUS

### Disabled (Caused Issues)
- ❌ `derayah-keepalive-trading` - Opened Chrome every 5 min
- ❌ `TASI Smart Failure Detection` - Every 15 min, could interfere

### Still Active
- ✅ `daily-ram-cleanup` - Daily at 04:00
- ✅ `ocean-health-monitor` - Every 30 min
- ✅ `tasi-premarket-screener` - 09:50 Sun-Thu
- ✅ `tasi-price-poller` - 10:00 Sun-Thu
- ✅ `tasi-midscreen-1` - 10:30 Sun-Thu
- ✅ `tasi-midscreen-2` - 12:00 Sun-Thu
- ✅ `tasi-rescreen` - 13:30 Sun-Thu
- ✅ `tasi-log-cleanup` - Daily at 04:00
- ✅ `post-market-analysis` - 15:35 Sun-Thu

---

## 7. FILES MODIFIED TODAY

1. `/home/mino/tasi-exec/bot.py` - Keepalive fixes, micro-scroll
2. `/home/mino/tasi-exec/poller.py` - VWAP logic, Zone Hold signal
3. `/home/mino/tasi-exec/ws_probe.py` - Tab matching priority
4. `/home/mino/tasi-exec/derayah_keepalive.py` - Browser preservation
5. `/home/mino/tasi-exec/screener.py` - Log duplication fix
6. `/home/mino/tasi-exec/derayah_api.py` - TC_URL update
7. `/home/mino/.config/systemd/user/chromium-derayah.service` - Removed kill
8. `/home/mino/tasi-exec/start-chrome.sh` - Added running check

---

## 8. MONITORING SETUP

### Chrome Trap
- Script: `/tmp/chrome_trap.sh`
- Status: Running (PID: 98369)
- Checks: Every 3 seconds for new Chrome windows/processes/tabs
- Result: 170+ checks, ZERO alerts (system stable)

### Log Files
- `/home/mino/tasi-exec/exec.log` - bot.py activity
- `/home/mino/tasi-exec/poller.log` - poller.py activity
- `/home/mino/tasi-exec/ws_probe.log` - WebSocket capture
- `/home/mino/tasi-exec/keepalive.log` - keepalive activity

---

## 9. NEXT SCHEDULED EVENTS

| Time | Event |
|------|-------|
| 13:00 | Next poller check (5-min interval) |
| 13:10 | Next bot.py keepalive (15-min interval) |
| 13:30 | **TASI Rescreen** (new picks evaluation) |
| 15:10 | Market Close |
| 15:35 | Post-market analysis |

---

## 10. CRITICAL CONTEXT FOR SUB-AGENTS

### Market Hours
- **Open**: 10:00 AM
- **Close**: 15:10 PM
- **Days**: Sunday - Thursday

### Chrome Remote Desktop
- URL: remotedesktop.google.com
- Host: "ocean"
- PIN: 056187

### Key Constraints
- **NEVER kill Chrome during trading hours**
- **NEVER open new tabs** (only navigate existing)
- **Preserve visible browser window** for CRD monitoring

### Capital
- Available: 1,000.66 SAR
- Updated: 2026-06-02 07:02

---

*Last Updated: 2026-06-02 12:56 PM +03*

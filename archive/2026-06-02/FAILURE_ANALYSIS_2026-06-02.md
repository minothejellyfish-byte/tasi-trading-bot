# TASI Trading System — Failure Analysis & Recovery Guide
## Date: 2026-06-02 11:21 GMT+3 (Tuesday)

---

## 🚨 CURRENT STATUS (CRITICAL)

### System Health
| Component | Status | Details |
|-----------|--------|---------|
| **Ollama API** | ✅ OK | 5 models loaded, responding |
| **OpenClaw Gateway** | ✅ OK | 4 days uptime, port 18789 |
| **System Resources** | ✅ OK | 4.4GB RAM free, CPU 0.54 |
| **TASI Bot (bot.py)** | 🟡 Running | PID 45176, active since May 31 |
| **Price Poller (poller.py)** | 🟡 Running | PID 89982, but BLIND (no live WS) |
| **Chromium (CDP 18801)** | ✅ Running | PID 55092, 2 tabs open |
| **Derayah Session** | 🟡 Unknown | Token expired 13+ days ago |
| **WebSocket Price Feed** | ❌ **BROKEN** | No live prices since ~10:46 AM |

### Market Status (Right Now)
- **TASI**: 11,025.41 (+15.89, +0.14%)
- **Market**: OPEN (10:00 AM – 3:10 PM)
- **Regime**: TRENDING (session +1.19%)
- **Capital**: 1,000.66 SAR (unchanged)
- **Open Positions**: NONE (all picks skipped due to stale prices)

---

## 🔴 ROOT CAUSE: WebSocket Price Feed Failure

### What Happened (Timeline)
| Time | Event |
|------|-------|
| **10:46 AM** | `ws_probe.py` finished its 30s capture and exited normally |
| **10:46 AM** | **WebSocket capture STOPPED** — no new data written to ws_frames_raw.log |
| **10:50 AM** | Keepalive detected stale token → attempted smart recovery |
| **10:55 AM** | CDP error: `No such target id` — page destroyed during recovery |
| **11:00 AM** | Keepalive created new tab but "Could not open TickerChart tab" |
| **Now** | TickerChart tab exists but WebSocket NOT streaming |

### The Chain of Failures

```
1. ws_probe.py exits after 30s capture
        ↓
2. No process is capturing CDP WebSocket frames
        ↓
3. poller.py's _ws_listener_loop() connected to tab but tab has NO active WS
        ↓
4. fetch_data() falls back to yfinance (15-min delayed)
        ↓
5. All picks show as "gapped above entry" (delayed price > entry zone)
        ↓
6. NO TRADES EXECUTED (system is being cautious — this is GOOD)
```

### Why WebSocket Is Not Streaming

**The TickerChart page loads but WebSocket connection fails because:**

1. **Token expired 13+ days ago** (`TC_DERAYAH` token from May 19)
2. **Page loads but can't authenticate** the WebSocket without valid token
3. **Derayah dashboard (`newonline.derayah.com`) is where auth happens**
4. **TickerChart (`derayah.tickerchart.net`) needs valid session cookies/tokens**

---

## 🖥️ SCREEN MONITORING ACCESS

### Chrome Remote Desktop (CRD)
- **Host**: ocean
- **PIN**: 056187
- **Status**: CRD host is running (PID found)
- **How to access**: Open Chrome browser on any device → visit `remotedesktop.google.com` → find "ocean" host

### Current Chromium Tabs
```
Tab 1: https://derayah.tickerchart.net/app/en (TickerChart — page loads but WS dead)
Tab 2: https://newonline.derayah.com/#/layout/trading/trading-portfolio (Portfolio)
Tab 3: about:blank
Tab 4: https://www.tradingview-widget.com/... (TradingView widget — iframe)
```

### What You Should See on Screen
1. **TickerChart tab**: Shows "Saudi Stock Market" with TASI index value
2. **BUT**: No live ticking prices, no WebSocket activity indicator
3. **If session were active**: You'd see real-time price updates, volume ticking

---

## 🔧 FIXES ALREADY APPLIED (by Mino)

### Option B: Fixed `poller.py` Tab Matching
**File**: `/home/mino/tasi-exec/poller.py`

**Changes:**
- `TC_URL` now matches broader `"tickerchart"` pattern
- Added `TC_FALLBACK_URLS = ["derayah.tickerchart.net", "tickerchart.net", "newonline.derayah.com"]`
- `_ws_listener_loop()` now searches ALL fallback patterns when finding tab
- CDP `/json` tab matching iterates through all patterns

**Why**: The old code only matched `derayah.tickerchart.net/app/en`, but after keepalive recovery the URL might be different.

### Option C: Fixed `derayah_keepalive.py` Tab Preservation
**File**: `/home/mino/tasi-exec/derayah_keepalive.py`

**Changes:**
1. **`cleanup_extra_tabs()`**: Now preserves ALL `tickerchart` tabs (not just `tickerchart.net`)
2. **`_recover_token_via_navigation()`**: Added "reactivate before close" logic
   - First tries to REACTIVATE existing tab (checks if WS is alive)
   - Only closes tab if reactivation fails
3. **Auto-reconnect logic**: Now prioritizes `ws_probe.py` restart FIRST
   - Step 1: Kill old ws_probe + restart fresh
   - Step 2: Verify ws_frames_raw.log is updating
   - Step 3: Only if ws_probe fails, try tab-level recovery
4. **Session expired handling**: REMOVED `kill_chromium()` call
   - Now preserves existing browser and just navigates tabs
   - Added warning that Playwright login opens separate window

**Why**: The keepalive was killing the browser and destroying the visible window, making monitoring impossible.

---

## 🎯 REMAINING ISSUE: WebSocket Still Not Streaming

### Current Tab State
```
Tab: https://derayah.tickerchart.net/app/en
Status: Page loads ✅
Title: "Derayah Trade"
Content: Shows "Saudi Stock Market" + TASI value
WebSocket: ❌ NOT CONNECTED
```

### Why WebSocket Won't Connect
The TickerChart page needs an **authenticated session**. Even though the page loads, the WebSocket handshake requires:
1. Valid `TC_DERAYAH` token in localStorage, OR
2. Valid session cookies from `newonline.derayah.com`

**The token expired 13 days ago. The session is likely expired.**

---

## 🛠️ RECOVERY OPTIONS

### Option A: Manual Login via CRD (Safest)
1. Connect to CRD (`remotedesktop.google.com` → "ocean" → PIN 056187)
2. Check if Derayah dashboard shows logged-in state
3. If logged out:
   - Navigate to `https://newonline.derayah.com`
   - Click "Login"
   - Enter credentials (National ID + Password)
   - Select Email OTP
   - Complete reCAPTCHA
   - Check email for OTP code
   - Enter OTP
4. Once logged in, click "Real Prices" to open TickerChart
5. Verify TickerChart shows live ticking prices

### Option B: Check if Auto-Login Works
The keepalive has `_auto_login_token_inject()` which:
1. Refreshes API token using refresh token
2. Navigates browser to Derayah trading page
3. Injects tokens into localStorage

**BUT**: This requires a valid refresh token in `/home/mino/tasi-exec/derayah_token_live.json`

### Option C: Restart Everything Fresh
If the browser is in a bad state:
1. Kill all Chrome processes
2. Clear browser cache/profile
3. Start fresh Chromium
4. Manually log in via CRD
5. Open TickerChart
6. Restart ws_probe.py and poller.py

---

## 📁 KEY FILES & LOCATIONS

### Core Trading System
```
/home/mino/tasi-exec/
├── bot.py                  # Main trading bot (PID 45176)
├── poller.py               # Price poller (PID 89982) — MODIFIED ✅
├── screener.py             # Morning screener
├── midscreen_ws.py         # Mid-day screener
├── derayah_keepalive.py    # Session keeper — MODIFIED ✅
├── ws_probe.py             # WebSocket probe — MODIFIED ✅
├── market_regime.py        # Regime classifier
├── capital_tracker.py      # Capital manager
├── derayah_api.py          # Order execution API
└── TASI_SYSTEM_REFERENCE.md # Documentation
```

### Logs
```
/home/mino/tasi-exec/
├── poller.log              # Price polling log (duplicated lines bug)
├── keepalive.log           # Session keepalive log
├── ws_probe.log            # WebSocket probe log
├── ws_frames_raw.log       # Raw WS frames (STALE since 10:46 AM)
├── ws_frames.json          # Parsed WS frames (STALE)
└── exec.log                # Execution log
```

### State Files
```
/home/mino/tasi-exec/
├── positions.json          # Open positions (empty — no open trades)
├── capital.json            # Capital: 1000.66 SAR
├── regime.json             # Current regime: TRENDING
├── picks.json              # Today's picks (5 symbols)
├── derayah_token_live.json # Auth tokens (expired)
└── keepalive_state.json    # Keepalive state
```

---

## ✅ VERIFICATION CHECKLIST

After fixing the issue, verify:

- [ ] TickerChart tab shows live ticking prices
- [ ] `ws_frames_raw.log` is updating with new timestamps
- [ ] `poller.log` shows "WS price" entries (not "WS cache miss")
- [ ] `fetch_data()` returns real-time prices
- [ ] System executes trades on valid signals

---

## 📞 SYSTEM CONTACT

- **Primary**: A A (Telegram: @AMAS989)
- **CRD Access**: `remotedesktop.google.com` → "ocean" → PIN 056187
- **Server**: Ocean (local Linux machine)

---

*Generated by Mino on 2026-06-02 11:21 GMT+3*

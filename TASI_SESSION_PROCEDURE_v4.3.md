# TASI Session Management — Configuration Reference v4.3
## Updated: 2026-06-10 21:00 GMT+3
## Changes from v4.2: 5-min refresh_cron (was 15-min) — see "v4.3 Changelog" at bottom

---

## 🏗️ New Architecture Components

### 1. Session Manager
**File**: `/home/mino/tasi-exec/derayah_session_manager.py`
**Purpose**: Core session lifecycle (Phase 1/2/3)

**Key Methods**:
```python
capture_tokens()      → Phase 1: Read browser localStorage
refresh_session()     → Phase 2: OAuth refresh + CDP navigation  
check_health()        → Phase 3: Detect failure, trigger recovery
auto_otp_recovery()     → Phase 3: 2 email OTP attempts
```

### 2. Telegram Commands
**File**: `/home/mino/tasi-exec/bot_commands.py`
**Purpose**: User-facing session commands

**Commands**:
```
/Login   → Phase 1: Capture tokens after manual browser login
/SS      → Full status report (all 3 phases)
```

### 3. WebSocket Data Flow (2026-06-09 Fix)
**File**: `/home/mino/tasi-exec/ws_keepalive_v2.sh`
**Purpose**: Monitor and restart `ws_probe.py` when needed

**What Changed**:
- **Before**: Checked `ws_frames.json` file size every 2 seconds (file only written at end of 90s run) → false "stuck" detection → killed process prematurely
- **After**: Checks `ws_frames_raw.log` file size (grows continuously during run) → accurate detection

**Data Flow**:
| Component | Writes To | When | Checked By |
|-----------|-----------|------|------------|
| `ws_probe.py` | `ws_frames.json` | End of 90s run | ❌ (not for keepalive) |
| `ws_probe.py` | `ws_frames_raw.log` | Every frame | ✅ Keepalive |
| `poller.py` | `ws_prices_*.jsonl` | Every price update | ✅ `/SS` command |

### 4. Position Tracking (2026-06-09 Fix)
**File**: `/home/mino/tasi-exec/bot.py`
**Purpose**: Net quantity tracking for manual trades

**What Changed**:
- `record_buy()`: Adds to existing position, recalculates weighted avg entry
- `record_sell()`: Reduces qty by sold amount, tracks realized P&L, only closes at qty=0
- Fees: Commission (0.05%) + VAT (15%) calculated on every manual trade
- `capital.json`: Updated immediately after buy/sell
- `CLOSE ALL`: Market sells all open positions (previously "not implemented")

**Why**: Old code overwrote position on new buy and closed entire position on any sell, causing missed market closes.

### 4. Chrome Profile (2026-06-09 Update)
**Active Profile**: `/home/mino/.config/google-chrome/derayah-live`
**Legacy Profile**: `/home/mino/.config/google-chrome/derayah-profile` (may have Chrome 148 freeze bug)

**History:**
- **2026-06-04**: Chrome 148 freeze bug discovered with `derayah-profile`
- **Solution**: Created `derayah-live` profile as workaround
- **Current**: `derayah-live` is the active profile used by `start-chrome.sh` and `bot.py`

**Files referencing profiles:**
| File | Profile Used |
|------|-------------|
| `start-chrome.sh` | `derayah-live` |
| `bot.py` | `derayah-live` |
| `ws_probe.py` | Uses CDP (port 18801), profile-agnostic |

### 5. Cron Refresh (v4.3 — 5-min interval, was 15-min)
**File**: `/home/mino/tasi-exec/derayah_refresh_cron.sh`
**Purpose**: Proactive SSO refresh + auto-recovery fallback

**Schedule**: `*/5 * * * *` (every 5 minutes, 24/7 — runs even off-hours to keep tokens warm)
**Logs**: `/home/mino/tasi-exec/refresh_cron.log`
**Crontab entry**: 
```bash
*/5 * * * * /home/mino/tasi-exec/derayah_refresh_cron.sh >> /home/mino/tasi-exec/refresh_cron.log 2>&1
```

**Why 5 minutes (not 15)?** — see "v4.3 Changelog"
- The SSO URL endpoint has a ~5 minute grace period after the access token's `exp` field passes (empirically verified 2026-06-10)
- 15-min cron missed this window: at 18:45 the token was 13 min past exp, beyond grace → 401 → auto-recovery needed
- 5-min cron ALWAYS catches the grace period (5 min < 5 min grace)
- Worst-case bot gap with 5-min cron: 5 min 24 sec (vs 15 min 24 sec previously)

**Flow per cron run:**
1. Decode `Derayah_accesstoken` exp from JWT
2. Call `GET /apispark/trade/TickerChartUrl` with Bearer access_token
3. **If 200 OK** (access alive OR within 5-min grace):
   - Parse SSO URL from response (contains opaque `tc_token`)
   - Navigate TC tab to SSO URL via CDP
   - Poll TC tab's `TC_DERAYAH` localStorage for up to 30s (new JWT)
   - Save fresh TC token to `/home/mino/tasi-exec/derayah_tokens.json`
   - Verify with `GET /trading/Portfolio/List` (expect 200)
4. **If 401** (access dead, past grace period):
   - Trigger Phase 3 auto-recovery: `auto_login_with_email_otp()`
   - Auto-fills creds from `/home/mino/.derayah-creds` (chmod 600)
   - Submits form, selects Email radio, fetches OTP from Mino's IMAP inbox
   - Submits OTP, captures fresh tokens
   - Bails to manual login + Telegram DM ONLY if reCAPTCHA challenge is detected
   - Typical recovery: 24 sec
5. **Phase 3 in `derayah_refresh_cron.sh`** — see file

---

## 🔐 Token Storage

**File**: `/home/mino/tasi-exec/derayah_tokens.json`

```json
{
  "Derayah_accesstoken": "eyJ...",
  "Derayah_refreshtoken": "def...",
  "TC_DERAYAH": "abc...",
  "sso_url": "https://sso.derayah.com/...",
  "captured_at": "2026-06-08T20:46:00",
  "last_refreshed": "2026-06-08T21:30:00",
  "expires_in": 3600,
  "tc_expiry": "2026-06-08T22:30:00",
  "tc_remaining_min": 59.7
}
```

---

## 🛡️ Trade Protection

### Session Validation (Before Every Trade)
```python
# bot.py + poller.py
from bot_commands import validate_session

is_valid, msg = validate_session()
if not is_valid:
    return {
        "success": False,
        "message": "🚫 Session expired. Run /Login first."
    }
```

### What Gets Blocked
- Manual `/buy` commands
- Auto-buy from poller
- Position sync
- Capital refresh

### What Doesn't Get Blocked
- `/SS` status check
- `/help` command
- Market regime check
- Non-trading operations

---

## ⏰ Cron Schedule

| Cron | Time | Action |
|------|------|--------|
| `*/5 * * * *` | Every 5 min, 24/7 | SSO refresh + auto-recovery (v4.3) |
| `55 9 * * 0-4` | 9:55 AM Sun–Thu | Cleanup stale files |

> **v4.3 change**: was `*/15 * * * *` before 2026-06-10 21:00. 5-min interval ensures the 5-min SSO grace period is always caught.

---

## 📊 Health Check Points

| Check | Frequency | Action on Failure |
|-------|-----------|-------------------|
| Token validity | Every 5 min (cron) | Attempt SSO refresh → if 401, auto-recover via email OTP |
| TC tab alive | Every 5 min (cron) | Open new tab via CDP if SSO navigation fails |
| API test | Before each trade | Block trade, notify user |
| localStorage | Phase 1 capture | Re-read after manual login |

---

## 🔄 Phase 3: Recovery Flow

```
Refresh attempt fails
    ↓
Open login tab automatically (CDP)
    ↓
Attempt OTP via email (2 tries)
    ↓
├─→ Success: Capture tokens, resume
│
└─→ Fail/Captcha: 
    ├─ Send Telegram notification to A A
    ├─ Set state to STAND BY (in-memory only)
    ├─ NO stand_down file created
    └─ Wait for manual /Login command
```

---

## 🎯 RACI Matrix (Updated)

| Activity | A A | Mino | bot.py | Session Mgr | WS Keepalive | Watchdog |
|----------|-----|------|--------|-------------|--------------|----------|
| Phase 1: Login | R,A | C | R | I | I | I |
| Phase 2: Refresh | I | C | I | R,A | C | I |
| Phase 3: Detect | I | C | I | R | I | A |
| Phase 3: Notify | I | R | R | R | I | I |
| Phase 3: Re-login | R,A | C | I | I | I | I |
| Trade Execution | A | C | R | I | C | I |
| Session Validation | I | I | R | I | I | A |

**Legend**: R=Responsible, A=Accountable, C=Consulted, I=Informed

---

## 🔍 Monitoring & Debugging

### Check Session Status
```bash
# Manual check
curl -s http://127.0.0.1:8188/system_stats | jq .system.state

# Via Telegram
/SS
```

### View Refresh Logs
```bash
tail -f /home/mino/tasi-exec/refresh_cron.log
```

### Check Token File
```bash
cat /home/mino/tasi-exec/derayah_tokens.json | python3 -m json.tool
```

### Test Phase 1 (Capture)
```bash
cd /home/mino/tasi-exec
python3 -c "from derayah_session_manager import SessionManager; sm = SessionManager(); sm.capture_tokens()"
```

### Test Phase 2 (Refresh)
```bash
cd /home/mino/tasi-exec
python3 -c "from derayah_session_manager import SessionManager; sm = SessionManager(); sm.refresh_session()"
```

### Test Phase 3 (Health)
```bash
cd /home/mino/tasi-exec
python3 -c "from derayah_session_manager import SessionManager; sm = SessionManager(); print(sm.check_health())"
```

---

## ⚠️ Known Limitations

| Issue | Impact | Mitigation |
|-------|--------|------------|
| Refresh token expires ~2.5h | SSO refresh fails after 2.5h | 5-min cron + auto-recovery via email OTP |
| Captcha on OTP | Auto-OTP blocked | Fallback to manual `/Login` |
| CDP port dependency | Session manager needs Chrome | Port 18801 must be open |
| localStorage cleared | Tokens lost on expiry | 5-min cron refreshes before 5-min grace window closes |
| 5-min SSO grace period | SSO 401s after 5 min past exp | 5-min cron interval < 5-min grace → always caught |

---

## 📝 Change Log

| Date | Change | File |
|------|--------|------|
| 2026-06-10 | **v4.3**: 5-min refresh_cron (was 15-min) | `crontab` |
| 2026-06-10 | **v4.3**: Added 5-min SSO grace period finding | `derayah_refresh_cron.sh` (comment) |
| 2026-06-10 | **v4.3**: `_close_extra_tabs()` cleanup (tracker domains) | `derayah_session_manager.py` |
| 2026-06-10 | **v4.3**: Auto-recovery via email OTP (8 new methods) | `derayah_session_manager.py` |
| 2026-06-10 | **v4.3**: `setup-derayah-creds.sh` for one-time creds setup | NEW file |
| 2026-06-09 | Session manager v4.2 created | `derayah_session_manager.py` |
| 2026-06-09 | Bot commands created | `bot_commands.py` |
| 2026-06-09 | Poller validation added | `poller.py` |
| 2026-06-09 | Bot.py commands added | `bot.py` |
| 2026-06-09 | RACI updated | `raci_matrix.html` |
| 2026-06-09 | Blueprint updated | `TASI_SYSTEM_BLUEPRINT.md` |

---

## v4.3 Changelog (2026-06-10)

### Background
On 2026-06-10, the access token died at 18:31 and the next 15-min cron at 18:45 missed the SSO grace window (13 min past exp). Auto-recovery triggered and brought the system back at 18:45:25 (14 min gap). Same gap had happened the night before. The 15-min interval was too coarse.

### Investigation (2026-06-10 18:55–20:55)
- Confirmed: poller (poller.py:275) and bot (derayah_api.py:60) both use `TC_DERAYAH` (the 60-min JWT). Both depend on SSO URL endpoint for refresh.
- Confirmed: OAuth refresh_token grant is DEAD since 2026-05-19 (server-side rotation).
- **Discovery**: The SSO URL endpoint has a **~5 minute grace period** after the access token's `exp` field passes. Empirically tested at 19:45:20: HTTP 200 at T+0s and T+296s, first 401 at T+326s. Test log: `/tmp/grace_period_test2.log` and `/tmp/grace_period_test3.log`.
- The 18:45 cron failure was because the token was 13m22s past exp — well beyond the 5-min grace.

### Change Applied
- **Crontab**: `*/15 * * * *` → `*/5 * * * *` for `derayah_refresh_cron.sh`
- **No code changes** to the refresh script — the existing flow already handles SSO success, 401 fallback to auto-recovery, and reCAPTCHA bail-to-manual correctly.
- **Backup**: `/tmp/crontab.before-5min.bak`

### Expected Behavior
- During normal operation: 12 SSO refresh calls per hour, all 200 OK (no recovery needed, just fresh TC tokens).
- When access token is in grace period: 200 OK → fresh TC token → no recovery.
- When access token past grace period: 401 → auto-recovery (24 sec) → fresh tokens.
- **Worst-case bot gap**: 5 min 24 sec (down from 15 min 24 sec).
- **Typical bot gap**: 0 sec (grace period catches it).
- **Cost**: 3x more cron runs (12/hr vs 4/hr). Each run is ~1-2 sec. Negligible.
- **reCAPTCHA risk**: same as before (~1-2% per recovery attempt). Bails to manual + Telegram DM if hit.

### Pre-existing Features (built 2026-06-10, documented in v4.3)
- `auto_login_with_email_otp()` — 8 new methods in `derayah_session_manager.py` for full automated re-login via email OTP
- `setup-derayah-creds.sh` — one-time creds setup, creates `/home/mino/.derayah-creds` (chmod 600)
- `_close_extra_tabs()` — closes tracker tabs (doubleclick, tiktok, snapchat, etc.) on every refresh
- False-positive CAPTCHA detection replaced with widget-based detection (iframe/bubble, not string-grep)

### What I am NOT doing
- NOT extending the server-side 60-min TTL (impossible from client)
- NOT removing the 15-min cron entry from system crontab as a backup (it doesn't exist — only the 5-min entry is in crontab now)
- NOT adding OAuth refresh deep-dive (confirmed dead, not worth pursuing)
| 2026-06-08 | Backlog updated | `v4.2_enhancement_backlog.md` |
| 2026-06-09 | Position tracking + fees fix | `bot.py` |
| 2026-06-09 | CLOSE ALL implemented | `bot.py` |
| 2026-06-09 | Poller BASE_DIR NameError fix | `poller.py` |
| 2026-06-09 | WebSocket keepalive fix | `ws_keepalive_v2.sh` |
| 2026-06-09 | /SS command data check fix | `bot_commands.py` |
| 2026-06-09 | Derayah refresh cron syntax fix | `crontab` (`**/50` → `*/50`) |
| 2026-06-09 | Chrome profile documentation | `TASI_SYSTEM_BLUEPRINT.md`, `TASI_SYSTEM_REFERENCE.md` |

---

**Next Test**: Tomorrow 09:50 KSA (pre-market)
**Owner**: Mino + A A
**Version**: v4.2

---

## v4.3.1 Update (2026-06-10 21:33) — Token sync + CDP tab navigation bugs

### Issue
1. **`_cdp_new_tab()` bug**: Chrome's `/json/new?url=...` does NOT honor the `url` query param. New tabs always open at `about:blank`. Auto-recovery at 21:10 created 2 orphan tabs at `about:blank#/signin` instead of `https://onboarding.derayah.com/#/signin`.
2. **Cron only updated `TC_DERAYAH`**: After successful SSO refresh, the JSON file got the new TC token but not the new `Derayah_accesstoken`. The dashboard tab's localStorage had the fresh access token, but the cron never read it. Next cron run used the stale file token → false 401 → auto-recovery triggered.

### Fix
1. **`_cdp_new_tab()` in `derayah_session_manager.py`**: After creating the tab via `PUT /json/new`, explicitly call `_cdp_navigate(ws_url, url)` to actually go to the URL. The `params={"url": ...}` argument is removed since it never worked.

2. **New `sync_tokens_from_browser()` method in `derayah_session_manager.py`**: 
   - Reads `Derayah_accesstoken` + `Derayah_refreshtoken` from dashboard tab localStorage
   - Reads `TC_DERAYAH` from TC tab localStorage (JSON format)
   - Compares JWT `exp` claims, writes freshest to JSON file
   - Returns `{"updated": [...], "kept": [...], "errors": [...]}`

3. **`derayah_refresh_cron.sh` — `sso_refresh()` start**: Calls `sync_tokens_from_browser()` BEFORE the SSO URL call. The live dashboard token is now used (not the stale JSON file).

4. **`derayah_refresh_cron.sh` — SSO success branch**: Calls `sync_tokens_from_browser()` after TC token refresh, to catch any new access token.

5. **`auto_login_with_email_otp()` success branch**: Calls `sync_tokens_from_browser()` after `capture_tokens()`, so JSON file is fully current after a full re-login.

### Verification (2026-06-10 21:32)
End-to-end test:
```
21:32:30   Syncing tokens from browser (source of truth)...
            Updated: Derayah_refreshtoken
            Kept:    Derayah_accesstoken, TC_DERAYAH
21:32:31   SSO URL response: 200
21:32:39   ✅ New TC exp: 1781119959 (+27m)
21:32:41 ✅ Session refresh completed
```
- Total runtime: 12 sec
- 0 orphan tabs created
- 2 stable tabs (1 dashboard + 1 TC)
- All tokens in sync between browser localStorage and JSON file

### Files Modified
| File | Changes |
|------|---------|
| `derayah_session_manager.py` | `_cdp_new_tab()` fix, new `sync_tokens_from_browser()`, `_jwt_exp()` helper, base64 import fix, `auto_login_with_email_otp()` post-success sync |
| `derayah_refresh_cron.sh` | Sync at start of `sso_refresh()`, sync after SSO success |

### Why This Matters
The 5-min cron alone isn't enough if the cron uses stale data. Now the cron uses the browser (source of truth) for the access token, and updates both tokens in the JSON file after every successful refresh. **The JSON file is no longer the bottleneck.**

---

## v4.3.2 Update (2026-06-10 21:38) — TC tab activation

### Change
After SSO navigation and after auto-recovery success, the TC tab is now brought to the foreground via CDP `Page.bringToFront`.

### Why
Vue 3 / SPA apps may throttle or pause WebSocket connections when the tab loses focus. Keeping the TC tab active ensures ws_probe.py's price feed stays continuous.

### Files Modified
| File | Changes |
|------|---------|
| `derayah_session_manager.py` | New `_activate_tab(tab_id)` helper, called in `_navigate_tc_to_sso()` and `auto_login_with_email_otp()` success branch |

---

## v4.3.3 Update (2026-06-11 22:20) — Tab deduplication fix

**Problem:** Chrome accumulated duplicate tabs over time (7 tabs instead of 2):
- 3× dashboard tabs (from repeated SSO refreshes)
- 1× TickerChart tab ✅
- 1× AppDynamics tracker (not in close list)
- 2× Omnibox Chrome UI artifacts

**Root cause:** `_close_extra_tabs()` kept ALL tabs matching keeper patterns instead of deduplicating.

**Fix:** Enhanced `_close_extra_tabs()` in `derayah_session_manager.py`:
1. Groups tabs by keeper pattern (dashboard, TC, signin)
2. Keeps only the **most recently active** tab per group:
   - Primary: checks `document.visibilityState == 'visible'` (foreground tab)
   - Fallback: highest tab ID (Chrome assigns monotonically increasing IDs)
3. Closes all duplicate keeper tabs + tracker tabs

**Result:** After SSO refresh, exactly 2 tabs remain:
- 1× active dashboard (newonline.derayah.com)
- 1× active TC trading (derayah.tickerchart.net)

### Files Modified
| File | Changes |
|------|---------|
| `derayah_session_manager.py` | `_close_extra_tabs()` deduplication logic — keep active tab, close duplicates |

---

## v4.3.4 Update (2026-06-12 01:25) — Double logging fix

**Problem:** Every log line appeared twice in `refresh_cron.log`:
```
2026-06-12 01:15:01 === Derayah Refresh Cron (SSO Navigation v2) ===
2026-06-12 01:15:01 === Derayah Refresh Cron (SSO Navigation v2) ===
2026-06-12 01:15:01 === SSO refresh attempt ===
2026-06-12 01:15:01 === SSO refresh attempt ===
```

**Root cause:** The `log()` function used `tee -a "$LOG_FILE"` which writes to both stdout AND the log file. The crontab also redirects stdout to the same log file (`>> /home/mino/tasi-exec/refresh_cron.log 2>&1`). This caused every log line to be written twice:
1. Once by `tee` inside the script
2. Once by the shell redirect from cron

**Fix:** Changed `log()` function and all `tee` calls to append directly to the log file:
```bash
# Before:
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

# After:
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}
```

Also removed `| tee -a "$LOG_FILE"` from Python script output lines that already had `2>>"$LOG_FILE"`.

### Files Modified
| File | Changes |
|------|---------|
| `derayah_refresh_cron.sh` | `log()` function: `tee` → `>>` ; 3× `| tee -a "$LOG_FILE"` removed |

---

*End of procedure updates*

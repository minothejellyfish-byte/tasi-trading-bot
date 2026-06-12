# Session Management Issues & Fixes Log
## 2026-06-09 01:10 GMT+3

---

## Issue #1: Bot Commands Not Responding

**Problem:** `/Login` and `/SS` commands not working in Telegram group

**Root Cause:**
1. `is_authorized()` only accepted messages from group chat (`-5235925419`)
2. Direct messages from `OWNER_ID` (5529987063) were rejected

**Fix:**
```python
def is_authorized(update: Update) -> bool:
    msg = update.message
    if msg.from_user and msg.from_user.id == OWNER_ID:
        return True
    if msg.chat_id != GROUP_CHAT_ID:
        return False
    # ... rest of function
```

**Result:** ✅ Fixed — now accepts commands from owner in any chat

---

## Issue #2: Handler Registration Order

**Problem:** Command handlers not triggering even after authorization fix

**Root Cause:** `MessageHandler` registered BEFORE `CommandHandler`
- MessageHandler with `~filters.COMMAND` should exclude commands
- But handler order matters in python-telegram-bot

**Fix:**
```python
# Register CommandHandler FIRST
if SESSION_ENABLED:
    app.add_handler(CommandHandler("Login", handle_login_command))
    app.add_handler(CommandHandler("SS", handle_status_command))
    log.info("Session commands enabled: /Login, /SS")

# Then add message handler for regular commands
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
```

**Result:** ✅ Fixed — commands now trigger session handlers

---

## Issue #3: Import Errors

**Problem:** `cannot import name 'validate_session' from 'bot_commands'`

**Root Cause:**
- `validate_session()` function defined after `SessionCommands` class in `bot_commands.py`
- Bot tried to import before function existed

**Fix:** Reordered functions in `bot_commands.py`:
1. `validate_session()` — defined first
2. `handle_login()` / `handle_status()` — standalone functions
3. `SessionCommands` class — wrapper for backward compatibility

**Result:** ✅ Fixed — all imports work

---

## Issue #4: Duplicate Content in bot_commands.py

**Problem:** File had duplicate `validate_session` function inside and outside `SessionCommands` class

**Root Cause:** Multiple edits created malformed file with duplicate sections

**Fix:** Cleaned up file structure:
- Removed duplicate `validate_session` from inside `SessionCommands`
- Removed HTML tags from messages (Telegram Markdown mode)

**Result:** ✅ Fixed — no more "Message text is empty" errors

---

## Issue #5: Cron Refresh Failing with invalid_client

**Problem:** API refresh (`/connect/token`) fails with `{"error":"invalid_client"}`

**Root Cause:**
- `client_id: "NewWebClient"` rejected by OAuth server
- Refresh token (64 chars) might be invalid or expired
- API endpoint requires different client_id for refresh

**Fix:** Switched to SSO navigation approach:
```python
# OLD (broken):
# POST /connect/token with refresh_token → 400 invalid_client

# NEW (working):
# GET /apispark/trade/TickerChartUrl with Bearer token → 200 OK
# Navigate TC tab to SSO URL → captures fresh tokens
```

**Test Result:**
```
✅ SSO URL acquired (249 chars)
✅ TC tab navigated to SSO URL
✅ Tokens captured — TC: 2754 chars, Access: 3135 chars
✅ Session refresh completed successfully
```

**Result:** ✅ Fixed — cron now uses SSO navigation

---

## Issue #6: Bot Keeps Stopping

**Problem:** Bot process dies every few minutes

**Root Cause:**
- Multiple bot instances running simultaneously
- Old instances consuming getUpdates → Conflict errors
- Parent process (OpenClaw node) sending SIGTERM

**Fix:**
```bash
# Kill all instances first
ps aux | grep "python3.*bot.py" | awk '{print $2}' | xargs -r kill -9

# Start with setsid to detach from parent
setsid python3 bot.py >> exec.log 2>&1 < /dev/null &
disown
```

**Result:** ✅ Fixed — single instance running stably

---

## Issue #7: Message Text Empty Error

**Problem:** `telegram.error.BadRequest: Message text is empty`

**Root Cause:**
- `handle_login()` in `bot_commands.py` sends messages directly via `reply_text()`
- `bot.py` tried to call `reply_text()` again on the return value (None)
- `result = await sc.handle_login(update, ctx)` returns None

**Fix:** Updated `bot.py` to not call `reply_text()` on session handler results:
```python
# OLD:
result = await sc.handle_login(update, ctx)
await update.message.reply_text(result, parse_mode="HTML")

# NEW:
await sc.handle_login(update, ctx)
```

**Result:** ✅ Fixed — no more empty message errors

---

## Files Modified

| File | Changes |
|------|---------|
| `bot.py` | is_authorized() fix, handler order fix, no double reply_text |
| `bot_commands.py` | Reordered functions, removed duplicates, removed HTML tags |
| `derayah_refresh_cron.sh` | Switched from API refresh to SSO navigation |

## Current Status

| Component | Status |
|-----------|--------|
| `/Login` command | ✅ Working |
| `/SS` command | ✅ Working |
| Token capture | ✅ Working (3135 chars access, 2754 chars TC) |
| SSO refresh | ✅ Working (249 chars SSO URL) |
| 50-min cron | ✅ Updated, ready for market hours |
| Bot stability | ✅ Single instance, no crashes |

## Next Steps

1. Pre-market (09:00 KSA): Test `/Login` after fresh manual login
2. Market open (10:00 KSA): Monitor first 50-min cron execution
3. Verify tokens are refreshed and saved to `derayah_tokens.json`
4. Check `refresh_cron.log` for any errors

---
*Session: 2026-06-09 00:58–01:10 GMT+3*

---

# Session Management Updates — 2026-06-10

## Update #1: 5-min Refresh Cron (was 15-min)

**Problem:** On 2026-06-09, the access token died at ~14:30 and the system didn't recover until Amin manually intervened. On 2026-06-10, the access token died at 18:31 and the next 15-min cron at 18:45 missed the SSO grace window (13 min past exp), requiring 14 min of bot downtime while auto-recovery triggered.

**Root Cause:**
- The SSO URL endpoint has a **~5 minute grace period** after the access token's `exp` field passes (empirically verified 2026-06-10 19:45:20–19:59:50, 8 sequential 200 OK responses, first 401 at T+326s)
- 15-min cron interval is too coarse — frequently missed the 5-min grace window
- The 18:45 failure was 13m22s past exp, well beyond the grace period
- The 19:45 success was 19 sec BEFORE exp, which is why it worked

**Fix Applied (2026-06-10 21:00):**
- Crontab: `*/15 * * * *` → `*/5 * * * *` for `derayah_refresh_cron.sh`
- Backup at `/tmp/crontab.before-5min.bak`
- No code changes needed — the existing refresh script already handles SSO 200/401 correctly

**Expected Result:**
- Worst-case bot gap: 5 min 24 sec (down from 15 min 24 sec)
- Typical bot gap: 0 sec (grace period catches it)
- Cost: 12 cron runs/hr vs 4/hr, ~1-2 sec each, negligible
- reCAPTCHA risk: same as before, bails to manual

## Update #2: Auto-Recovery via Email OTP (new feature)

**Problem:** When SSO URL 401s, the old refresh_cron would just send "manual login needed" Telegram DMs. User had to manually log in 24/7 to keep the system alive.

**Solution:** Built full automated re-login flow that:
1. Reads creds from `/home/mino/.derayah-creds` (chmod 600)
2. Fills the onboarding signin form via CDP
3. Selects Email radio (OTP via email, not SMS)
4. Submits, waits for OTP input
5. IMAP-polls Mino's Gmail inbox for OTP from `ccr@derayah.com`
6. Fills 4 separate digit inputs (maxLength=1 each)
7. Submits, captures fresh tokens from resulting dashboard tab

**Files Added/Modified:**
- `/home/mino/tasi-exec/derayah_session_manager.py` — added 8 new methods (~43KB total)
  - `_detect_recaptcha_challenge(ws_url)` — widget-based, NOT string-grep
  - `_find_signin_tab()`, `_wait_for_signin_ready()`, `_fill_signin_form()`
  - `_wait_for_otp_input()`, `_fill_otp()`
  - `_fetch_otp_from_email()` — IMAP poll, regex-extract 4-6 digit code
  - `_wait_for_login_complete()`
  - `auto_login_with_email_otp()` — full orchestration
- `/home/mino/tasi-exec/setup-derayah-creds.sh` — NEW, one-time creds setup
- `/home/mino/tasi-exec/derayah_refresh_cron.sh` — Phase 3 auto_recover() integration

**Tested:**
- ✅ Real end-to-end: 17:31 login, OTP `9192` fetched from IMAP, all 4 digits filled correctly
- ✅ SSO refresh: 17:37, 17:40, 17:43 — 100% success rate
- ✅ reCAPTCHA detection: 0% false positive on live signin page
- ✅ 5-min cron: 21:00 run, 200 OK, fresh TC token saved

## Update #3: Tab Accumulation Fix

**Problem:** Concern that auto-recovery's onboarding tab might accumulate over time.

**Solution:** Added `_close_extra_tabs()` to `derayah_session_manager.py`:
- Default `keep_patterns`: `derayah.tickerchart.net`, `newonline.derayah.com`, `onboarding.derayah.com`
- Default `close_patterns`: `doubleclick.net`, `tiktok.com`, `snapchat.com`, `facebook.com`, `fbcdn.net`, `linkedin.com`, `licdn.com`, `twitter.com`, `ads-twitter.com`, `google-analytics.com`, `googletagmanager.com`, `platformance.io`, `appdynamics.com`

**Wired into:**
- `auto_login_with_email_otp` success branch
- `derayah_refresh_cron.sh` after SSO success

**Verified:** 4 consecutive cron runs, tab count stable at 2 (TC + dashboard). 0 closed when clean.

## Update #4: False-Positive CAPTCHA Detection Removed

**Old:** `grep -i "captcha" /path/to/onboarding.html` — always matched the reCAPTCHA script tag → always false-positive

**New:** Checks for the actual reCAPTCHA challenge widget:
```python
def _detect_recaptcha_challenge(ws_url):
    return sm._cdp_eval(ws_url, '''
        !!document.querySelector('iframe[src*="recaptcha"]') ||
        !!document.querySelector('iframe[title*="reCAPTCHA"]') ||
        !!document.querySelector('.g-recaptcha-bubble')
    ''')
```

**Result:** 0% false positive on live signin page. Bails to manual ONLY when actual reCAPTCHA challenge is shown.

---

*Updated: 2026-06-10 21:00 GMT+3*

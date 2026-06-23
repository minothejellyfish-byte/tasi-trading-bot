#!/bin/bash
# =============================================================================
# Derayah Session Refresh Cron Script
# =============================================================================
# Phase 2: Proactive token refresh via SSO navigation.
#
# Updated 2026-06-10 15:50 — root-cause rewrite
#
# The previous version was a "SSO navigation" approach that:
#   - Used the broken OAuth refresh_token grant (always returns 400 invalid_grant)
#   - Treated a string match on "captcha" in the onboarding page as a CAPTCHA
#     (false positive — the reCAPTCHA script tag is ALWAYS in the page)
#   - Never actually recovered; just sent "manual login needed" notifications
#
# REALITY (verified 2026-06-10):
#   - OAuth refresh_token is permanently dead on this OAuth client. Has been
#     since May 19. The "Token refreshed via API" success log from that day
#     was a one-off before the client config changed.
#   - The ONLY way to refresh tokens is SSO navigation:
#       1. Have a valid Derayah_accesstoken (1-hour lifetime)
#       2. Call GET /apispark/trade/TickerChartUrl with that token
#       3. Get a fresh SSO URL
#       4. Navigate the TC tab to that URL
#       5. The TC tab reloads, sets a fresh TC_DERAYAH localStorage entry
#       6. We re-read TC_DERAYAH from localStorage
#   - When the access_token itself expires (60 min from capture), there is NO
#     way to auto-recover. User MUST do manual login, and we MUST then call
#     capture_tokens() to get fresh tokens.
#   - The 15-min refresh_cron should be conservative: only run SSO refresh if
#     the access_token is still valid (or close to valid).
#
# BEHAVIOR:
#   - 0-50 min after capture: SSO URL fetch works → SSO navigation refreshes
#     the TC token. No user action needed.
#   - 50-60 min after capture: SSO URL starts returning 401. We're in the
#     "danger zone". Cron tries email-OTP auto-recovery if ~/.derayah-creds
#     exists and Gmail forwarding is configured.
#   - >60 min after capture: All tokens dead. Auto-recovery attempts full
#     re-login via signin form + email OTP (auto-fetches via Mino's IMAP).
#     If reCAPTCHA is shown, bails to manual login + Telegram DM.
# =============================================================================

set -uo pipefail

SCRIPT_DIR="/home/mino/tasi-exec"
LOG_FILE="/home/mino/tasi-exec/refresh_cron.log"
TOKEN_FILE="/home/mino/tasi-exec/derayah_tokens.json"

# ─── Logging ──────────────────────────────────────────────────────────────────
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

# ─── Telegram notification (real, not the placeholder from the old version) ──
notify_user() {
    local severity="$1" message="$2"
    local bot_token="***"
    local chat_id="5529987063"  # Amin's DM
    
    # Use proper URL encoding via python (curl + jq not always available)
    python3 - <<PYEOF 2>>"$LOG_FILE"
import json, urllib.request, urllib.parse, sys, datetime
try:
    bot_token = "***"
    chat_id = "$chat_id"
    severity = """$severity"""
    message = """$message"""
    text = f"""{severity} Derayah Session: {message}

Auto-recovery failed. Please:
1. Open Chrome on Ocean (or SSH in: ssh ocean)
2. Click the Derayah signin tab
3. Enter your username + password
4. (If OTP) enter the SMS code
5. Reply to this DM with: /Login

Until then, TASI pre-market/position sync will fail with 401s.
"""
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=data
    )
    urllib.request.urlopen(req, timeout=10)
    print(f"{datetime.datetime.now().isoformat()}  Telegram notification sent successfully")
except Exception as e:
    print(f"{datetime.datetime.now().isoformat()}  Telegram notify failed: {e}", file=sys.stderr)
PYEOF
}

# ─── Decode JWT expiry without external deps ────────────────────────────────
# Usage: exp=$(jwt_exp "$token")
# Echoes the unix epoch seconds, or 0 on failure.
jwt_exp() {
    local token="$1"
    python3 -c "
import sys, base64, json
t = '''$token'''.split('.')
if len(t) == 3:
    try:
        pad = t[1] + '=' * (-len(t[1]) % 4)
        print(int(json.loads(base64.urlsafe_b64decode(pad.encode())).get('exp', 0)))
        sys.exit(0)
    except Exception:
        pass
print(0)
" 2>/dev/null
}

# ─── Phase 2: SSO navigation refresh (the ONLY working path) ────────────────
sso_refresh() {
    log "=== SSO refresh attempt ==="
    
    # Read tokens
    if [[ ! -f "$TOKEN_FILE" ]]; then
        log "❌ No token file — manual login needed"
        return 1
    fi
    
    local access_tok tc_tok access_exp tc_exp now remaining_min
    access_tok=$(python3 -c "import json; print(json.load(open('$TOKEN_FILE'))['Derayah_accesstoken'])" 2>/dev/null)
    tc_tok=$(python3 -c "import json; print(json.load(open('$TOKEN_FILE')).get('TC_DERAYAH',''))" 2>/dev/null)
    now=$(date +%s)
    access_exp=$(jwt_exp "$access_tok")
    tc_exp=$(jwt_exp "$tc_tok")
    
    if [[ -z "$access_tok" || "$access_tok" == "None" ]]; then
        log "❌ No access token in file — manual login needed"
        return 1
    fi
    
    # Calculate remaining time on the access token
    if [[ "$access_exp" -gt 0 ]]; then
        remaining_min=$(( (access_exp - now) / 60 ))
        log "  Access token expires in ${remaining_min} min"
    else
        remaining_min=-1
        log "  ⚠️ Could not decode access token expiry"
    fi
    
    # ─── Step 1: Read from DASHBOARD localStorage FIRST ──────────────────────
    log "  Checking dashboard tab for fresh token..."
    local dash_token
    dash_token=$(python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from derayah_session_manager import SessionManager
import base64, json, time

sm = SessionManager()
tabs = sm._cdp_list_tabs()
dash = sm._find_dashboard_tab(tabs)
if dash:
    ws = dash.get('webSocketDebuggerUrl')
    token = sm._cdp_eval(ws, \"localStorage.getItem('Derayah_accesstoken') || ''\")
    if token and len(token) > 100:
        parts = token.split('.')
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
        exp = payload.get('exp', 0)
        remaining = (exp - time.time()) // 60
        print(f'TOKEN_FOUND|{exp}|{remaining}')
    else:
        print('NO_TOKEN')
else:
    print('NO_DASHBOARD_TAB')
" 2>>"$LOG_FILE")
    
    local dash_token_found=false
    local dash_token_exp=0
    if [[ "$dash_token" == TOKEN_FOUND* ]]; then
        IFS='|' read -r _ dash_token_exp dash_remaining <<< "$dash_token"
        log "  ✅ Dashboard token found (exp ${dash_remaining} min)"
        dash_token_found=true
        access_exp=$dash_token_exp
        remaining_min=$dash_remaining
        # Re-read token from dashboard for use
        access_tok=$(python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from derayah_session_manager import SessionManager
sm = SessionManager()
tabs = sm._cdp_list_tabs()
dash = sm._find_dashboard_tab(tabs)
if dash:
    ws = dash.get('webSocketDebuggerUrl')
    print(sm._cdp_eval(ws, \"localStorage.getItem('Derayah_accesstoken') || ''\"))
" 2>/dev/null)
    else
        log "  ℹ️ No dashboard token (reason: ${dash_token:-unknown})"
    fi

    # ─── Sync from browser FIRST (browser is source of truth) ─────────────────
    # The dashboard tab may have a fresher Derayah_accesstoken than the JSON
    # file. Reading it here avoids false 401s when the file is stale.
    log "  Syncing tokens from browser (source of truth)..."
    local sync_out
    sync_out=$(python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from derayah_session_manager import SessionManager
sm = SessionManager()
sync = sm.sync_tokens_from_browser()
if sync.get('updated'):
    print(f'    Updated: ' + ', '.join(sync['updated']))
if sync.get('kept'):
    print(f'    Kept:    ' + ', '.join(sync['kept']))
if sync.get('errors'):
    for e in sync['errors']:
        print(f'    Err:     {e}')
" 2>>"$LOG_FILE")
    [[ -n "$sync_out" ]] && echo "$sync_out" >> "$LOG_FILE"
    
    # Re-read after sync
    access_tok=$(python3 -c "import json; print(json.load(open('$TOKEN_FILE'))['Derayah_accesstoken'])" 2>/dev/null)
    access_exp=$(jwt_exp "$access_tok")
    if [[ "$access_exp" -gt 0 ]]; then
        remaining_min=$(( (access_exp - now) / 60 ))
        log "  After sync: access expires in ${remaining_min} min"
    fi
    
    # ─── Try SSO URL ──────────────────────────────────────────────────────────
    local sso_resp
    sso_resp=$(python3 -c "
import json, requests
tokens = json.load(open('$TOKEN_FILE'))
headers = {
    'Authorization': f'Bearer {tokens[\"Derayah_accesstoken\"]}',
    'Accept': 'application/json',
    'Origin': 'https://newonline.derayah.com',
    'Referer': 'https://newonline.derayah.com/',
}
try:
    r = requests.get(
        'https://api.derayah.com/apispark/trade/TickerChartUrl',
        headers=headers, timeout=10, allow_redirects=False
    )
    # Print status on line 1, full body on subsequent lines (no truncation)
    print(f'STATUS:{r.status_code}')
    print(r.text)
except Exception as e:
    print('STATUS:0')
    print(f'EXC:{e}')
" 2>>"$LOG_FILE")
    
    local sso_code="${sso_resp%%$'\n'*}"
    sso_code="${sso_code#STATUS:}"
    local sso_body="${sso_resp#*$'\n'}"
    
    log "  SSO URL response: $sso_code"
    
    if [[ "$sso_code" != "200" ]]; then
        log "  ❌ SSO URL failed (HTTP $sso_code) — access token is invalid or expired"
        # Don't return yet — fall through to recovery notification
        return 2
    fi
    
    # Parse SSO URL
    local sso_url
    sso_url=$(echo "$sso_body" | python3 -c "import json, sys; print(json.load(sys.stdin).get('data',''))" 2>/dev/null)
    if [[ -z "$sso_url" ]]; then
        log "  ❌ SSO URL parse failed: $sso_body"
        return 2
    fi
    log "  ✅ SSO URL acquired (${#sso_url} chars)"
    
    # Navigate TC tab to SSO URL
    if ! python3 -c "
import sys, json
sys.path.insert(0, '$SCRIPT_DIR')
from derayah_session_manager import SessionManager
sm = SessionManager()
sm._navigate_tc_to_sso('$sso_url')
print('navigated')
" 2>>"$LOG_FILE"; then
        log "  ❌ TC tab navigation failed"
        return 2
    fi
    log "  ✅ TC tab navigated to SSO URL"
    
    # Wait for the navigation to complete + new token to land in localStorage
    log "  Polling for TC_DERAYAH update (up to 30s)..."
    if ! python3 -c "
import sys, json, time
sys.path.insert(0, '$SCRIPT_DIR')
from derayah_session_manager import SessionManager
sm = SessionManager()

# Read current TC token exp for comparison
import base64
def jwt_exp(tok):
    if not tok: return 0
    try:
        parts = tok.split('.')
        if len(parts) == 3:
            pad = parts[1] + '=' * (-len(parts[1]) % 4)
            return int(json.loads(base64.urlsafe_b64decode(pad.encode())).get('exp', 0))
    except: pass
    return 0

# Get baseline exp from file
old_tc = ''
try:
    old_tc = json.load(open('$TOKEN_FILE')).get('TC_DERAYAH', '')
except: pass
old_exp = jwt_exp(old_tc)
print(f'  Old TC exp: {old_exp}')

deadline = time.time() + 30
found = False
while time.time() < deadline:
    tabs = sm._cdp_list_tabs()
    tc = sm._find_tc_tab(tabs)
    if tc:
        new_ls = sm._cdp_eval(tc.get('webSocketDebuggerUrl'), \"localStorage.getItem('TC_DERAYAH')\")
        if new_ls:
            try:
                d = json.loads(new_ls)
                new_tc = d.get('token', '')
                new_exp = jwt_exp(new_tc)
                if new_exp > old_exp:
                    print(f'  ✅ New TC exp: {new_exp} (+{(new_exp-old_exp)//60}m)')
                    # Save
                    t = json.load(open('$TOKEN_FILE'))
                    t['TC_DERAYAH'] = new_tc
                    with open('$TOKEN_FILE', 'w') as f:
                        json.dump(t, f, indent=2)
                    print(f'  ✅ Saved fresh TC token')
                    found = True
                    break
            except: pass
    time.sleep(1)
if not found:
    print(f'  ❌ No new TC token after 30s')
    sys.exit(2)
" 2>>"$LOG_FILE"; then
        log "  ❌ Token re-capture failed"
        return 2
    fi
    
    # Verify the new tokens work
    local verify
    verify=$(python3 -c "
import json, requests
t = json.load(open('$TOKEN_FILE'))
tc = t.get('TC_DERAYAH','')
if not tc:
    print('NO_TC_TOKEN')
else:
    r = requests.get('https://api.derayah.com/trading/Portfolio/List',
                     headers={'Authorization': f'Bearer {tc}', 'Origin':'https://derayah.tickerchart.net'}, timeout=10)
    print(f'{r.status_code}|{r.text[:80]}')
" 2>>"$LOG_FILE")
    
    local v_code="${verify%%|*}"
    log "  Verify with TC token: HTTP $v_code"
    if [[ "$v_code" == "200" ]]; then
        log "✅ SSO refresh successful — new TC token valid"
        # Sync ALL tokens from browser (TC trading + dashboard access) to JSON file.
        # The SSO flow only updates TC_DERAYAH in the TC tab; Derayah_accesstoken
        # may have also been refreshed in the dashboard tab. Reading from the
        # browser (source of truth) keeps the JSON file current.
        python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from derayah_session_manager import SessionManager
sm = SessionManager()
sync = sm.sync_tokens_from_browser()
print(f'  Sync: {len(sync[\"updated\"])} updated, {len(sync[\"kept\"])} kept')
if sync.get('errors'):
    for e in sync['errors']:
        print(f'  Sync err: {e}')
" 2>>"$LOG_FILE"
        # Clean up any tracker tabs that opened during SSO navigation
        python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from derayah_session_manager import SessionManager
sm = SessionManager()
closed = sm._close_extra_tabs()
if closed > 0:
    print(f'  Closed {closed} tracker tab(s)')
" 2>>"$LOG_FILE"
        return 0
    else
        log "  ❌ SSO refresh didn't yield a valid TC token"
        return 2
    fi
}



# ─── Phase 3: Auto-Recovery via Email OTP ────────────────────────────────
# Triggered when SSO refresh fails and ~/.derayah-creds is available.
# Flow:
#   1. Call SessionManager.auto_login_with_email_otp() in Python
#   2. That method fills the signin form, selects Email radio, submits
#   3. Polls Mino's IMAP for the OTP from ccr@derayah.com
#   4. Fills the OTP, submits, waits for dashboard redirect
#   5. Captures tokens via localStorage
#   6. Bails to manual login if reCAPTCHA is shown at any step
auto_recover() {
    local reason="$1"
    local severity="$2"

    log "=== Phase 3: Auto-Recovery via Email OTP ==="
    log "  Reason: $reason"
    log "  Calling derayah_session_manager.auto_login_with_email_otp()..."

    local recovery_result
    recovery_result=$(python3 -c "
import sys, json
sys.path.insert(0, '$SCRIPT_DIR')
from derayah_session_manager import SessionManager
sm = SessionManager()
result = sm.auto_login_with_email_otp(otp_timeout=90)
print(json.dumps(result))
" 2>>"$LOG_FILE")

    local rc=$?
    if [[ $rc -ne 0 ]]; then
        log "  ❌ Auto-recovery Python script crashed (exit $rc)"
        notify_user "$severity" "Auto-recovery crashed. Reason: $reason. Manual Derayah login needed."
        return 1
    fi

    local success
    success=$(echo "$recovery_result" | python3 -c "import sys, json; print(json.load(sys.stdin).get('success', False))" 2>/dev/null)
    local error
    error=$(echo "$recovery_result" | python3 -c "import sys, json; print(json.load(sys.stdin).get('error', ''))" 2>/dev/null)

    if [[ "$success" == "True" ]]; then
        log "  ✅ Auto-recovery succeeded!"
        notify_user "🟢" "Auto-recovery succeeded! Session restored automatically. No action needed."
        return 0
    else
        log "  ❌ Auto-recovery failed: $error"
        # If reCAPTCHA was detected, that's expected — manual login is the fallback
        if echo "$error" | grep -qi "recaptcha\|challenge"; then
            log "  ℹ️  reCAPTCHA detected — falling back to manual login as designed"
            notify_user "$severity" "Auto-recovery hit reCAPTCHA. Manual Derayah login needed. Reason: $reason"
        else
            notify_user "$severity" "Auto-recovery failed: $error. Manual Derayah login needed."
        fi
        return 1
    fi
}
# ─── Main ────────────────────────────────────────────────────────────────────
main() {
    log "=== Derayah Refresh Cron (SSO Navigation v2) ==="
    
    # Make sure CDP is up
    if ! curl -s --max-time 5 --connect-timeout 2 "http://127.0.0.1:18801/json" >/dev/null 2>&1; then
        log "❌ CDP not accessible — attempting Chrome auto-restart"
        # System display is :0 — CRD uses existing lightdm display
        export DISPLAY=:0
        PROFILE_DIR="/home/mino/.config/google-chrome/derayah-live"
        rm -f "$PROFILE_DIR/SingletonLock" "$PROFILE_DIR/DevToolsActivePort" 2>/dev/null || true
        if bash /home/mino/tasi-exec/start-chrome.sh >>"$LOG_FILE" 2>&1; then
            log "  ✅ Chrome restarted"
            sleep 3
        else
            log "  ❌ Chrome restart failed — aborting"
            notify_user "🔴" "Chrome auto-restart failed. Trading system offline."
            exit 1
        fi
    fi
    
    # ─── v4.3.6 Fix: Verify required tabs exist ──────────────────────────────
    # CDP may be up but tabs could be missing (e.g., user closed tab).
    # Opening missing tabs via CDP prevents false 401s in sso_refresh().
    local tabs_json
    tabs_json=$(curl -s --max-time 5 --connect-timeout 2 "http://127.0.0.1:18801/json" 2>/dev/null)
    
    if [[ -n "$tabs_json" ]]; then
        # Check for TC tab
        if ! echo "$tabs_json" | grep -q "tickerchart"; then
            log "⚠️ TC tab missing — opening via CDP"
            curl -s --max-time 5 -X PUT "http://127.0.0.1:18801/json/new?https://derayah.tickerchart.net/app/en" >/dev/null 2>&1 || true
            sleep 1
        fi
        
        # Check for dashboard tab
        if ! echo "$tabs_json" | grep -q "newonline.derayah"; then
            log "⚠️ Dashboard tab missing — opening via CDP"
            curl -s --max-time 5 -X PUT "http://127.0.0.1:18801/json/new?https://newonline.derayah.com/" >/dev/null 2>&1 || true
            sleep 1
        fi
    fi
    
    # Try SSO refresh
    local rc
    sso_refresh
    rc=$?
    
    case $rc in
        0)
            log "✅ SSO refresh returned success — verifying dashboard tab is actually logged in..."
            # ─── v4.3.7 Fix: Verify dashboard tab is not stuck on signin page ───
            # The SSO endpoint returns 200 even when the user is logged out server-side,
            # because the session cookie is still valid. The dashboard tab may show the
            # onboarding signin page (onboarding.derayah.com/#/signin) even though TC
            # token got refreshed. We must detect this and trigger auto-recovery.
            local dashboard_url
            dashboard_url=$(python3 -c "
import sys, json
sys.path.insert(0, '$SCRIPT_DIR')
from derayah_session_manager import SessionManager
sm = SessionManager()
tabs = sm._cdp_list_tabs()
db = sm._find_dashboard_tab(tabs)
if db:
    print(db.get('url', ''))
else:
    print('NOT_FOUND')
" 2>>"$LOG_FILE")
            log "  Dashboard tab URL: $dashboard_url"

            if echo "$dashboard_url" | grep -qiE "signin|onboarding"; then
                log "  ❌ Dashboard tab shows signin/onboarding page — session is NOT actually logged in"
                if [[ -f "$HOME/.derayah-creds" || -f "/home/mino/.derayah-creds" ]]; then
                    [[ -f "/home/mino/.derayah-creds" ]] && CREDS_FILE="/home/mino/.derayah-creds" || CREDS_FILE="$HOME/.derayah-creds"
                    log "  Using creds file: $CREDS_FILE"
                    auto_recover "Dashboard shows signin after SSO refresh" "🔴"
                    exit $?
                else
                    log "  No ~/.derayah-creds — cannot auto-recover"
                    notify_user "🔴" "SSO refresh succeeded but dashboard is on signin page. No auto-recovery creds. Manual Derayah login needed ASAP."
                    exit 1
                fi
            else
                log "✅ Session refresh completed — dashboard is logged in"
                exit 0
            fi
            ;;
        1)
            # No tokens at all — try auto-recovery via email OTP, then manual
            log "❌ No tokens — attempting auto-recovery via email OTP"
            if [[ -f "$HOME/.derayah-creds" || -f "/home/mino/.derayah-creds" ]]; then
            [[ -f "/home/mino/.derayah-creds" ]] && CREDS_FILE="/home/mino/.derayah-creds" || CREDS_FILE="$HOME/.derayah-creds"
            log "  Using creds file: $CREDS_FILE"
                auto_recover "No tokens found" "🟡"
                exit $?
            else
                log "  No ~/.derayah-creds — cannot auto-recover"
                notify_user "🟡" "No valid tokens found AND no auto-recovery creds. Manual Derayah login needed before 09:50 TASI open."
                exit 1
            fi
            ;;
        2)
            # Tokens exist but SSO refresh failed — try auto-recovery
            log "❌ SSO refresh failed — attempting auto-recovery via email OTP"
            if [[ -f "$HOME/.derayah-creds" || -f "/home/mino/.derayah-creds" ]]; then
            [[ -f "/home/mino/.derayah-creds" ]] && CREDS_FILE="/home/mino/.derayah-creds" || CREDS_FILE="$HOME/.derayah-creds"
            log "  Using creds file: $CREDS_FILE"
                auto_recover "SSO refresh failed" "🔴"
                exit $?
            else
                log "  No ~/.derayah-creds — cannot auto-recover"
                notify_user "🔴" "Auto-refresh failed AND no auto-recovery creds. Manual Derayah login needed ASAP."
                exit 1
            fi
            ;;
        *)
            log "❌ Unknown refresh outcome: $rc"
            notify_user "🔴" "Refresh cron returned unexpected code $rc. Please check $LOG_FILE."
            exit 1
            ;;
    esac
}

main "$@"

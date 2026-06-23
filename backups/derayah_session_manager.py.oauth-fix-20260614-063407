#!/usr/bin/env python3
"""
Derayah Session Manager
=======================

Manages the 3-phase session lifecycle:
1. LOGIN     - Capture tokens from browser
2. MAINTAIN  - Refresh tokens every ~50 min
3. RECOVERY  - Handle failures, notify user

Usage:
    from derayah_session_manager import SessionManager
    sm = SessionManager()
    
    # Phase 1: Capture after manual login
    sm.capture_tokens()  # Reads from browser, saves to file
    
    # Phase 2: Refresh (called by cron or /refresh command)
    sm.refresh_session()  # Full refresh cycle
    
    # Check health
    health = sm.check_health()
    print(health)

File: /home/mino/tasi-exec/derayah_session_manager.py
# Migrated 2026-06-08 from workspace/ per TASI_SESSION_MIGRATION_PLAN_v4.2
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import requests
import websocket

# ─── Config ──────────────────────────────────────────────────────────────────

TOKEN_FILE      = Path("/home/mino/tasi-exec/derayah_tokens.json")
CDP_URL         = "http://127.0.0.1:18801"
TOKEN_ENDPOINT  = "https://api.derayah.com/idspark/connect/token"
SSO_ENDPOINT    = "https://api.derayah.com/apispark/trade/TickerChartUrl"
CLIENT_ID       = "NewWebClient"

# Load client_secret from creds file (preferred) or env var (fallback)
_CLIENT_SECRET = None
try:
    _creds_path = os.path.expanduser("~/.derayah-creds")
    if not os.path.exists(_creds_path):
        _creds_path = "/home/mino/.derayah-creds"
    if os.path.exists(_creds_path):
        with open(_creds_path) as f:
            _creds = json.load(f)
            _CLIENT_SECRET = _creds.get("client_secret")
except Exception:
    pass
if not _CLIENT_SECRET:
    _CLIENT_SECRET = os.environ.get("DERAYAH_CLIENT_SECRET", "")
CLIENT_SECRET = _CLIENT_SECRET

# Token lifetimes
ACCESS_TTL      = 3600      # 60 minutes
REFRESH_MARGIN  = 600       # Refresh 10 min before expiry

log = logging.getLogger(__name__)


# ─── Session Manager ─────────────────────────────────────────────────────────

class SessionManager:
    """Manages Derayah dashboard + TC tab session lifecycle."""

    def __init__(self):
        self.tokens = self._load_tokens()
        self._last_refresh = 0

    # ─── Token I/O ─────────────────────────────────────────────────────────────

    def _load_tokens(self) -> dict:
        """Load tokens from disk."""
        if TOKEN_FILE.exists():
            with open(TOKEN_FILE) as f:
                return json.load(f)
        return {}

    def _save_tokens(self, data: dict) -> None:
        """Save tokens to disk."""
        with open(TOKEN_FILE, "w") as f:
            json.dump(data, f, indent=2)
        log.info(f"Tokens saved to {TOKEN_FILE}")

    # ─── Phase 1: Capture ──────────────────────────────────────────────────────

    def capture_tokens(self) -> dict:
        """
        Phase 1: Capture tokens from browser after manual login.
        
        Reads Derayah_accesstoken and Derayah_refreshtoken from
        dashboard localStorage via CDP.
        
        Returns:
            dict with captured tokens and metadata
        """
        log.info("Phase 1: Capturing tokens from browser...")
        
        tabs = self._cdp_list_tabs()
        dash = self._find_dashboard_tab(tabs)
        
        if not dash:
            # Try to be helpful: open the signin tab if it doesn't exist
            log.warning("No Derayah tab found — opening onboarding signin tab")
            self._cdp_new_tab("https://onboarding.derayah.com/#/signin")
            import time
            time.sleep(3)
            tabs = self._cdp_list_tabs()
            dash = self._find_dashboard_tab(tabs)
            if not dash:
                raise RuntimeError(
                    "No Derayah tab found even after opening signin. "
                    "Please log in manually, then re-run capture_tokens()."
                )
            log.info("Signin tab opened — waiting for user to complete login")
            raise RuntimeError(
                "Signin tab opened. Please log in to Derayah in Chrome, "
                "then re-run capture_tokens()."
            )
        
        ws_url = dash.get("webSocketDebuggerUrl")
        
        # Read tokens from dashboard localStorage
        access = self._cdp_eval(ws_url, "localStorage.getItem('Derayah_accesstoken') || ''")
        refresh = self._cdp_eval(ws_url, "localStorage.getItem('Derayah_refreshtoken') || ''")
        
        if not access or not refresh:
            raise RuntimeError(
                "Tokens not found in Derayah tab localStorage. "
                "Is the user logged in? Look at the signin tab — it may still "
                "be showing the login form. Please complete the login, then "
                "re-run capture_tokens()."
            )
        
        # Save
        data = {
            "Derayah_accesstoken": access,
            "Derayah_refreshtoken": refresh,
            "captured_at": datetime.now().isoformat(),
            "source": "manual-login-capture"
        }
        self._save_tokens(data)
        self.tokens = data
        
        # Also capture TC token if TC tab exists
        tc = self._find_tc_tab(tabs)
        if tc:
            tc_token = self._cdp_eval(
                tc.get("webSocketDebuggerUrl"),
                'JSON.parse(localStorage.getItem("TC_DERAYAH") || "{}").token || ""'
            )
            if tc_token:
                data["TC_DERAYAH"] = tc_token
                self._save_tokens(data)
                log.info(f"TC token captured ({len(tc_token)} chars)")
        
        log.info(f"✅ Tokens captured! Access token: {len(access)} chars")
        return data

    def sync_tokens_from_browser(self) -> dict:
        """
        Reconcile JSON token file with browser localStorage (the source of truth).
        
        Reads:
        - Derayah_accesstoken from dashboard tab (newonline.derayah.com)
        - Derayah_refreshtoken from dashboard tab
        - TC_DERAYAH from TC tab (derayah.tickerchart.net)
        
        Writes the freshest version of each to derayah_tokens.json.
        Called by:
        - refresh_cron.sh after successful SSO refresh (so next run uses fresh access)
        - auto-recovery success (so JSON file stays in sync)
        - Bot startup (so poller/bot read the latest from the file)
        
        Returns: dict with what was found, written keys, and skip reasons.
        """
        result = {"updated": [], "kept": [], "errors": []}
        try:
            tabs = self._cdp_list_tabs()
            
            # Load current file state
            current = {}
            if TOKEN_FILE.exists():
                with open(TOKEN_FILE) as f:
                    current = json.load(f)
            
            updates = {}
            
            # ─── 1. Dashboard tab: Derayah_accesstoken + refresh_token ─────────
            dash = self._find_dashboard_tab(tabs)
            if dash:
                ws = dash.get("webSocketDebuggerUrl")
                access = self._cdp_eval(ws, "localStorage.getItem('Derayah_accesstoken') || ''")
                refresh = self._cdp_eval(ws, "localStorage.getItem('Derayah_refreshtoken') || ''")
                
                if access and len(access) > 100:
                    new_access_exp = self._jwt_exp(access)
                    file_access_exp = self._jwt_exp(current.get("Derayah_accesstoken", ""))
                    if new_access_exp > file_access_exp:
                        updates["Derayah_accesstoken"] = access
                        result["updated"].append(f"Derayah_accesstoken (exp {new_access_exp} > {file_access_exp})")
                    else:
                        result["kept"].append(f"Derayah_accesstoken (file {file_access_exp} >= browser {new_access_exp})")
                
                if refresh and len(refresh) > 10:
                    updates["Derayah_refreshtoken"] = refresh
                    result["updated"].append("Derayah_refreshtoken")
            else:
                result["errors"].append("No dashboard tab found")
            
            # ─── 2. TC tab: TC_DERAYAH ──────────────────────────────────────────
            tc = self._find_tc_tab(tabs)
            if tc:
                ws = tc.get("webSocketDebuggerUrl")
                # TC stores it as JSON: {"token": "...", "expiresAt": ...}
                tc_raw = self._cdp_eval(ws, 'localStorage.getItem("TC_DERAYAH") || ""')
                if tc_raw:
                    try:
                        tc_data = json.loads(tc_raw)
                        tc_token = tc_data.get("token", "")
                        if tc_token and len(tc_token) > 100:
                            new_tc_exp = self._jwt_exp(tc_token)
                            file_tc_exp = self._jwt_exp(current.get("TC_DERAYAH", ""))
                            if new_tc_exp > file_tc_exp:
                                updates["TC_DERAYAH"] = tc_token
                                result["updated"].append(f"TC_DERAYAH (exp {new_tc_exp} > {file_tc_exp})")
                            else:
                                result["kept"].append(f"TC_DERAYAH (file {file_tc_exp} >= browser {new_tc_exp})")
                    except json.JSONDecodeError:
                        result["errors"].append("TC_DERAYAH localStorage not valid JSON")
            else:
                result["errors"].append("No TC tab found")
            
            # ─── 3. Write back if any updates ──────────────────────────────────
            if updates:
                current.update(updates)
                current["last_synced_at"] = datetime.now().isoformat()
                current["sync_source"] = "browser-localStorage"
                self._save_tokens(current)
                self.tokens = current
                log.info(f"✅ Synced {len(updates)} token(s) from browser: {list(updates.keys())}")
            else:
                log.info("No new tokens to sync from browser")
            
            return result
        except Exception as e:
            log.error(f"sync_tokens_from_browser failed: {e}")
            result["errors"].append(str(e))
            return result

    def _jwt_exp(self, token: str) -> int:
        """Decode JWT exp claim. Returns 0 on failure."""
        if not token:
            return 0
        try:
            import base64
            parts = token.split(".")
            if len(parts) != 3:
                return 0
            pad = parts[1] + "=" * (-len(parts[1]) % 4)
            return int(json.loads(base64.urlsafe_b64decode(pad.encode())).get("exp", 0))
        except Exception:
            return 0

    # ─── Phase 2: Refresh ──────────────────────────────────────────────────────

    def refresh_session(self) -> dict:
        """
        Phase 2: Full token refresh cycle.
        
        1. Refresh Derayah tokens via OAuth API
        2. Get fresh SSO URL
        3. Navigate TC tab to SSO URL
        4. Verify freshness
        
        Returns:
            dict with new tokens and expiry info
        """
        log.info("Phase 2: Starting session refresh...")
        
        # Step 1: Refresh Derayah tokens
        new_access, new_refresh, expires_in = self._refresh_derayah_tokens()
        log.info(f"Step 1: New access token ({len(new_access)} chars, expires in {expires_in}s)")
        
        # Step 2: Get SSO URL
        sso_url = self._get_sso_url(new_access)
        log.info(f"Step 2: SSO URL acquired ({len(sso_url)} chars)")
        
        # Step 3: Navigate TC tab to SSO URL
        self._navigate_tc_to_sso(sso_url)
        log.info("Step 3: TC tab navigated to SSO URL")
        
        # Step 4: Inject into dashboard
        self._inject_dashboard_tokens(new_access, new_refresh)
        log.info("Step 4: Dashboard tokens injected")
        
        # Step 5: Verify
        time.sleep(12)  # Wait for SSO redirect
        tc_token = self._get_tc_token()
        
        if not tc_token:
            raise RuntimeError("TC token not found after SSO navigation")
        
        # Check expiry
        exp = self._decode_token_expiry(tc_token)
        remaining = exp - time.time() if exp else 0
        
        # Save everything
        data = {
            "Derayah_accesstoken": new_access,
            "Derayah_refreshtoken": new_refresh,
            "sso_url": sso_url,
            "TC_DERAYAH": tc_token,
            "last_refreshed": datetime.now().isoformat(),
            "expires_in": expires_in,
            "tc_expiry": datetime.fromtimestamp(exp).isoformat() if exp else None,
            "tc_remaining_min": round(remaining / 60, 1) if remaining else None
        }
        self._save_tokens(data)
        self.tokens = data
        self._last_refresh = time.time()
        
        log.info(f"✅ Session refreshed! TC token valid for {remaining/60:.1f} min")
        return data

    def _refresh_derayah_tokens(self) -> tuple:
        """Call OAuth refresh endpoint. Returns (access, refresh, expires_in)."""
        refresh_token = self.tokens.get("Derayah_refreshtoken", "")
        
        if not refresh_token:
            raise RuntimeError("No refresh token available - need manual login")
        
        resp = requests.post(
            TOKEN_ENDPOINT,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://newonline.derayah.com",
                "Referer": "https://newonline.derayah.com/",
            },
            timeout=15
        )
        
        if resp.status_code != 200:
            raise RuntimeError(f"Token refresh failed: {resp.status_code} - {resp.text[:200]}")
        
        result = resp.json()
        return (
            result.get("access_token", ""),
            result.get("refresh_token", "") or refresh_token,  # May return same
            result.get("expires_in", 0)
        )

    def _get_sso_url(self, access_token: str) -> str:
        """Get fresh SSO URL using access token."""
        resp = requests.get(
            SSO_ENDPOINT,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Origin": "https://newonline.derayah.com",
                "Referer": "https://newonline.derayah.com/",
            },
            timeout=10
        )
        
        if resp.status_code != 200:
            raise RuntimeError(f"SSO URL fetch failed: {resp.status_code}")
        
        result = resp.json()
        url = result.get("data", "")
        if not url:
            raise RuntimeError("No SSO URL in response")
        
        return url

    def _navigate_tc_to_sso(self, sso_url: str) -> None:
        """Navigate TC tab to SSO URL via CDP, then activate it.
        
        Activating the TC tab ensures ws_probe.py's WebSocket isn't disrupted
        by the user clicking on a different tab mid-navigation. The Vue app
        on the dashboard tab may pause its WebSocket if it loses focus.
        """
        tabs = self._cdp_list_tabs()
        tc = self._find_tc_tab(tabs)
        
        if tc:
            # Navigate existing TC tab
            ws_url = tc.get("webSocketDebuggerUrl")
            self._cdp_navigate(ws_url, sso_url)
        else:
            # Create new tab with SSO URL (already navigates in _cdp_new_tab)
            new_tab = self._cdp_new_tab(sso_url)
            if new_tab:
                tc = new_tab
        
        # Activate the TC tab so it stays focused (keeps ws_probe data flowing)
        if tc:
            tc_id = tc.get("id")
            if tc_id:
                self._activate_tab(tc_id)

    def _inject_dashboard_tokens(self, access: str, refresh: str) -> None:
        """Inject tokens into dashboard localStorage."""
        tabs = self._cdp_list_tabs()
        dash = self._find_dashboard_tab(tabs)
        
        if not dash:
            log.warning("Dashboard tab not found - skipping injection")
            return
        
        ws_url = dash.get("webSocketDebuggerUrl")
        
        # Escape for JavaScript
        access_esc = access.replace("'", "\\'")
        refresh_esc = refresh.replace("'", "\\'")
        
        self._cdp_eval(ws_url, f"localStorage.setItem('Derayah_accesstoken', '{access_esc}')")
        self._cdp_eval(ws_url, f"localStorage.setItem('Derayah_refreshtoken', '{refresh_esc}')")

    # ─── Phase 3: Recovery ─────────────────────────────────────────────────────

    def needs_refresh(self) -> bool:
        """Check if session needs refresh (< 10 min remaining)."""
        tc_token = self.tokens.get("TC_DERAYAH", "")
        if not tc_token:
            return True
        
        exp = self._decode_token_expiry(tc_token)
        if not exp:
            return True
        
        remaining = exp - time.time()
        return remaining < REFRESH_MARGIN

    def check_health(self) -> dict:
        """Quick health check of all components."""
        result = {
            "timestamp": datetime.now().isoformat(),
            "dashboard_tab": False,
            "tc_tab": False,
            "tc_token_valid": False,
            "tc_token_expiry": None,
            "tc_remaining_min": None,
            "api_working": False,
            "needs_refresh": False,
        }
        
        # Check tabs
        tabs = self._cdp_list_tabs()
        result["dashboard_tab"] = bool(self._find_dashboard_tab(tabs))
        tc = self._find_tc_tab(tabs)
        result["tc_tab"] = bool(tc)
        
        # Check TC token
        if tc:
            tc_token = self._get_tc_token()
            if tc_token:
                result["tc_token_valid"] = True
                exp = self._decode_token_expiry(tc_token)
                if exp:
                    result["tc_token_expiry"] = datetime.fromtimestamp(exp).isoformat()
                    remaining = exp - time.time()
                    result["tc_remaining_min"] = round(remaining / 60, 1)
                    result["needs_refresh"] = remaining < REFRESH_MARGIN
                
                # Test API
                result["api_working"] = self._test_api(tc_token)
        
        return result

    # ─── CDP Helpers ───────────────────────────────────────────────────────────

    def _cdp_list_tabs(self) -> list:
        """List all CDP tabs."""
        try:
            resp = requests.get(f"{CDP_URL}/json/list", timeout=5)
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            log.error(f"CDP list failed: {e}")
            return []

    def _cdp_is_healthy(self) -> bool:
        """Quick check: is CDP responding?"""
        try:
            resp = requests.get(f"{CDP_URL}/json/version", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def _ensure_chrome_running(self) -> bool:
        """Ensure Chrome is running and CDP is healthy.
        
        If Chrome is not running, attempts to restart it via start-chrome.sh.
        This is a single point of truth for Chrome availability — call it
        at the start of any method that needs CDP.
        
        Returns:
            True if Chrome is running and healthy, False otherwise.
        """
        if self._cdp_is_healthy():
            return True
        
        log.warning("CDP is down — attempting Chrome restart")
        try:
            import subprocess
            result = subprocess.run(
                ["bash", "/home/mino/tasi-exec/start-chrome.sh"],
                timeout=30, capture_output=True, text=True
            )
            if result.returncode == 0:
                log.info("Chrome restarted, waiting for CDP...")
                time.sleep(5)
                if self._cdp_is_healthy():
                    log.info("CDP is healthy after restart")
                    return True
                else:
                    log.warning("Chrome started but CDP not responding")
                    return False
            else:
                log.error(f"start-chrome.sh failed: {result.returncode}")
                return False
        except Exception as e:
            log.error(f"Chrome restart failed: {e}")
            return False

    def _find_dashboard_tab(self, tabs: list) -> dict:
        """Find Derayah dashboard tab.
        
        Matches either:
        - newonline.derayah.com (the trading dashboard, post-login)
        - onboarding.derayah.com (the signin/onboarding flow, pre-login)
        
        Both contain 'Derayah_accesstoken' in localStorage after a successful
        login. The onboarding page sets it after a successful signin redirect.
        """
        for t in tabs:
            url = t.get("url", "")
            if "newonline.derayah.com" in url or "onboarding.derayah.com" in url:
                return t
        return None

    def _find_tc_tab(self, tabs: list) -> dict:
        """Find TickerChart tab."""
        for t in tabs:
            if "derayah.tickerchart.net" in t.get("url", ""):
                return t
        return None

    def _cdp_eval(self, ws_url: str, expression: str) -> str:
        """Evaluate JavaScript via CDP."""
        try:
            ws = websocket.create_connection(ws_url, timeout=10)
            ws.send(json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "returnByValue": True}
            }))
            resp = json.loads(ws.recv())
            ws.close()
            return resp.get("result", {}).get("result", {}).get("value", "")
        except Exception as e:
            log.error(f"CDP eval failed: {e}")
            return ""

    def _cdp_navigate(self, ws_url: str, url: str) -> None:
        """Navigate tab to URL via CDP."""
        try:
            ws = websocket.create_connection(ws_url, timeout=10)
            ws.send(json.dumps({
                "id": 1,
                "method": "Page.navigate",
                "params": {"url": url}
            }))
            ws.recv()
            ws.close()
        except Exception as e:
            log.error(f"CDP navigate failed: {e}")

    def _activate_tab(self, tab_id: str) -> None:
        """Bring a tab to the foreground via CDP (Page.bringToFront).
        
        Some Vue 3 / SPA apps throttle or pause their WebSocket connections
        when the tab loses focus. Keeping the TC tab active after SSO
        navigation ensures ws_probe.py's price feed stays continuous.
        """
        try:
            # Find the tab's websocket from its id
            tabs = self._cdp_list_tabs()
            target_ws = None
            for t in tabs:
                if t.get("id") == tab_id:
                    target_ws = t.get("webSocketDebuggerUrl")
                    break
            if not target_ws:
                log.warning(f"Could not find websocket for tab {tab_id}")
                return
            
            ws = websocket.create_connection(target_ws, timeout=5)
            ws.send(json.dumps({
                "id": 1,
                "method": "Page.bringToFront"
            }))
            ws.recv()
            ws.close()
            log.info(f"Activated tab {tab_id[:20]}")
        except Exception as e:
            log.warning(f"Failed to activate tab: {e}")

    def _cdp_new_tab(self, url: str) -> dict:
        """Create new tab via CDP AND navigate it to the given URL.
        
        Chrome's /json/new endpoint does NOT honor the ?url= query param —
        the new tab always opens at about:blank. We must explicitly navigate
        via Page.navigate after creation.
        
        Returns: dict with 'webSocketDebuggerUrl' and 'id', or {} on failure.
        """
        try:
            # Step 1: Create empty tab
            resp = requests.put(f"{CDP_URL}/json/new", timeout=10)
            if resp.status_code != 200:
                log.error(f"CDP /json/new returned {resp.status_code}")
                return {}
            new_tab = resp.json()
            ws_url = new_tab.get("webSocketDebuggerUrl")
            if not ws_url:
                log.error("CDP new tab: no webSocketDebuggerUrl in response")
                return {}
            
            # Step 2: Explicitly navigate to the URL (the actual fix)
            self._cdp_navigate(ws_url, url)
            
            # Step 3: Return the tab info so the caller can keep using it
            return new_tab
        except Exception as e:
            log.error(f"CDP new tab failed: {e}")
            return {}

    # ─── Auto-Recovery: Detect CAPTCHA + Email OTP Login ───────────────────────

    def _detect_recaptcha_challenge(self, ws_url: str) -> bool:
        """Detect ACTUAL reCAPTCHA challenge widget (not the script tag).
        
        The reCAPTCHA <script> tag is ALWAYS in the page (loads the API).
        The challenge widget only appears when reCAPTCHA has been triggered:
          - iframe[src*="recaptcha"] with title starting with "reCAPTCHA"
          - window.grecaptcha object with a widget rendered
        
        Returns True if a real challenge is visible to the user.
        """
        try:
            has_iframe = self._cdp_eval(ws_url,
                "!!document.querySelector('iframe[src*=\"recaptcha\"]') || "
                "!!document.querySelector('iframe[title*=\"reCAPTCHA\"]') || "
                "!!document.querySelector('iframe[title*=\"challenge\"]')"
            )
            has_bubble = self._cdp_eval(ws_url,
                "!!document.querySelector('.g-recaptcha-bubble')"
            )
            return (has_iframe or has_bubble) in (True, 'true', 'True', '1')
        except Exception as e:
            log.warning(f"reCAPTCHA detection failed: {e}")
            return False  # Assume no challenge if detection fails

    def _find_signin_tab(self) -> dict:
        """Find the onboarding.derayah.com signin tab."""
        tabs = self._cdp_list_tabs()
        for t in tabs:
            url = t.get("url", "")
            if "onboarding.derayah.com" in url and "/signin" in url:
                return t
        return None

    def _close_extra_tabs(self, keep_patterns: list = None,
                          close_patterns: list = None) -> int:
        """Close tabs matching close_patterns, keeping those matching keep_patterns.
        
        Default behavior: close doubleclick.net, tiktok, snapchat, facebook tracker tabs.
        Keep TC tab and dashboard tab.
        
        Args:
            keep_patterns: list of URL substrings to NEVER close
            close_patterns: list of URL substrings to close (default: tracker domains)
        
        Returns:
            Number of tabs closed
        """
        # ── Load current tabs from CDP ──────────────────────────────────────
        tabs = self._cdp_list_tabs()
        if not tabs:
            log.warning("_close_extra_tabs: no tabs found via CDP")
            return 0
        
        if keep_patterns is None:
            keep_patterns = [
                "derayah.tickerchart.net",   # TC trading tab
                "newonline.derayah.com",     # Dashboard tab (post-login)
                "onboarding.derayah.com",    # Active signin tab (don't close mid-recovery)
            ]
        if close_patterns is None:
            close_patterns = [
                "doubleclick.net",
                "tiktok.com",
                "snapchat.com",
                "facebook.com",
                "fbcdn.net",
                "linkedin.com",
                "licdn.com",
                "twitter.com",
                "ads-twitter.com",
                "google-analytics.com",
                "googletagmanager.com",
                "platformance.io",
                "appdynamics.com",
            ]
        
        # ── Deduplicate keeper tabs: keep only the MOST RECENTLY ACTIVE one ──
        # Group tabs by keeper pattern, then keep only the active/foreground tab.
        from collections import defaultdict
        keeper_groups = defaultdict(list)
        ungrouped = []
        for t in tabs:
            url = t.get("url", "")
            matched = False
            for p in keep_patterns:
                if p in url:
                    keeper_groups[p].append(t)
                    matched = True
                    break
            if not matched:
                ungrouped.append(t)

        tabs_to_keep = set()
        for pattern, group in keeper_groups.items():
            if not group:
                continue
            # Prefer the tab whose page reports visibilityState == 'visible'
            # (the one actually in the foreground).  Fall back to the tab
            # with the highest CDP internal id (newer tabs have higher ids).
            best = None
            for t in group:
                ws_url = t.get("webSocketDebuggerUrl")
                try:
                    visible = self._cdp_eval(ws_url, "document.visibilityState")
                    if visible == "visible":
                        best = t
                        break       # foreground wins immediately
                except Exception:
                    pass
            if best is None:
                # No foreground tab — pick the one with the highest id
                # (Chrome assigns monotonically increasing ids as hex strings)
                best = max(group, key=lambda x: x.get("id", ""))
            tabs_to_keep.add(best.get("id"))
            if len(group) > 1:
                log.info(f"  Keeping active {pattern} tab (id={best.get('id')}), closing {len(group)-1} duplicate(s)")

        # Rebuild the full tab list so ungrouped tabs are still processed
        # Only include KEEPER tabs we're actually keeping (not duplicates)
        kept_keeper_tabs = [t for grp in keeper_groups.values() for t in grp if t.get("id") in tabs_to_keep]
        # Process ALL tabs for closing, but skip the ones we're keeping
        all_tabs = self._cdp_list_tabs()  # Get fresh list including duplicates
        closed = 0
        for t in all_tabs:
            url = t.get("url", "")
            tab_id = t.get("id", "")
            # Skip if this is a keeper we're keeping
            if tab_id in tabs_to_keep:
                continue
            # Also close duplicate keeper tabs that are NOT in tabs_to_keep
            # (these are duplicates of the keeper we decided to keep)
            is_duplicate_keeper = any(p in url for p in keep_patterns)
            # Close if URL matches a close pattern OR it's a duplicate keeper
            if any(p in url for p in close_patterns) or is_duplicate_keeper:
                try:
                    import websocket
                    ws = websocket.create_connection(t.get("webSocketDebuggerUrl"), timeout=5)
                    # Use Page.close (window.close) — Target.closeTarget doesn't actually close
                    ws.send(json.dumps({"id": 1, "method": "Page.close", "params": {}}))
                    ws.close()
                    closed += 1
                    log.info(f"Closed tab: {url[:60]}")
                except Exception as e:
                    log.warning(f"Failed to close tab {url}: {e}")
        return closed

    def _wait_for_signin_ready(self, ws_url: str, timeout: int = 15) -> bool:
        """Wait for the signin form to be ready (inputs present, not loading)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready = self._cdp_eval(ws_url,
                "!!document.getElementById('username') && "
                "!!document.getElementById('password') && "
                "!document.querySelector('.loading, [class*=loading], [class*=spinner]')"
            )
            if ready in (True, 'true', 'True', '1'):
                return True
            time.sleep(0.5)
        return False

    def _fill_signin_form(self, ws_url: str, username: str, password: str,
                          otp_method: str = "email") -> None:
        """Fill the signin form and click submit. Returns after submit fires.
        
        otp_method: 'email' or 'sms' - selects the radio button.
        """
        # Type username (simulate human typing for rate-limit friendliness)
        self._cdp_eval(ws_url, f"""
            (function() {{
                const el = document.getElementById('username');
                el.focus();
                el.value = '{username}';
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                el.blur();
            }})()
        """)
        time.sleep(0.3)
        # Type password
        self._cdp_eval(ws_url, f"""
            (function() {{
                const el = document.getElementById('password');
                el.focus();
                el.value = '{password}';
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                el.blur();
            }})()
        """)
        time.sleep(0.5)
        # Select OTP method radio
        if otp_method == "email":
            # value="1" = Email
            self._cdp_eval(ws_url,
                "(() => { const r = document.querySelector('input[type=radio][value=\"1\"]');"
                "if (r) { r.click(); } })()"
            )
        else:
            # value="2" = SMS (default)
            self._cdp_eval(ws_url,
                "(() => { const r = document.querySelector('input[type=radio][value=\"2\"]');"
                "if (r) { r.click(); } })()"
            )
        time.sleep(0.3)
        # Click submit button
        self._cdp_eval(ws_url, "document.querySelector('button[type=submit]').click()")
        log.info(f"Signin form submitted (otp_method={otp_method})")

    def _wait_for_otp_input(self, ws_url: str, timeout: int = 30) -> bool:
        """Wait for the OTP input to appear (means OTP has been sent)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            # The OTP step typically has an input with name/id like 'otp' or 'code'
            # or 4-6 separate digit inputs
            has_otp_input = self._cdp_eval(ws_url,
                "(() => {"
                "  const inputs = document.querySelectorAll('input');"
                "  for (const i of inputs) {"
                "    const id = (i.id || '').toLowerCase();"
                "    const name = (i.name || '').toLowerCase();"
                "    const ph = (i.placeholder || '').toLowerCase();"
                "    if (id.includes('otp') || id.includes('code') || id.includes('digit') ||"
                "        name.includes('otp') || name.includes('code') ||"
                "        ph.includes('otp') || ph.includes('code') ||"
                "        i.type === 'tel' || i.inputMode === 'numeric' ||"
                "        (i.maxLength >= 4 && i.maxLength <= 6)) {"
                "      return true;"
                "    }"
                "  }"
                "  return false;"
                "})()"
            )
            if has_otp_input in (True, 'true', 'True', '1'):
                return True
            time.sleep(0.5)
        return False

    def _fill_otp(self, ws_url: str, otp_code: str) -> None:
        """Fill the OTP code into the OTP input(s).
        
        Handles two common OTP UI patterns:
        1. Single input with maxLength=4-6: just set value
        2. 4-6 separate digit inputs (maxLength=1 each): distribute digits
        """
        digits_js = ','.join(f'"{d}"' for d in otp_code)
        
        # Single JS function that handles both patterns
        result = self._cdp_eval(ws_url, f"""
            (function() {{
                const code = '{otp_code}';
                const digits = [{digits_js}];
                const inputs = Array.from(document.querySelectorAll('input'));
                
                // Pattern 1: single input with maxLength >= code length
                for (const i of inputs) {{
                    if (i.maxLength >= code.length && code.length >= 4) {{
                        i.focus();
                        i.value = code;
                        i.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        i.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        i.blur();
                        return 'single';
                    }}
                }}
                
                // Pattern 2: 4-6 separate digit inputs (maxLength=1 each)
                let di = 0;
                for (const i of inputs) {{
                    if (di >= digits.length) break;
                    if (i.maxLength === 1) {{
                        i.focus();
                        i.value = digits[di];
                        i.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        i.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        i.dispatchEvent(new Event('keyup', {{ bubbles: true }}));
                        i.blur();
                        di++;
                    }}
                }}
                if (di > 0) return 'multi:' + di;
                
                // Pattern 3: fallback - find first non-empty input and put full code
                for (const i of inputs) {{
                    if (i.type !== 'hidden' && !i.disabled) {{
                        i.focus();
                        i.value = code;
                        i.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        i.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        i.blur();
                        return 'fallback';
                    }}
                }}
                return 'none';
            }})()
        """)
        log.info(f"OTP fill result: {result} (code={otp_code})")
        
        time.sleep(0.3)
        # Click submit button
        clicked = self._cdp_eval(ws_url,
            "(() => {"
            "  const btns = document.querySelectorAll('button[type=submit], button.btn-primary');"
            "  for (const b of btns) { if (!b.disabled && b.offsetParent !== null) { b.click(); return true; } }"
            "  return false;"
            "})()"
        )
        log.info(f"Submit button clicked: {clicked}")

    def _fetch_otp_from_email(self, sender: str = "derayah.com",
                              subject_keyword: str = "تفعيل",  # 'activation' in Arabic
                              timeout: int = 90,
                              since_minutes: int = 10) -> str:
        """Poll Mino's IMAP inbox for the latest OTP email and extract the code.
        
        The Derayah OTP email pattern (verified Jun 10):
          From: Derayah Service <ccr@derayah.com>
          Subject (Arabic): 'رمز التفعيل' = 'Activation Code'
          Body pattern: 'استخدم رمز التحقق لمرة واحدة: NNNN' or 'رمز التحقق: NNNN'
        
        Args:
            sender: search emails from this domain
            subject_keyword: keyword in subject (Arabic 'تفعيل' = activation)
            timeout: max seconds to wait
            since_minutes: only consider emails newer than this many minutes
        
        Returns:
            The 4-6 digit OTP code, or empty string if not found.
        """
        import imaplib
        import email as email_module
        from email.header import decode_header
        import re as re_module
        
        Mino_IMAP = "minothejellyfish@gmail.com"
        Mino_PASS = "hvlp isup xiro whbv"
        IMAP_HOST = "imap.gmail.com"
        
        deadline = time.time() + timeout
        last_uid = None
        search_count = 0
        
        while time.time() < deadline:
            search_count += 1
            try:
                M = imaplib.IMAP4_SSL(IMAP_HOST, 993)
                M.login(Mino_IMAP, Mino_PASS)
                M.select('INBOX')
                
                # Search for emails from derayah.com in the last N minutes
                # Use date-based search for "since" filter
                # IMPORTANT: Gmail IMAP SINCE uses UTC, not local time
                from datetime import datetime, timedelta, timezone
                since_date = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).strftime("%d-%b-%Y")
                search_crit = f'(FROM "{sender}" SINCE {since_date})'
                typ, data = M.search(None, search_crit)
                ids = data[0].split() if data and data[0] else []
                
                log.info(f"IMAP search #{search_count}: found {len(ids)} email(s) from {sender} since {since_date}")
                
                if ids:
                    log.info(f"Checking {len(ids)} email(s), most recent first")
                    # Check the most recent first
                    for num in reversed(ids):
                        log.info(f"Checking email UID {num}")
                        typ, msg_data = M.fetch(num, '(RFC822)')
                        if not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple):
                            log.warning(f"Email UID {num}: no data or wrong format")
                            continue
                        msg = email_module.message_from_bytes(msg_data[0][1])
                        
                        # Get subject
                        subj = msg.get('Subject', '')
                        try:
                            # decode_header returns list of (bytes/str, encoding) tuples
                            # Properly decode and concatenate them
                            subj_decoded = ''
                            for s, enc in decode_header(subj):
                                if isinstance(s, bytes):
                                    subj_decoded += s.decode(enc or 'utf-8', errors='ignore')
                                else:
                                    subj_decoded += str(s)
                        except Exception as e:
                            log.warning(f"Subject decode failed: {e}")
                            subj_decoded = str(subj)
                        
                        log.info(f"Email UID {num}: subject='{subj_decoded[:60]}'")
                        
                        # Check subject contains keyword
                        if subject_keyword and subject_keyword not in subj_decoded:
                            log.info(f"Email UID {num}: subject missing keyword '{subject_keyword}' - skipping")
                            continue
                        
                        log.info(f"Email UID {num}: subject matches keyword")
                        
                        # Get body
                        body = ""
                        for part in msg.walk():
                            if part.get_content_type() == 'text/plain':
                                try:
                                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                except:
                                    body = str(part.get_payload())
                                break
                        if not body:
                            for part in msg.walk():
                                if part.get_content_type() == 'text/html':
                                    try:
                                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                        body = re_module.sub(r'<[^>]+>', ' ', body)
                                    except:
                                        body = str(part.get_payload())
                                    break
                        
                        log.info(f"Email UID {num}: body length={len(body)}")
                        
                        # Check the email is recent (within since_minutes)
                        date_hdr = msg.get('Date', '')
                        try:
                            from email.utils import parsedate_to_datetime
                            email_time = parsedate_to_datetime(date_hdr)
                            age = (datetime.now(email_time.tzinfo) - email_time).total_seconds()
                            log.info(f"Email UID {num}: age={age/60:.1f}min, since_limit={since_minutes}min")
                            if age > since_minutes * 60:
                                log.info(f"Email UID {num}: too old ({age/60:.1f}min > {since_minutes}min) - skipping")
                                continue  # Too old
                        except Exception as e:
                            log.warning(f"Email UID {num}: date parse failed: {e}")
                            pass
                        
                        log.info(f"Email UID {num}: age OK, searching for OTP in body")
                        
                        # Extract OTP code
                        # Pattern 1: 'رمز التحقق لمرة واحدة: NNNN' (one-time verification code)
                        # Pattern 2: 'رمز التحقق: NNNN' (verification code)
                        # Pattern 3: 'رمز التفعيل: NNNN' (activation code)
                        # Pattern 4: Generic 4-6 digit code in OTP-like context
                        otp = ""
                        patterns = [
                            r'رمز التحقق لمرة واحدة[:\s]*(\d{4,6})',
                            r'رمز التحقق[:\s]*(\d{4,6})',
                            r'رمز التفعيل[:\s]*(\d{4,6})',
                            r'verification code[:\s]*(\d{4,6})',
                            r'activation code[:\s]*(\d{4,6})',
                            r'code[:\s]+(\d{4,6})\b',
                        ]
                        for i, pat in enumerate(patterns):
                            m = re_module.search(pat, body, re_module.IGNORECASE)
                            if m:
                                otp = m.group(1)
                                log.info(f"Email UID {num}: OTP found with pattern #{i}: {otp}")
                                break
                        if not otp:
                            log.info(f"Email UID {num}: no OTP found with standard patterns")
                            # Last resort: find 4-6 digit number near 'code' or 'OTP'
                            ctx = re_module.search(r'.{0,30}(?:code|OTP|otp|رمز).{0,30}', body)
                            if ctx:
                                digits = re_module.findall(r'\d{4,6}', ctx.group(0))
                                if digits:
                                    otp = digits[0]
                                    log.info(f"Email UID {num}: OTP found with last resort: {otp}")
                                else:
                                    log.info(f"Email UID {num}: last resort found context but no digits")
                            else:
                                log.info(f"Email UID {num}: no OTP context found in body")
                        
                        if otp:
                            M.logout()
                            log.info(f"OTP fetched from email: {otp} (subj: {subj_decoded[:40]})")
                            return otp
                else:
                    log.info(f"IMAP search: no emails found from {sender}")
                
                M.logout()
            except Exception as e:
                log.warning(f"IMAP poll error: {e}")
            
            time.sleep(3)  # Poll every 3s
        
        log.error(f"OTP fetch timed out after {timeout}s (searched {search_count} times)")
        return ""

    def _wait_for_login_complete(self, ws_url: str, timeout: int = 30) -> bool:
        """Wait for login to complete - either success (redirect to dashboard) or error."""
        deadline = time.time() + timeout
        success_url_patterns = ["newonline.derayah.com", "/dashboard", "/portfolio", "/home"]
        error_indicators = ["invalid", "خطأ", "error", "failed", "expired", "منتهي"]
        
        while time.time() < deadline:
            url = self._cdp_eval(ws_url, "location.href") or ""
            body = (self._cdp_eval(ws_url, "document.body.innerText") or "").lower()
            
            # Check for success - redirected to dashboard
            for pat in success_url_patterns:
                if pat in url:
                    log.info(f"Login complete: redirected to {url}")
                    return True
            
            # Check for error message
            for err in error_indicators:
                if err in body:
                    log.warning(f"Login error detected: '{err}' in body")
                    return False
            
            time.sleep(1)
        
        log.warning(f"Login did not complete in {timeout}s; current URL: {url}")
        return False

    def auto_login_with_email_otp(self, otp_timeout: int = 90) -> dict:
        """Full auto-recovery: open signin, fill creds, select email OTP, fetch OTP from IMAP, submit.
        
        Requires:
        - ~/.derayah-creds with username + password
        - Gmail forwarding: Derayah OTPs auto-forwarded to minothejellyfish@gmail.com
        - Chrome with derayah-live profile running, CDP accessible
        
        Returns:
            dict with success=True and the captured tokens, or success=False with error.
        """
        result = {"success": False, "error": None, "tokens": None}
        
        # Phase 0: Ensure Chrome is running
        if not self._ensure_chrome_running():
            result["error"] = "Chrome is not running and could not be restarted. Health monitor will handle it."
            return result
        
        # Load creds (try multiple locations for robustness)
        # The file might be at /home/mino/.derayah-creds (user's HOME)
        # or at /home/mino/.openclaw-mino/.derayah-creds (agent's HOME)
        creds_candidates = [
            os.path.expanduser("~/.derayah-creds"),  # Respects $HOME (agent or user)
            "/home/mino/.derayah-creds",              # Absolute real user HOME
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".derayah-creds"),
        ]
        creds_path = None
        for candidate in creds_candidates:
            if os.path.exists(candidate):
                creds_path = candidate
                break
        if not creds_path:
            result["error"] = (
                f"Credentials file not found in any of: {', '.join(creds_candidates)}. "
                f"Run setup-derayah-creds.sh first."
            )
            return result
        log.info(f"Using creds file: {creds_path}")
        
        try:
            with open(creds_path) as f:
                creds = json.load(f)
        except Exception as e:
            result["error"] = f"Failed to read creds file {creds_path}: {e}"
            return result
        
        username = creds.get("username")
        password = creds.get("password")
        otp_method = creds.get("otp_method", "email")
        
        if not username or not password:
            result["error"] = "Credentials file missing username or password"
            return result
        
        # Force email OTP regardless of saved preference (we can only auto-recover via email)
        otp_method = "email"
        log.info(f"=== Auto-Recovery: email OTP login (user={username}) ===")
        
        # Phase 0.5: Close ALL existing Derayah tabs before opening new signin tab
        # This prevents signin tab accumulation when auto-recovery runs multiple times
        tabs = self._cdp_list_tabs()
        for tab in tabs:
            url = tab.get("url", "")
            if "derayah.com" in url or "onboarding.derayah.com" in url:
                try:
                    import websocket
                    ws = websocket.create_connection(tab.get("webSocketDebuggerUrl"), timeout=5)
                    ws.send(json.dumps({"id": 1, "method": "Page.close", "params": {}}))
                    ws.close()
                    log.info(f"Closed existing derayah tab: {url[:60]}")
                except Exception as e:
                    log.warning(f"Failed to close tab {tab.get('id')}: {e}")
        
        # Open fresh signin tab
        log.info("Opening fresh signin tab")
        new_tab = self._cdp_new_tab("https://onboarding.derayah.com/#/signin")
        if not new_tab:
            result["error"] = "Failed to open signin tab"
            return result
        time.sleep(3)
        signin = new_tab
        
        ws_url = signin.get("webSocketDebuggerUrl")
        if not ws_url:
            result["error"] = "Signin tab has no CDP websocket"
            return result
        
        # Navigate to signin (in case it's on another route)
        self._cdp_eval(ws_url, "location.hash = '#/signin'")
        time.sleep(2)
        
        # Wait for form
        if not self._wait_for_signin_ready(ws_url, timeout=15):
            result["error"] = "Signin form never became ready"
            return result
        log.info("Signin form is ready")
        
        # Check for reCAPTCHA before submitting
        if self._detect_recaptcha_challenge(ws_url):
            result["error"] = "reCAPTCHA challenge detected - manual login required"
            log.error("⛔ reCAPTCHA challenge visible - bailing to manual login")
            return result
        
        # Fill and submit
        self._fill_signin_form(ws_url, username, password, otp_method)
        
        # Wait for OTP input to appear
        log.info("Waiting for OTP input to appear...")
        if not self._wait_for_otp_input(ws_url, timeout=30):
            # Check if we got redirected to dashboard (login without OTP, maybe remembered)
            url = self._cdp_eval(ws_url, "location.href") or ""
            if "newonline.derayah.com" in url or "/dashboard" in url:
                log.info("Login completed without OTP (session was remembered)")
                # Tokens should be in localStorage now
                time.sleep(2)
                try:
                    result["tokens"] = self.capture_tokens()
                    result["success"] = True
                    return result
                except Exception as e:
                    result["error"] = f"Logged in but capture_tokens failed: {e}"
                    return result
            # Check for reCAPTCHA that appeared
            if self._detect_recaptcha_challenge(ws_url):
                result["error"] = "reCAPTCHA triggered after submit - manual login required"
                return result
            result["error"] = "OTP input never appeared; login may have failed"
            return result
        
        # Check for reCAPTCHA again before fetching OTP
        if self._detect_recaptcha_challenge(ws_url):
            result["error"] = "reCAPTCHA challenge appeared with OTP - manual login required"
            return result
        
        # Fetch OTP from email
        log.info("Fetching OTP from email...")
        otp_code = self._fetch_otp_from_email(
            sender="derayah.com",
            subject_keyword="تفعيل",
            timeout=otp_timeout,
            since_minutes=10
        )
        if not otp_code:
            result["error"] = f"OTP email not received within {otp_timeout}s"
            return result
        
        # Fill and submit OTP
        self._fill_otp(ws_url, otp_code)
        
        # Wait for login complete
        if not self._wait_for_login_complete(ws_url, timeout=30):
            # Check for reCAPTCHA one more time
            if self._detect_recaptcha_challenge(ws_url):
                result["error"] = "reCAPTCHA after OTP - manual login required"
                return result
            result["error"] = "Login did not complete after OTP submit"
            return result
        
        # Capture tokens
        time.sleep(2)
        try:
            result["tokens"] = self.capture_tokens()
            result["success"] = True
            log.info("✅ Auto-recovery complete: login + OTP + capture all succeeded")
        except Exception as e:
            result["error"] = f"Login completed but capture_tokens failed: {e}"
        
        # Clean up any tracker tabs (doubleclick, tiktok, etc) that opened during signin
        # These accumulate when Derayah onboarding loads tracking pixels
        if result["success"]:
            try:
                closed = self._close_extra_tabs()
                if closed > 0:
                    log.info(f"Closed {closed} tracker tab(s) after auto-recovery")
            except Exception as e:
                log.warning(f"Tab cleanup failed: {e}")
            
            # Activate TC tab so ws_probe.py's WebSocket stays active.
            # The auto-recovery flow used the signin/dashboard tab, so the
            # TC tab may have lost focus during recovery.
            try:
                tabs = self._cdp_list_tabs()
                tc = self._find_tc_tab(tabs)
                if tc:
                    self._activate_tab(tc.get("id", ""))
                    log.info("Activated TC tab after auto-recovery")
            except Exception as e:
                log.warning(f"TC tab activation failed: {e}")
            
            # Sync all tokens (dashboard access + TC trading) to JSON file
            # This ensures next refresh_cron uses the fresh access token
            try:
                sync = self.sync_tokens_from_browser()
                result["sync"] = sync
                log.info(f"Post-recovery sync: {len(sync.get('updated', []))} updated, {len(sync.get('kept', []))} kept")
            except Exception as e:
                log.warning(f"Post-recovery sync failed: {e}")
        
        return result

    # ─── Token Helpers ─────────────────────────────────────────────────────────

    def _get_tc_token(self) -> str:
        """Get TC_DERAYAH token from TC tab."""
        tabs = self._cdp_list_tabs()
        tc = self._find_tc_tab(tabs)
        
        if not tc:
            return ""
        
        return self._cdp_eval(
            tc.get("webSocketDebuggerUrl"),
            'JSON.parse(localStorage.getItem("TC_DERAYAH") || "{}").token || ""'
        )

    def _decode_token_expiry(self, token: str) -> float:
        """Decode JWT expiry."""
        try:
            import base64
            parts = token.split(".")
            if len(parts) == 3:
                pad = parts[1] + "=" * (-len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(pad.encode()))
                return payload.get("exp", 0)
        except Exception:
            pass
        return 0

    def _test_api(self, token: str) -> bool:
        """Test API with token."""
        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Origin": "https://derayah.tickerchart.net",
                "Referer": "https://derayah.tickerchart.net/",
            }
            resp = requests.get("https://api.derayah.com/trading/Portfolio/List", headers=headers, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False


# ─── CLI ─────────────────────────────────────────────────────────────────────

# ─── Async-safe wrapper ────────────────────────────────────────────────────

async def auto_login_with_email_otp_async(session_manager=None, otp_timeout: int = 90) -> dict:
    """Async-safe wrapper for auto_login_with_email_otp().
    
    Runs the blocking sync method in a thread pool to avoid blocking
    the asyncio event loop (e.g., when called from bot.py handlers).
    """
    import asyncio
    if session_manager is None:
        session_manager = SessionManager()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,  # default executor
        session_manager.auto_login_with_email_otp,
        otp_timeout
    )


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    
    sm = SessionManager()
    
    if len(sys.argv) < 2:
        print("Usage: python derayah_session_manager.py [capture|refresh|health|auto-login]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "capture":
        try:
            result = sm.capture_tokens()
            print(f"✅ Captured! Access: {len(result.get('Derayah_accesstoken', ''))} chars")
            print(f"   Refresh: {len(result.get('Derayah_refreshtoken', ''))} chars")
        except Exception as e:
            print(f"❌ Capture failed: {e}")
            sys.exit(1)
    
    elif cmd == "refresh":
        try:
            result = sm.refresh_session()
            print(f"✅ Refreshed! TC token valid for {result.get('tc_remaining_min', 0)} min")
        except Exception as e:
            print(f"❌ Refresh failed: {e}")
            sys.exit(1)
    
    elif cmd == "health":
        health = sm.check_health()
        print(json.dumps(health, indent=2))
    
    elif cmd == "auto-login":
        try:
            result = sm.auto_login_with_email_otp()
            if result["success"]:
                print(f"✅ Auto-login succeeded!")
                if result.get("tokens"):
                    print(f"   Tokens: access={len(result['tokens'].get('Derayah_accesstoken',''))} refresh={len(result['tokens'].get('Derayah_refreshtoken',''))}")
            else:
                print(f"❌ Auto-login failed: {result.get('error')}")
                sys.exit(1)
        except Exception as e:
            print(f"❌ Auto-login exception: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

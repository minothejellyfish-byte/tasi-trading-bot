"""
Bot Commands for Derayah Session Management
==============================================

Add these command handlers to bot.py:

    from bot_commands import handle_login, handle_status
    
    # In your command dispatcher:
    if message.text == "/Login":
        await handle_login(update, context)
    elif message.text == "/SS":
        await handle_status(update, context)
"""

import json
import time
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from derayah_session_manager import SessionManager



def check_cdp_running():
    """Check if Chrome CDP is responding on port 18801."""
    try:
        import urllib.request
        req = urllib.request.Request('http://127.0.0.1:18801/json/list', method='GET')
        urllib.request.urlopen(req, timeout=3)
        return True
    except:
        return False


def start_chrome():
    """Start Chrome with CDP."""
    try:
        import subprocess
        subprocess.Popen(
            ['/bin/bash', '/home/mino/tasi-exec/start-chrome.sh'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except Exception as e:
        print(f"Failed to start Chrome: {e}")
        return False


def check_derayah_dashboard():
    """Check if Derayah dashboard is open in Chrome."""
    try:
        import urllib.request
        import json
        req = urllib.request.Request('http://127.0.0.1:18801/json/list', method='GET')
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read())
        for tab in data:
            url = tab.get('url', '')
            if 'newonline.derayah.com' in url or 'derayah' in url.lower():
                return True, tab.get('title', 'Derayah')
        return False, None
    except Exception as e:
        return False, str(e)


def navigate_to_derayah():
    """Navigate to Derayah dashboard via CDP."""
    try:
        import urllib.request
        import json
        import websocket
        
        req = urllib.request.Request('http://127.0.0.1:18801/json/list', method='GET')
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read())
        
        if not data:
            return False
        
        ws_url = data[0]['webSocketDebuggerUrl']
        ws = websocket.create_connection(ws_url, timeout=5)
        ws.send(json.dumps({
            'id': 1,
            'method': 'Page.navigate',
            'params': {'url': 'https://newonline.derayah.com'}
        }))
        ws.close()
        return True
    except Exception as e:
        print(f"Failed to navigate: {e}")
        return False


async def handle_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /Login command — FULLY AUTOMATED Phase 1.
    
    1. Checks if Chrome/CDP is running
    2. If not → starts Chrome automatically
    3. Checks if Derayah dashboard is open
    4. If not → navigates to it
    5. Captures tokens from browser
    """
    
    # Step 1: Check CDP
    if not check_cdp_running():
        await update.message.reply_text("🔧 Chrome not running. Starting Chrome...")
        if not start_chrome():
            await update.message.reply_text("❌ Failed to start Chrome. Please start manually.")
            return
        
        # Wait for Chrome to start
        for i in range(10):
            time.sleep(1)
            if check_cdp_running():
                break
        else:
            await update.message.reply_text("❌ Chrome started but CDP not responding after 10 seconds.")
            return
    
    # Step 2: Check Derayah dashboard
    has_derayah, tab_title = check_derayah_dashboard()
    if not has_derayah:
        await update.message.reply_text("🌐 Derayah not found. Opening Derayah...")
        if navigate_to_derayah():
            time.sleep(3)  # Wait for page load
            has_derayah, tab_title = check_derayah_dashboard()
    
    await update.message.reply_text("🪼 Capturing tokens from browser...")
    
    try:
        sm = SessionManager()
        tokens = sm.capture_tokens()
        
        # Format response
        access_len = len(tokens.get("Derayah_accesstoken", ""))
        refresh_len = len(tokens.get("Derayah_refreshtoken", ""))
        tc_len = len(tokens.get("TC_DERAYAH", ""))
        
        msg = (
            f"✅ *Tokens Captured!*\n\n"
            f"📊 Dashboard token: `{access_len}` chars\n"
            f"🔄 Refresh token: `{refresh_len}` chars\n"
            f"📈 TC token: `{tc_len}` chars\n\n"
            f"Session ready for trading.\n"
            f"Use `/SS` to check status."
        )
        
        await update.message.reply_text(msg, parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ *Capture Failed*\n\n"
            f"Error: `{str(e)}`\n\n"
            f"Please:\n"
            f"1. Login to Derayah dashboard\n"
            f"2. Click 'Login To Derayah Trade'\n"
            f"3. Then run `/Login` again",
            parse_mode="Markdown"
        )


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /SS command - Full system status
    
    Shows:
    - Derayah login status
    - TC tab status + token expiry
    - Poller status
    - WS/Probe status
    """
    await update.message.reply_text("🪼 Checking system status...")
    
    try:
        sm = SessionManager()
        health = sm.check_health()
        
        # Format sections
        lines = ["*📊 System Status*", ""]
        
        # 1. Derayah Login
        lines.append("*1. Derayah Login*")
        if health["dashboard_tab"]:
            lines.append("   ✅ Dashboard: Active")
            lines.append(f"   📁 localStorage: {health.get('localstorage_items', 'N/A')} items")
        else:
            lines.append("   ❌ Dashboard: Not found")
        lines.append("")
        
        # 2. TC Status
        lines.append("*2. TickerChart Session*")
        if health["tc_tab"]:
            lines.append("   ✅ TC Tab: Active")
            if health["tc_token_valid"]:
                lines.append("   ✅ Token: Valid")
                if health["tc_token_expiry"]:
                    exp = health["tc_token_expiry"]
                    # Parse ISO format
                    try:
                        exp_dt = datetime.fromisoformat(exp)
                        lines.append(f"   ⏰ Expires: `{exp_dt.strftime('%H:%M:%S')}`")
                    except:
                        lines.append(f"   ⏰ Expires: `{exp}`")
                if health["tc_remaining_min"]:
                    rem = health["tc_remaining_min"]
                    if rem > 10:
                        lines.append(f"   🟢 Remaining: `{rem:.1f}` min")
                    elif rem > 5:
                        lines.append(f"   🟡 Remaining: `{rem:.1f}` min (refresh soon)")
                    else:
                        lines.append(f"   🔴 Remaining: `{rem:.1f}` min **URGENT**")
            else:
                lines.append("   ❌ Token: Missing")
        else:
            lines.append("   ❌ TC Tab: Not found")
        lines.append("")
        
        # 3. API Status
        lines.append("*3. API Connectivity*")
        if health["api_working"]:
            lines.append("   ✅ Derayah API: Responding")
        else:
            lines.append("   ❌ Derayah API: Not responding")
        lines.append("")
        
        # 4. Poller Status
        lines.append("*4. Trading Bot*")
        try:
            import subprocess
            result = subprocess.run(
                ["pgrep", "-f", "poller.py"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                lines.append("   ✅ Poller: Running")
            else:
                lines.append("   ❌ Poller: Not running")
        except:
            lines.append("   ⚠️ Poller: Check failed")
        
        # Check picks
        try:
            with open("/home/mino/tasi-exec/picks.json") as f:
                picks = json.load(f)
                count = len(picks.get("picks", []))
                lines.append(f"   📊 Picks loaded: `{count}`")
        except:
            lines.append("   ⚠️ Picks: File not found")
        lines.append("")
        
        # 5. WS/Probe Status
        lines.append("*5. WebSocket Data*")
        try:
            import requests
            resp = requests.get("http://127.0.0.1:18801/json/version", timeout=2)
            if resp.status_code == 200:
                lines.append("   ✅ CDP: Responding")
            else:
                lines.append("   ❌ CDP: Error")
        except:
            lines.append("   ❌ CDP: Not responding")
        
        try:
            import subprocess
            result = subprocess.run(
                ["pgrep", "-f", "ws_probe"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                lines.append("   ✅ WS Probe: Running")
            else:
                lines.append("   ❌ WS Probe: Not running")
        except:
            lines.append("   ⚠️ WS Probe: Check failed")
        
        # Check ws_prices file (live trading data from poller.py)
        import glob
        ws_files = glob.glob("/home/mino/tasi-exec/ws_prices_*.jsonl")
        if ws_files:
            ws_file = sorted(ws_files)[-1]  # Latest file
            import os
            mtime = os.path.getmtime(ws_file)
            age = (time.time() - mtime) / 60
            if age < 5:
                lines.append(f"   ✅ Data: Fresh (`{age:.1f}` min ago)")
            elif age < 30:
                lines.append(f"   🟡 Data: Stale (`{age:.1f}` min ago)")
            else:
                lines.append(f"   🔴 Data: Old (`{age:.1f}` min ago)")
        else:
            lines.append("   ❌ Data: No ws_prices files found")
        lines.append("")
        
        # Summary
        lines.append("*Summary:*")
        if health["tc_token_valid"] and health["api_working"]:
            lines.append("   🟢 *Ready for trading*")
        elif health["dashboard_tab"] and not health["tc_token_valid"]:
            lines.append("   🟡 *Need TC refresh* - Run `/Login` after clicking SSO")
        else:
            lines.append("   🔴 *Session issue* - Check dashboard login")
        
        msg = "\n".join(lines)
        await update.message.reply_text(msg, parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ *Status Check Failed*\n\nError: `{str(e)}`",
            parse_mode="Markdown"
        )


# Export for bot.py
__all__ = ["handle_login", "handle_status"]


def validate_session():
    """Validate Derayah session before trading."""
    try:
        sm = SessionManager()
        health = sm.check_health()
        return True, "Session valid"
    except Exception as e:
        return False, f"Session invalid: {e}"


class SessionCommands:
    """Wrapper class for backward compatibility with bot.py"""
    
    async def handle_login(self, update, context):
        return await handle_login(update, context)
    
    async def handle_status(self, update, context):
        return await handle_status(update, context)

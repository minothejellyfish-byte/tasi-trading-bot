#!/usr/bin/env python3
"""
TASI Agent Bridge Handler
Activated by /Tasi command
Deactivated by /Tasix command
"""

import os
import json
import subprocess
from datetime import datetime

BRIDGE_FILE = "/tmp/tasi_bridge_active"
LOG_FILE = "/home/mino/tasi-exec/tasi_bridge.log"
SYSTEM_FILES = [
    "/home/mino/tasi-exec/TASI_SYSTEM_BLUEPRINT.md",
    "/home/mino/tasi-exec/TASI_SYSTEM_REFERENCE.md",
    "/home/mino/tasi-exec/TASI_AGENT_MEMORY.md",
    "/home/mino/tasi-exec/TASI_Trading_Blueprint.md"
]

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)
    return line.strip()

def is_bridge_active():
    return os.path.exists(BRIDGE_FILE)

def activate_bridge():
    with open(BRIDGE_FILE, "w") as f:
        f.write(datetime.now().isoformat())
    return log("🟢 TASI Bridge ACTIVATED")

def deactivate_bridge():
    if os.path.exists(BRIDGE_FILE):
        os.remove(BRIDGE_FILE)
    return log("🔴 TASI Bridge DEACTIVATED")

def get_system_status():
    status = []
    
    # Check processes
    procs = subprocess.run("ps aux | grep -E 'bot.py|poller.py|ws_probe' | grep -v grep", 
                          shell=True, capture_output=True, text=True)
    status.append(f"**Processes:**\n```\n{procs.stdout or 'None running'}\n```")
    
    # Check capital
    try:
        with open("/home/mino/tasi-exec/capital.json") as f:
            cap = json.load(f)
        status.append(f"**Capital:** {cap.get('available_capital', 'N/A')} SAR | Grand Total: {cap.get('grand_total', 'N/A')} SAR")
    except:
        status.append("**Capital:** File not found")
    
    # Check positions
    try:
        with open("/home/mino/tasi-exec/positions.json") as f:
            pos = json.load(f)
        open_pos = [p for p in pos.get("positions", []) if not p.get("closed", False)]
        status.append(f"**Positions:** {len(open_pos)} open")
    except:
        status.append("**Positions:** File not found")
    
    # Check regime
    try:
        with open("/home/mino/tasi-exec/regime.json") as f:
            reg = json.load(f)
        status.append(f"**Regime:** {reg.get('regime', 'N/A')} (return: {reg.get('session_return', 'N/A')}%)")
    except:
        status.append("**Regime:** File not found")
    
    # Check stand_down
    if os.path.exists("/home/mino/tasi-exec/stand_down"):
        status.append("**STAND DOWN:** 🛑 ACTIVE (No buys allowed)")
    else:
        status.append("**STAND DOWN:** ✅ Not active")
    
    # Check market time
    from datetime import datetime
    now = datetime.now()
    hour = now.hour
    if 10 <= hour < 15:
        status.append(f"**Market:** 🟢 OPEN (closes in {15-hour} hours)")
    else:
        status.append("**Market:** 🔴 CLOSED (opens 10:00 AM)")
    
    return "\n".join(status)

def handle_command(text, chat_id, send_fn):
    text = text.strip()
    
    # Activation
    if text == "/Tasi":
        activate_bridge()
        welcome = """🎯 **TASI Agent Bridge ACTIVATED**

Welcome to TASI Trading Control. I have full system knowledge:
- ✅ System Blueprint (architecture, components, files)
- ✅ Quick Reference (commands, schedule, fees)
- ✅ Agent Memory (daily schedule, changes, status)
- ✅ Trading Blueprint (strategies, backtests)

**Current Status:**
"""
        status = get_system_status()
        send_fn(welcome + "\n" + status)
        return True
    
    # Deactivation
    if text == "/Tasix":
        # Log everything before closing
        log("="*50)
        log("SESSION LOG ARCHIVED")
        log("="*50)
        
        # Read bridge log
        bridge_log = ""
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE) as f:
                bridge_log = f.read()
        
        deactivate_bridge()
        
        # Archive the session
        archive_msg = f"""🔴 **TASI Agent Bridge CLOSED**

📋 **Session Log:**
```
{bridge_log[-2000:] if len(bridge_log) > 2000 else bridge_log}
```

✅ All commands logged to: `{LOG_FILE}`
✅ Bridge deactivated. Back to normal mode.
"""
        send_fn(archive_msg)
        return True
    
    # Only process other commands if bridge is active
    if not is_bridge_active():
        return False
    
    # Bridge commands
    if text.lower() == "status":
        log("Command: STATUS")
        send_fn(get_system_status())
        return True
    
    if text.lower() == "help":
        log("Command: HELP")
        help_text = """📖 **TASI Bridge Commands:**

| Command | Action |
|---------|--------|
| `status` | Full system status |
| `capital` | Show capital details |
| `positions` | List open positions |
| `regime` | Show market regime |
| `schedule` | Today's schedule |
| `files` | List key files |
| `help` | This help message |
| `/Tasix` | Close bridge |
"""
        send_fn(help_text)
        return True
    
    if text.lower() == "capital":
        log("Command: CAPITAL")
        try:
            with open("/home/mino/tasi-exec/capital.json") as f:
                cap = json.load(f)
            msg = f"""💰 **Capital Status:**
- Available: {cap.get('available_capital', 'N/A')} SAR
- Grand Total: {cap.get('grand_total', 'N/A')} SAR
- Securities Value: {cap.get('securities_value', 'N/A')} SAR
- Money Transfer: {cap.get('money_transfer', 'N/A')} SAR
- Total Fees: {cap.get('total_fees', 'N/A')} SAR
- Initial: {cap.get('initial_capital', 'N/A')} SAR
- Updated: {cap.get('updated_at', 'N/A')}
"""
            send_fn(msg)
        except Exception as e:
            send_fn(f"❌ Error: {e}")
        return True
    
    if text.lower() == "positions":
        log("Command: POSITIONS")
        try:
            with open("/home/mino/tasi-exec/positions.json") as f:
                pos = json.load(f)
            open_pos = [p for p in pos.get("positions", []) if not p.get("closed", False)]
            if not open_pos:
                send_fn("📭 **No open positions**")
            else:
                msg = "📊 **Open Positions:**\n"
                for p in open_pos:
                    msg += f"- {p.get('symbol', 'N/A')}: {p.get('qty', 0)} shares @ {p.get('entry_price', 'N/A')} SAR\n"
                send_fn(msg)
        except Exception as e:
            send_fn(f"❌ Error: {e}")
        return True
    
    if text.lower() == "regime":
        log("Command: REGIME")
        try:
            with open("/home/mino/tasi-exec/regime.json") as f:
                reg = json.load(f)
            msg = f"""📈 **Market Regime:**
- Regime: {reg.get('regime', 'N/A')}
- Session Return: {reg.get('session_return', 'N/A')}%
- VWAP: {reg.get('vwap', 'N/A')}
- Above VWAP: {reg.get('above_vwap', 'N/A')}
- Strategy: {reg.get('params', {}).get('strategy', 'N/A')}
- Max Positions: {reg.get('params', {}).get('max_positions', 'N/A')}
- Target: {reg.get('params', {}).get('target_pct', 'N/A')}%
- Hard Stop: {reg.get('params', {}).get('hard_stop', 'N/A')}%
"""
            send_fn(msg)
        except Exception as e:
            send_fn(f"❌ Error: {e}")
        return True
    
    if text.lower() == "schedule":
        log("Command: SCHEDULE")
        msg = """📅 **Today's Schedule (Sun-Thu):**

| Time | Action |
|------|--------|
| 09:50 | Premarket screener |
| 09:55 | Remove STAND DOWN |
| 10:00 | Market opens + poller starts |
| 10:05 | Map selectors |
| 10:07 | WS probe starts |
| 10:30 | Mid-screen #1 |
| 12:00 | Mid-screen #2 |
| 13:30 | Rescreen |
| 14:45 | Hard close (STAND DOWN) |
| 15:00 | Market closes |
| 15:35 | Post-market analysis |
"""
        send_fn(msg)
        return True
    
    if text.lower() == "files":
        log("Command: FILES")
        msg = """📁 **Key System Files:**

| File | Purpose |
|------|---------|
| `bot.py` | Telegram bot + keepalive |
| `poller.py` | Price polling + auto-trade |
| `screener.py` | Premarket scanner |
| `midscreen_ws.py` | Mid-session screens |
| `market_regime.py` | Regime classifier |
| `capital.json` | Account balance |
| `positions.json` | Open positions |
| `regime.json` | Current regime |
"""
        send_fn(msg)
        return True
    
    # Unrecognized command
    log(f"Unknown command: {text}")
    send_fn(f"❓ Unknown command: `{text}`. Type `help` for available commands.")
    return True

if __name__ == "__main__":
    # Test mode
    print("TASI Bridge Handler loaded")
    print(f"Bridge active: {is_bridge_active()}")
    print(get_system_status())

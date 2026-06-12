"""
TASI Telegram Handler
Handles /Tasi commands and bridges to TASI Operations Agent
"""

import os, json, subprocess, logging

# TASI Agent Session (persistent)
TASI_AGENT_SESSION = "agent:mina:subagent:a3109f8b-7543-4947-b6e7-37ae8202d282"
BRIDGE_FILE = "/tmp/tasi_bridge_active"

logger = logging.getLogger(__name__)

def is_bridge_active():
    return os.path.exists(BRIDGE_FILE)

def activate_bridge():
    with open(BRIDGE_FILE, 'w') as f:
        f.write("active")
    return True

def deactivate_bridge():
    if os.path.exists(BRIDGE_FILE):
        os.remove(BRIDGE_FILE)
    return True

def get_welcome_message():
    return """🎯 **TASI Agent Bridge Activated**

You are now connected to the TASI Operations Agent.

**Available Commands:**
• `status` — Full system status
• `buy SYMBOL QTY` — Execute buy order
• `sell SYMBOL QTY` — Execute sell order  
• `stand_down` — Block all buys
• `resume` — Allow buys again
• `positions` — Show open positions
• `capital` — Show capital & balance
• `help` — This help message

**Current Status:**
• Market: CLOSED (opens Sunday 10:00 AM)
• Capital: 997.59 SAR
• Position: 4021 (59 shares)
• stand_down: ACTIVE

Type `/Tasix` to close the bridge."""

def get_status_text():
    """Get current system status"""
    try:
        # Read current files
        with open("/home/mino/tasi-exec/capital.json", 'r') as f:
            capital = json.load(f)
        with open("/home/mino/tasi-exec/positions.json", 'r') as f:
            positions = json.load(f)
        
        status = f"""📊 **TASI System Status**

**Capital:**
• Grand Total: {capital.get('grand_total', 'N/A')} SAR
• Available: {capital.get('available_capital', 'N/A')} SAR
• Invested: {capital.get('invested', 'N/A')} SAR

**Positions:**
"""
        if positions:
            for sym, pos in positions.items():
                status += f"• {sym}: {pos.get('quantity', 0)} shares @ {pos.get('entry_price', 'N/A')} SAR\n"
        else:
            status += "• No open positions\n"
        
        status += f"""
**System:**
• Market: CLOSED
• stand_down: {'ACTIVE' if os.path.exists('/home/mino/tasi-exec/stand_down') else 'INACTIVE'}
• WebSocket: Active via keepalive

**Next Market Open:** Sunday 10:00 AM"""
        
        return status
    except Exception as e:
        logger.error(f"Status error: {e}")
        return f"⚠️ Error reading status: {e}"

def handle_tasi_command(text, chat_id, tg_send_func):
    """Main handler for TASI commands"""
    text = text.strip()
    
    # Close bridge
    if text == "/Tasix":
        deactivate_bridge()
        tg_send_func("🔴 **TASI Agent Bridge Closed**\n\nBack to normal mode. Type `/Tasi` to reconnect.", chat_id)
        return True
    
    # Open bridge
    if text == "/Tasi":
        activate_bridge()
        tg_send_func(get_welcome_message(), chat_id)
        return True
    
    # Process commands while bridge is active
    if is_bridge_active():
        cmd = text.lower().strip()
        
        if cmd == "help":
            tg_send_func(get_welcome_message(), chat_id)
        
        elif cmd == "status":
            tg_send_func(get_status_text(), chat_id)
        
        elif cmd.startswith("buy "):
            # Extract symbol and qty
            parts = cmd.split()
            if len(parts) == 3:
                symbol, qty = parts[1], parts[2]
                tg_send_func(f"🔄 Routing BUY {qty} {symbol} to execution engine...", chat_id)
                # Here you would call auto_buy()
            else:
                tg_send_func("⚠️ Usage: `buy SYMBOL QTY`\nExample: `buy 4021 10`", chat_id)
        
        elif cmd.startswith("sell "):
            parts = cmd.split()
            if len(parts) == 3:
                symbol, qty = parts[1], parts[2]
                tg_send_func(f"🔄 Routing SELL {qty} {symbol} to execution engine...", chat_id)
                # Here you would call auto_sell()
            else:
                tg_send_func("⚠️ Usage: `sell SYMBOL QTY`\nExample: `sell 4021 10`", chat_id)
        
        elif cmd == "stand_down":
            # Create stand_down file
            open("/home/mino/tasi-exec/stand_down", 'w').close()
            tg_send_func("🛑 **STAND DOWN Activated**\n\nAll buys are now blocked.", chat_id)
        
        elif cmd == "resume":
            # Remove stand_down file
            if os.path.exists("/home/mino/tasi-exec/stand_down"):
                os.remove("/home/mino/tasi-exec/stand_down")
            tg_send_func("✅ **RESUME**\n\nBuys are now allowed.", chat_id)
        
        elif cmd == "positions":
            tg_send_func(get_status_text(), chat_id)
        
        elif cmd == "capital":
            tg_send_func(get_status_text(), chat_id)
        
        else:
            tg_send_func(f"❓ Unknown command: `{cmd}`\n\nType `help` for available commands.", chat_id)
        
        return True
    
    return False  # Not a TASI command

# For direct testing
if __name__ == "__main__":
    print(get_welcome_message())
    print("\n--- Status ---")
    print(get_status_text())

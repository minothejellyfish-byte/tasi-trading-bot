#!/usr/bin/env python3
"""
Script to send rescreen results to the TASI Execution Telegram group
"""

import asyncio
import os
import sys
import json
from telegram import Bot

# Configuration
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8989533040:AAFWzP_lYL3g_w4eXGxrvwdo-tBNdPxVYQU")
GROUP_CHAT_ID = -5235925419

def format_rescreen_message(picks_data):
    """Format the rescreen results into a message"""
    picks = picks_data.get('picks', [])
    date = picks_data.get('date', 'Unknown')
    
    message = f"📊 **Rescreen (13:30)** — {len(picks)} picks\n"
    message += "| # | Symbol | Entry Zone | Score | Change |\n"
    message += "|---|---|---|---|---|\n"
    
    for i, pick in enumerate(picks, 1):
        symbol = pick.get('symbol', 'N/A')
        entry_high = pick.get('entry_high', 0)
        entry_low = pick.get('entry_low', 0)
        score = pick.get('score', 0)
        change_pct = pick.get('pm_metrics', {}).get('change_pct', 0)
        
        message += f"| {i} | {symbol} | {entry_low} – {entry_high} | {score} | {change_pct}% |\n"
    
    return message

async def send_message(message):
    """Send message to Telegram group"""
    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(chat_id=GROUP_CHAT_ID, text=message, parse_mode="Markdown")
        print("Message sent successfully!")
        return True
    except Exception as e:
        print(f"Failed to send message: {e}")
        return False

def main():
    # Check if picks file exists
    picks_file = "/home/mino/tasi-exec/picks_1330.json"
    if not os.path.exists(picks_file):
        print("Picks file not found!")
        sys.exit(1)
    
    # Read picks data
    try:
        with open(picks_file, 'r') as f:
            picks_data = json.load(f)
    except Exception as e:
        print(f"Failed to read picks file: {e}")
        sys.exit(1)
    
    # Format message
    message = format_rescreen_message(picks_data)
    print("Sending message:")
    print(message)
    
    # Send message
    success = asyncio.run(send_message(message))
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()
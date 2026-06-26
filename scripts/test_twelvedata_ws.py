#!/usr/bin/env python3
"""Test Twelve Data WebSocket for real-time streaming."""

import websocket
import json
import time
from datetime import datetime

API_KEY = "***"  # Using demo key

def on_message(ws, message):
    data = json.loads(message)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Message: {json.dumps(data, indent=2)[:300]}")

def on_error(ws, error):
    print(f"Error: {error}")

def on_close(ws, close_status_code, close_msg):
    print(f"Closed: {close_status_code} - {close_msg}")

def on_open(ws):
    print("WebSocket connected!")
    
    # Subscribe to AAPL real-time price
    subscribe_msg = {
        "action": "subscribe",
        "params": {
            "symbols": "AAPL",
            "apikey": API_KEY
        }
    }
    ws.send(json.dumps(subscribe_msg))
    print(f"Sent subscription: {subscribe_msg}")
    
    # Keep connection alive for 30 seconds
    time.sleep(30)
    ws.close()

if __name__ == "__main__":
    print("=" * 50)
    print("Twelve Data WebSocket Test")
    print("=" * 50)
    
    # Twelve Data WebSocket endpoint
    ws_url = "wss://ws.twelvedata.com/v1/quotes/price"
    
    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    
    # Run for max 35 seconds
    ws.run_forever()
    
    print("\n" + "=" * 50)
    print("Test complete!")
    print("=" * 50)

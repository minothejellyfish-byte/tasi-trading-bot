#!/usr/bin/env python3
"""
US Poller with Alpaca WebSocket primary + Twelve Data REST fallback.
Falls back to Twelve Data REST polling when Alpaca WebSocket disconnects.
"""

import os
import json
import time
import asyncio
import websockets
import requests
from datetime import datetime
from pathlib import Path

# Configuration
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "***")  # Replace with real key

ALPACA_WS_URL = "wss://stream.data.alpaca.markets/v2/iex"
TWELVE_DATA_QUOTE_URL = "https://api.twelvedata.com/quote"

# Symbols to track
SYMBOLS = ["AAPL", "MSFT", "TSLA", "NVDA", "AMZN", "GOOGL", "META", "SPY", "QQQ"]

class DualSourcePoller:
    def __init__(self):
        self.alpaca_connected = False
        self.prices = {}
        self.last_alpaca_msg = 0
        self.fallback_active = False
        
    async def alpaca_websocket(self):
        """Primary: Alpaca WebSocket for real-time prices."""
        while True:
            try:
                print(f"[{datetime.now()}] Connecting to Alpaca WebSocket...")
                
                auth_msg = {
                    "action": "auth",
                    "key": ALPACA_API_KEY,
                    "secret": ALPACA_SECRET_KEY
                }
                
                subscribe_msg = {
                    "action": "subscribe",
                    "quotes": SYMBOLS,
                    "trades": SYMBOLS
                }
                
                async with websockets.connect(ALPACA_WS_URL) as ws:
                    await ws.send(json.dumps(auth_msg))
                    await ws.send(json.dumps(subscribe_msg))
                    
                    self.alpaca_connected = True
                    self.fallback_active = False
                    print(f"[{datetime.now()}] Alpaca WebSocket connected!")
                    
                    async for message in ws:
                        data = json.loads(message)
                        for item in data:
                            if item.get("T") == "q":  # Quote
                                symbol = item["S"]
                                self.prices[symbol] = {
                                    "bid": item.get("bp", 0),
                                    "ask": item.get("ap", 0),
                                    "source": "alpaca",
                                    "timestamp": datetime.now().isoformat()
                                }
                                self.last_alpaca_msg = time.time()
                                
            except Exception as e:
                print(f"[{datetime.now()}] Alpaca WebSocket error: {e}")
                self.alpaca_connected = False
                self.fallback_active = True
                await asyncio.sleep(5)  # Reconnect delay
                
    async def twelve_data_fallback(self):
        """Fallback: Twelve Data REST polling every 15 seconds."""
        while True:
            if self.fallback_active or not self.alpaca_connected:
                try:
                    symbols_str = ",".join(SYMBOLS[:5])  # Batch 5 symbols per call (within 8/min limit)
                    url = f"{TWELVE_DATA_QUOTE_URL}?symbol={symbols_str}&apikey=***"
                    
                    resp = requests.get(url, timeout=10)
                    data = resp.json()
                    
                    if isinstance(data, list):
                        for quote in data:
                            symbol = quote.get("symbol", "")
                            if symbol:
                                self.prices[symbol] = {
                                    "price": quote.get("price", 0),
                                    "open": quote.get("open", 0),
                                    "high": quote.get("high", 0),
                                    "low": quote.get("low", 0),
                                    "volume": quote.get("volume", 0),
                                    "source": "twelvedata",
                                    "timestamp": datetime.now().isoformat()
                                }
                        print(f"[{datetime.now()}] Twelve Data fallback: {len(data)} quotes updated")
                    
                except Exception as e:
                    print(f"[{datetime.now()}] Twelve Data error: {e}")
                    
            await asyncio.sleep(15)  # Poll every 15 seconds
            
    async def health_monitor(self):
        """Monitor connection health and switch sources."""
        while True:
            now = time.time()
            
            # If no Alpaca message for 30 seconds, activate fallback
            if self.alpaca_connected and (now - self.last_alpaca_msg) > 30:
                print(f"[{datetime.now()}] Alpaca stale for 30s, activating Twelve Data fallback")
                self.fallback_active = True
                
            # Log current state
            sources = {"alpaca": 0, "twelvedata": 0}
            for p in self.prices.values():
                sources[p.get("source", "unknown")] += 1
                
            print(f"[{datetime.now()}] Prices: {len(self.prices)} | Sources: {sources} | Fallback: {self.fallback_active}")
            
            await asyncio.sleep(10)
            
    async def run(self):
        """Run both sources concurrently."""
        await asyncio.gather(
            self.alpaca_websocket(),
            self.twelve_data_fallback(),
            self.health_monitor()
        )

if __name__ == "__main__":
    print("=" * 60)
    print("US Poller: Alpaca WebSocket + Twelve Data Fallback")
    print("=" * 60)
    
    poller = DualSourcePoller()
    asyncio.run(poller.run())

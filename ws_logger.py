#!/usr/bin/env python3
"""
WS Price Logger - Saves TickerChart WebSocket prices to file.
"""
import json
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path("/home/mino/tasi-exec")

def log_price(symbol: str, price: float, change: float, pchange: float, real: bool):
    """Log a price update to the daily file."""
    # Create daily file
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = BASE_DIR / f"ws_prices_{date_str}.jsonl"
    
    entry = {
        "ts": time.time(),
        "time": datetime.now().isoformat(),
        "symbol": symbol,
        "price": price,
        "change": change,
        "pchange": pchange,
        "real": real,
    }
    
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        # Fail silently - logging shouldn't break trading
        pass

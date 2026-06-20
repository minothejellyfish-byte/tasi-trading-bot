#!/usr/bin/env python3
"""
WS Price Logger - Saves TickerChart WebSocket prices to file.
v4.7: Added liquidity direction fields (bidvolume, askvolume, tbv, tav, ratios)
"""
import json
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path("/home/mino/tasi-exec")

def log_price(symbol: str, price: float, change: float, pchange: float, real: bool,
              vwap: float = None, volume: float = 0.0,
              bidvolume: float = 0.0, askvolume: float = 0.0,
              tbv: float = 0.0, tav: float = 0.0,
              liquidity_ratio: float = 1.0, net_flow: float = 0.0,
              total_depth_ratio: float = 1.0):
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

    # Add VWAP and volume if available
    if vwap is not None:
        entry["vwap"] = vwap
    if volume:
        entry["volume"] = volume

    # v4.7: Liquidity direction fields
    if bidvolume or askvolume:
        entry["bidvolume"] = bidvolume
        entry["askvolume"] = askvolume
    if tbv or tav:
        entry["tbv"] = tbv
        entry["tav"] = tav
    if liquidity_ratio != 1.0:
        entry["liquidity_ratio"] = round(liquidity_ratio, 4)
    if net_flow != 0.0:
        entry["net_flow"] = round(net_flow, 4)
    if total_depth_ratio != 1.0:
        entry["total_depth_ratio"] = round(total_depth_ratio, 4)

    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        # Fail silently - logging shouldn't break trading
        pass

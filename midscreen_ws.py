#!/usr/bin/env python3
"""
WebSocket-based Mid-Screen Screener
Uses real-time Derayah/TickerChart websocket data instead of delayed yfinance.
"""

import json
import logging
import os
from datetime import datetime, time, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd
import pytz

RIYADH = pytz.timezone("Asia/Riyadh")
BASE_DIR = Path("/home/mino/tasi-exec")
SHARIA_FILE = BASE_DIR / "sharia_list.json"
WS_LOG_DIR = BASE_DIR

log = logging.getLogger(__name__)


def load_sharia_tickers():
    """Load Sharia-compliant tickers (base symbols without .SR)."""
    with open(SHARIA_FILE) as f:
        data = json.load(f)
    stocks = data.get("stocks", [])
    return [s["symbol"] for s in stocks if s.get("symbol", "").isdigit()]


def load_ws_data(date_str: str) -> dict:
    """Load websocket prices for a given date. Returns {symbol: [ticks]}."""
    ws_file = WS_LOG_DIR / f"ws_prices_{date_str}.jsonl"
    if not ws_file.exists():
        log.warning(f"No websocket file: {ws_file}")
        return {}

    data = {}
    with open(ws_file) as f:
        for line in f:
            try:
                d = json.loads(line)
                sym = d["symbol"]
                t = datetime.fromisoformat(d["time"])
                if t.tzinfo is None:
                    t = RIYADH.localize(t)
                if sym not in data:
                    data[sym] = []
                data[sym].append({"time": t, "price": d["price"]})
            except:
                continue
    return data


def build_bars(ws_data: dict, start_time: time, end_time: time) -> dict:
    """Build OHLC bars from websocket ticks for a time window."""
    bars = {}
    for sym, ticks in ws_data.items():
        window_ticks = [t for t in ticks if start_time <= t["time"].time() <= end_time]
        if len(window_ticks) < 5:
            continue

        prices = [t["price"] for t in window_ticks]
        bars[sym] = {
            "symbol": sym,
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
            "ticks": len(window_ticks),
            "change_pct": (prices[-1] - prices[0]) / prices[0] * 100,
            "range_pct": (max(prices) - min(prices)) / prices[0] * 100,
        }
    return bars


def score_intraday(bar: dict) -> float:
    """Score a stock based on intraday momentum."""
    score = 0

    # Change (directional move)
    change = bar["change_pct"]
    score += min(abs(change), 5) * 10  # Cap at 5% for 50 pts
    if change > 0:
        score += 5  # Bonus for positive move

    # Range (volatility)
    score += min(bar["range_pct"], 3) * 5  # Cap at 3% for 15 pts

    # Tick density (liquidity proxy)
    ticks = bar["ticks"]
    score += min(ticks / 50, 5)  # More ticks = more liquid

    return score


def run_midscreen(mode: str = "midscreen1", picks_file: str = None, top_n: int = 5) -> dict:
    """
    Run mid-screen using websocket data.

    mode:
    - midscreen1: 10:00-10:30 (early momentum)
    - midscreen2: 11:00-12:00 (last hour)
    - rescreen: 12:00-13:30 (pre-cutoff)
    """
    now = datetime.now(RIYADH)
    date_str = now.strftime("%Y-%m-%d")

    # Time windows
    if mode == "midscreen1":
        start, end = time(10, 0), time(10, 30)
    elif mode == "midscreen2":
        start, end = time(11, 0), time(12, 0)
    elif mode == "rescreen":
        start, end = time(12, 0), time(13, 30)
    else:
        log.error(f"Unknown mode: {mode}")
        return None

    log.info(f"[{mode}] Running mid-screen: {start}-{end}")

    # Load websocket data
    ws_data = load_ws_data(date_str)
    if not ws_data:
        log.error(f"[{mode}] No websocket data available — cannot run mid-screen")
        return None

    # Build bars from websocket
    bars = build_bars(ws_data, start, end)
    log.info(f"[{mode}] Built {len(bars)} bars from websocket")

    if len(bars) < 10:
        log.warning(f"[{mode}] Only {len(bars)} bars from websocket — insufficient data")
        return None

    # Score all bars
    scored = []
    for sym, bar in bars.items():
        bar["score"] = score_intraday(bar)
        scored.append(bar)

    # Sort by score
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:top_n]

    # Get entry zones from yesterday's daily data
    picks = []
    for bar in top:
        sym = bar["symbol"]
        try:
            ticker = yf.Ticker(f"{sym}.SR")
            # Try 2 days first, fallback to 1 day if insufficient
            df = ticker.history(period="2d", interval="1d")
            if len(df) >= 2:
                prev_high = float(df["High"].iloc[-2])
                prev_low = float(df["Low"].iloc[-2])
                prev_close = float(df["Close"].iloc[-2])
            elif len(df) == 1:
                # Fallback: use 1 day of data
                prev_high = float(df["High"].iloc[-1])
                prev_low = float(df["Low"].iloc[-1])
                prev_close = float(df["Close"].iloc[-1])
                log.warning(f"[{mode}] {sym}: only 1 day data available, using last bar")
            else:
                # No data - derive from websocket bars
                prev_high = bar["high"]
                prev_low = bar["low"]
                prev_close = bar["close"]
                log.warning(f"[{mode}] {sym}: no yfinance data, using websocket bar")
            
            if prev_close >= prev_high * 0.99:
                # v4.1: Wider entry zone (close * 0.98)
                e_lo = round(min(prev_high * 0.995, prev_close * 0.98), 2)
                e_hi = round(prev_close * 1.01, 2)
            else:
                e_lo = round(prev_low * 0.998, 2)
                e_hi = round(prev_high * 1.002, 2)
        except Exception as e:
            log.error(f"[{mode}] {sym}: entry zone calc failed - {e}")
            # Fallback to websocket-derived zones
            e_lo = round(bar["low"] * 0.99, 2)
            e_hi = round(bar["high"] * 1.01, 2)

        picks.append({
            "symbol": f"{sym}.SR",
            "entry_high": e_hi,
            "entry_low": e_lo,
            "score": round(bar["score"], 1),
            "pm_metrics": {
                "change_pct": round(bar["change_pct"], 2),
                "range_pct": round(bar["range_pct"], 2),
                "ticks": bar["ticks"],
            },
            "tier": "midscreen",
            "source": mode,
        })

    picks_data = {
        "date": date_str,
        "mode": mode,
        "window": f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}",
        "picks": picks,
    }

    if picks_file:
        # v4.1: Atomic write for picks file (prevents corruption)
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json', dir=os.path.dirname(picks_file))
        json.dump(picks_data, tmp, indent=2)
        tmp.close()
        os.rename(tmp.name, picks_file)
        log.info(f"[{mode}] Wrote {len(picks)} picks to {picks_file}")

    return picks_data


def main():
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "midscreen1"
    picks_file = sys.argv[2] if len(sys.argv) > 2 else None

    result = run_midscreen(mode=mode, picks_file=picks_file)
    if result:
        print(f"[{mode}] Found {len(result['picks'])} picks")
        for i, p in enumerate(result["picks"], 1):
            pm = p["pm_metrics"]
            print(f"{i}. {p['symbol']}: score={p['score']} change={pm['change_pct']:+.2f}% range={pm['range_pct']:.2f}% ticks={pm['ticks']}")
    else:
        print(f"[{mode}] No picks found")


if __name__ == "__main__":
    main()

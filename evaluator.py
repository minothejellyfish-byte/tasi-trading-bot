#!/usr/bin/env python3
"""
Pick Evaluator — v4.11
Runs every 30 min (10:15, 10:45, 11:15, 11:45, 12:15, 12:45, 13:15, 13:45, 14:15)
Re-evaluates existing picks and adds evaluator_score, evaluator_action, evaluator_note.
Overwrites picks.json with evaluation fields.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np
import yfinance as yf

# ─── Config ──────────────────────────────────────────────────────────────────

BASE_DIR     = Path("/home/mino/tasi-exec")
PICKS_FILE   = BASE_DIR / "picks.json"
LOG_FILE     = BASE_DIR / "evaluator.log"

RIYADH = datetime.now().astimezone().tzinfo  # Use system tz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ─── Helpers ─────────────────────────────────────────────────────────────────

def load_picks_all():
    """Load all picks from picks.json."""
    if not PICKS_FILE.exists():
        return []
    try:
        with open(PICKS_FILE) as f:
            data = json.load(f)
        return data.get("picks", [])
    except Exception as e:
        log.error(f"Failed to load picks: {e}")
        return []


def save_picks_atomic(picks):
    """Atomic write to picks.json."""
    import tempfile
    data = {
        "date": datetime.now().date().isoformat(),
        "mode": "evaluated",
        "evaluated_at": datetime.now().isoformat(),
        "picks": picks,
    }
    tmp = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json', dir=BASE_DIR)
    json.dump(data, tmp, indent=2, default=str)
    tmp.close()
    os.rename(tmp.name, PICKS_FILE)
    log.info(f"Saved {len(picks)} evaluated picks to {PICKS_FILE}")


def fetch_data(symbol):
    """Fetch current price and intraday df."""
    try:
        df = yf.download(symbol, period="1d", interval="5m", progress=False)
        if df is not None and not df.empty:
            price = float(df["Close"].iloc[-1])
            return price, df
    except Exception as e:
        log.debug(f"fetch_data {symbol}: {e}")
    return None, None


def calc_momentum_slope(df):
    """Calculate momentum slope from last 10 closes."""
    if df is None or len(df) < 5:
        return 0.0
    try:
        closes = df["Close"].tail(10).values
        if len(closes) < 5:
            return 0.0
        x = np.arange(len(closes))
        slope, _ = np.polyfit(x, closes, 1)
        # Normalize: slope as % of mean price
        mean_price = np.mean(closes)
        if mean_price > 0:
            return (slope / mean_price) * 100  # % per bar
        return 0.0
    except Exception as e:
        log.debug(f"calc_momentum_slope error: {e}")
        return 0.0


def get_ws_metrics(symbol):
    """Read WS metrics from ws_prices jsonl file."""
    date_str = datetime.now().date().isoformat()
    ws_file = BASE_DIR / f"ws_prices_{date_str}.jsonl"
    
    if not ws_file.exists():
        return {}
    
    metrics = {
        "liquidity_ratio": 1.0,
        "spread_pct": 0.0,
        "net_flow": 0.0,
        "last_price": 0,
    }
    
    try:
        with open(ws_file) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("symbol") == symbol:
                        metrics["liquidity_ratio"] = entry.get("liquidity_ratio", 1.0)
                        metrics["spread_pct"] = entry.get("spread_pct", 0.0)
                        metrics["net_flow"] = entry.get("net_flow", 0.0)
                        metrics["last_price"] = entry.get("price", 0)
                except:
                    continue
    except Exception as e:
        log.debug(f"get_ws_metrics {symbol}: {e}")
    
    return metrics


def evaluate_pick(pick, price, df):
    """
    Evaluate a single pick and return (action, score, note).
    
    States:
    - KEEP: Valid, in zone, momentum good
    - STALE: In zone but momentum fading
    - TRENDING: Above zone, strong momentum (don't chase)
    - RECOVERING: Below zone but recovering
    - SCRATCH: Fading, below zone, weak momentum
    """
    symbol = pick.get("symbol", "")
    e_lo = pick.get("entry_low", 0)
    e_hi = pick.get("entry_high", 0)
    
    # Default
    if not price or not e_lo or not e_hi:
        return "KEEP", 50, "No data available"
    
    # Zone position
    if e_lo <= price <= e_hi:
        zone = "IN_ZONE"
    elif price > e_hi:
        zone = "ABOVE"
    else:
        zone = "BELOW"
    
    # Momentum
    slope = calc_momentum_slope(df)
    
    # WS metrics
    base = symbol.replace(".SR", "")
    ws = get_ws_metrics(base)
    liq = ws.get("liquidity_ratio", 1.0)
    
    # Decision matrix
    if zone == "IN_ZONE":
        if slope > 0.1 and liq > 1.0:
            return "KEEP", 90, f"Valid: in zone, momentum +{slope:.2f}%, liquidity {liq:.2f}"
        elif slope < -0.1:
            return "STALE", 50, f"Stale: in zone but fading (momentum {slope:.2f}%, liquidity {liq:.2f})"
        else:
            return "KEEP", 75, f"Stable: in zone, flat momentum {slope:.2f}%, liquidity {liq:.2f}"
    
    elif zone == "ABOVE":
        if slope > 0.2 and liq > 1.5:
            return "TRENDING", 70, f"Strong: above zone, momentum +{slope:.2f}%, liquidity {liq:.2f} — don't chase"
        elif slope > 0:
            return "TRENDING", 60, f"Rising: above zone, momentum +{slope:.2f}% — wait for pullback"
        else:
            return "STALE", 40, f"Saturated: above zone, fading momentum {slope:.2f}%"
    
    elif zone == "BELOW":
        if slope > 0.1 and price > e_lo * 0.98:
            return "RECOVERING", 65, f"Recovering: below zone but rising (momentum +{slope:.2f}%, liquidity {liq:.2f})"
        elif slope > 0:
            return "RECOVERING", 55, f"Bounce: below zone, weak recovery (momentum +{slope:.2f}%)"
        else:
            return "SCRATCH", 20, f"Falling: below zone, negative momentum {slope:.2f}%, liquidity {liq:.2f}"
    
    return "KEEP", 50, "Unknown state"


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now()
    log.info(f"Evaluator started at {now.strftime('%Y-%m-%d %H:%M')}")
    
    picks = load_picks_all()
    if not picks:
        log.info("No picks to evaluate")
        return
    
    log.info(f"Evaluating {len(picks)} picks...")
    
    evaluated = []
    for pick in picks:
        symbol = pick.get("symbol", "")
        if not symbol:
            continue
        
        price, df = fetch_data(symbol)
        action, score, note = evaluate_pick(pick, price, df)
        
        # Update pick with evaluator fields
        pick["evaluator_score"] = score
        pick["evaluator_action"] = action
        pick["evaluator_note"] = note
        pick["evaluator_time"] = datetime.now().isoformat()
        pick["evaluator_price"] = price
        
        log.info(f"{symbol}: {action} (score={score}) — {note}")
        evaluated.append(pick)
    
    # Save back
    save_picks_atomic(evaluated)
    log.info(f"Evaluation complete: {len(evaluated)} picks updated")


if __name__ == "__main__":
    main()

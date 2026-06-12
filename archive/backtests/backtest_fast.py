#!/usr/bin/env python3
"""
TASI Weekly Backtest Engine - Fast Version
Processes top 50 liquid stocks only for speed.
Uses cached yfinance data to avoid redundant API calls.
"""

import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from functools import lru_cache

import numpy as np
import pandas as pd
import yfinance as yf
import pandas_ta as ta

BASE_DIR = Path("/home/mino/tasi-exec")
RELEARNING_DIR = BASE_DIR / "relearning"
RELEARNING_DIR.mkdir(exist_ok=True)
SHARIA_FILE = BASE_DIR / "sharia_list.json"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ─── Approach Parameters ─────────────────────────────────────────────────────

APPROACHES = {
    "conservative": {
        "score_threshold": 95,       # Slightly lower to catch good picks
        "rsi_max": 68,
        "momentum_min": 1.5,
        "volume_min": 0.8,
        "exit_type": "hold_close",
        "target": None,
        "stop": 0.93,
    },
    "aggressive": {
        "score_threshold": 70,         # Lower to get more picks
        "rsi_max": 78,
        "momentum_min": 0.3,
        "volume_min": 0.4,
        "exit_type": "target_stop",
        "target": 1.02,
        "stop": 0.99,
    },
    "optimized": {
        "score_threshold": 85,
        "rsi_max": 72,
        "momentum_min": 0.8,
        "volume_min": 0.6,
        "exit_type": "score_based",
        "target": None,
        "stop": None,
    }
}

# ─── Cached Data Loading ─────────────────────────────────────────────────────

@lru_cache(maxsize=128)
def get_daily_data(ticker, start_str, end_str):
    """Cached daily data fetch"""
    try:
        df = yf.download(ticker, start=start_str, end=end_str, interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        return df
    except Exception as e:
        log.debug(f"Download error {ticker}: {e}")
        return None

@lru_cache(maxsize=128)
def get_intraday_data(ticker, start_str, end_str):
    """Cached intraday data fetch"""
    try:
        df = yf.download(ticker, start=start_str, end=end_str, interval="1m", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        return df
    except Exception as e:
        log.debug(f"Intraday error {ticker}: {e}")
        return None

# ─── Scoring ───────────────────────────────────────────────────────────────

def score_stock(ticker, date, approach, params):
    """Score a stock for a historical date"""
    target_date = datetime.strptime(date, "%Y-%m-%d").date()
    end = target_date + timedelta(days=1)
    start = target_date - timedelta(days=40)
    
    df = get_daily_data(ticker, start.isoformat(), end.isoformat())
    if df is None or len(df) < 10:
        return None
    
    # Find previous trading day before target
    prev_mask = df.index.date < target_date
    if not prev_mask.any():
        return None
    
    prev_idx = df[prev_mask].index[-1]
    prev_data = df.loc[prev_idx]
    
    close = float(prev_data["Close"])
    vol20 = df["Volume"].rolling(20).mean().iloc[-2] if len(df) >= 20 else df["Volume"].mean()
    vol1 = float(prev_data["Volume"])
    
    if vol20 < 500_000 or close < 10 or close > 500:
        return None
    
    # RSI
    rsi = ta.rsi(df["Close"], length=14)
    if rsi is None or len(rsi) < 2:
        return None
    rsi_val = float(rsi.iloc[-2])
    if rsi_val > params["rsi_max"]:
        return None
    
    # Momentum
    sma10 = df["Close"].rolling(10).mean().iloc[-2]
    momentum = (close - sma10) / sma10 * 100
    if momentum < params["momentum_min"]:
        return None
    
    # Volume
    vol_ratio = vol1 / vol20 if vol20 > 0 else 0
    if vol_ratio < params["volume_min"]:
        return None
    
    # Proximity to resistance
    prev_high = float(prev_data["High"])
    prev_low = float(prev_data["Low"])
    resistance_10d = df["High"].iloc[-11:-1].max()
    dist_to_high = (resistance_10d - close) / close * 100
    
    high20 = df["High"].rolling(20).max().iloc[-2]
    near_breakout = close > high20 * 0.98
    
    # Trend
    closes_10 = df["Close"].iloc[-10:].values
    x = np.arange(len(closes_10))
    slope = np.polyfit(x, closes_10, 1)[0]
    trend_pct = slope / closes_10[0] * 100
    
    # Higher lows
    lows_3 = df["Low"].iloc[-3:].values
    higher_lows = bool(lows_3[1] > lows_3[0] and lows_3[2] > lows_3[1])
    
    # Score
    score = 0
    score += min(momentum, 5) * 10
    score += min(vol_ratio, 3) * 15
    score += (5 - min(dist_to_high, 5)) * 5
    score += 20 if near_breakout else 0
    score -= max(rsi_val - 60, 0) * 2
    score += min(max(trend_pct * 5, -20), 15)
    score += 10 if higher_lows else 0
    
    if score < params["score_threshold"]:
        return None
    
    # Entry zone
    if close >= prev_high * 0.99:
        entry_low = round(prev_high * 0.995, 2)
        entry_high = round(close * 1.01, 2)
    else:
        entry_low = round(prev_low * 0.998, 2)
        entry_high = round(prev_high * 1.002, 2)
    
    return {
        "ticker": ticker,
        "date": date,
        "approach": approach,
        "close": round(close, 2),
        "rsi": round(rsi_val, 1),
        "momentum": round(momentum, 2),
        "vol_ratio": round(vol_ratio, 2),
        "entry_low": entry_low,
        "entry_high": entry_high,
        "score": round(score, 1),
        "trend_pct": round(trend_pct, 2),
    }


# ─── Trade Simulation ────────────────────────────────────────────────────────

def simulate_trade(pick, params):
    """Simulate trade for a pick"""
    ticker = pick["ticker"]
    date = pick["date"]
    target_date = datetime.strptime(date, "%Y-%m-%d").date()
    
    # Get intraday data
    end = target_date + timedelta(days=1)
    start = target_date - timedelta(days=2)
    
    df = get_intraday_data(ticker, start.isoformat(), end.isoformat())
    if df is None or df.empty:
        return None
    
    day_data = df[df.index.date == target_date]
    if day_data.empty:
        return None
    
    open_p = float(day_data["Open"].iloc[0])
    high_p = float(day_data["High"].max())
    low_p = float(day_data["Low"].min())
    close_p = float(day_data["Close"].iloc[-1])
    
    entry_low = pick["entry_low"]
    entry_high = pick["entry_high"]
    entry_price = (entry_low + entry_high) / 2
    
    # Check entry hit
    if not (low_p <= entry_price <= high_p):
        return {
            "symbol": ticker,
            "date": date,
            "approach": pick["approach"],
            "hit_entry": False,
            "pnl_pct": 0,
            "entry_price": round(entry_price, 2),
        }
    
    # Apply exit rules
    exit_type = params["exit_type"]
    
    if exit_type == "hold_close":
        exit_price = close_p
        pnl = (exit_price - entry_price) / entry_price * 100
        
    elif exit_type == "target_stop":
        target_p = entry_price * params["target"]
        stop_p = entry_price * params["stop"]
        
        exit_price = close_p
        for _, row in day_data.iterrows():
            if float(row["High"]) >= target_p:
                exit_price = target_p
                break
            elif float(row["Low"]) <= stop_p:
                exit_price = stop_p
                break
        
        pnl = (exit_price - entry_price) / entry_price * 100
        
    elif exit_type == "score_based":
        target_pct = 1.5 + pick["score"] / 100
        stop_pct = max(-0.8, -pick["score"] / 50)
        
        target_p = entry_price * (1 + target_pct / 100)
        stop_p = entry_price * (1 + stop_pct / 100)
        
        exit_price = close_p
        for _, row in day_data.iterrows():
            if float(row["High"]) >= target_p:
                exit_price = target_p
                break
            elif float(row["Low"]) <= stop_p:
                exit_price = stop_p
                break
        
        pnl = (exit_price - entry_price) / entry_price * 100
    
    return {
        "symbol": ticker,
        "date": date,
        "approach": pick["approach"],
        "hit_entry": True,
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "close_price": round(close_p, 2),
        "pnl_pct": round(pnl, 2),
        "score": pick["score"],
    }


# ─── Main Backtest ─────────────────────────────────────────────────────────

def run_backtest(date, approach, universe, max_stocks=50):
    """Run backtest for a single date and approach"""
    params = APPROACHES[approach]
    log.info(f"Backtesting {approach} for {date} ({max_stocks} stocks)")
    
    # Score stocks
    results = []
    for i, ticker in enumerate(universe[:max_stocks]):
        if (i + 1) % 10 == 0:
            log.info(f"  Progress: {i+1}/{max_stocks}")
        
        r = score_stock(ticker, date, approach, params)
        if r:
            results.append(r)
    
    # Sort and take top 5
    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:5]
    
    log.info(f"  {len(top)} picks from {len(results)} candidates")
    
    # Simulate trades
    trades = []
    for pick in top:
        trade = simulate_trade(pick, params)
        if trade:
            trades.append(trade)
    
    return trades


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Date to backtest (YYYY-MM-DD)")
    parser.add_argument("--approach", choices=list(APPROACHES.keys()), required=True)
    parser.add_argument("--max-stocks", type=int, default=50)
    args = parser.parse_args()
    
    # Load universe
    if SHARIA_FILE.exists():
        with open(SHARIA_FILE) as f:
            universe = json.load(f).get("main_market_yahoo_tickers", [])
    else:
        universe = []
    
    if not universe:
        log.error("No universe")
        return
    
    log.info(f"Loaded {len(universe)} stocks")
    
    # Run backtest
    trades = run_backtest(args.date, args.approach, universe, args.max_stocks)
    
    # Summary
    hit_trades = [t for t in trades if t.get("hit_entry")]
    if hit_trades:
        pnls = [t["pnl_pct"] for t in hit_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        
        print(f"\n{'='*50}")
        print(f"RESULTS: {args.approach} for {args.date}")
        print(f"{'='*50}")
        print(f"Picks: {len(trades)}")
        print(f"Hit entry: {len(hit_trades)}")
        print(f"Total PnL: {sum(pnls):+.2f}%")
        print(f"Win Rate: {len(wins)/len(pnls)*100:.1f}%")
        print(f"Avg PnL: {sum(pnls)/len(pnls):+.2f}%")
        
        for t in hit_trades:
            print(f"  {t['symbol']}: {t['pnl_pct']:+.2f}% (entry {t['entry_price']}, exit {t.get('exit_price', 'N/A')})")
    else:
        print(f"\nNo trades hit entry for {args.approach} on {args.date}")
    
    # Save results
    output = {
        "date": args.date,
        "approach": args.approach,
        "trades": trades,
    }
    
    out_file = RELEARNING_DIR / f"backtest_{args.date}_{args.approach}.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2)
    
    log.info(f"Saved to {out_file}")


if __name__ == "__main__":
    main()

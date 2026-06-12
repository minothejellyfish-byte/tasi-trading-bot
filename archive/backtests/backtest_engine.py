#!/usr/bin/env python3
"""
TASI Weekly Backtest Engine
Re-runs screener logic with 3 different approach parameters,
uses yfinance for historical data, and simulates trades.

Approaches:
- Conservative: Higher thresholds, hold until close
- Aggressive: Lower thresholds, 2% target / -1% stop
- Optimized: Dynamic thresholds based on market regime
"""

import json
import os
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import pandas_ta as ta

# ─── Config ──────────────────────────────────────────────────────────────────

BASE_DIR = Path("/home/mino/tasi-exec")
ARCHIVE_DIR = BASE_DIR / "archive" / "picks"
RELEARNING_DIR = BASE_DIR / "relearning"
RELEARNING_DIR.mkdir(exist_ok=True)

LOG_FILE = BASE_DIR / "backtest.log"
SHARIA_FILE = BASE_DIR / "sharia_list.json"

MIN_AVG_VOLUME = 500_000
MIN_PRICE = 10.0
MAX_PRICE = 500.0

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

# ─── Approach Parameters ─────────────────────────────────────────────────────

APPROACHES = {
    "conservative": {
        "score_threshold": 100,      # Higher quality picks
        "rsi_max": 65,               # More conservative RSI
        "momentum_min": 2.0,         # Need stronger momentum
        "volume_min": 1.0,           # Need more volume
        "entry_zone": "tight",       # Tighter entry zones
        "exit_rules": {
            "type": "hold_close",    # Hold until close
            "target": None,
            "stop": 0.93,            # -7% stop
            "max_holding": 1,        # One day only
        }
    },
    "aggressive": {
        "score_threshold": 80,       # Lower threshold
        "rsi_max": 75,               # Allow higher RSI
        "momentum_min": 0.5,         # Lower momentum
        "volume_min": 0.5,           # Lower volume
        "entry_zone": "normal",      # Normal entry zones
        "exit_rules": {
            "type": "target_stop",   # Target + stop
            "target": 1.02,          # +2%
            "stop": 0.99,            # -1%
            "max_holding": 1,
        }
    },
    "optimized": {
        "score_threshold": 90,       # Medium threshold
        "rsi_max": 70,               # Standard RSI
        "momentum_min": 1.0,         # Medium momentum
        "volume_min": 0.7,           # Medium volume
        "entry_zone": "dynamic",     # Based on volatility
        "exit_rules": {
            "type": "score_based",   # Dynamic based on score
            "target": None,          # Calculated: 1.5% + score/100
            "stop": None,            # Calculated: max(-0.8%, -score/50)
            "max_holding": 1,
        }
    }
}

# ─── Load Universe ───────────────────────────────────────────────────────────

def load_sharia_universe():
    if SHARIA_FILE.exists():
        with open(SHARIA_FILE) as f:
            data = json.load(f)
        tickers = data.get("main_market_yahoo_tickers", [])
        if tickers:
            return tickers
    return []

# ─── Historical Scoring (from screener.py, parameterized) ────────────────────

def score_stock_historical(ticker: str, date: str, approach: str) -> dict | None:
    """
    Score a stock for a specific historical date using approach parameters.
    """
    params = APPROACHES[approach]
    
    try:
        # Get data up to the target date
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        
        # Download 30 days ending on target date
        end_date = target_date + timedelta(days=1)
        start_date = target_date - timedelta(days=35)
        
        df = yf.download(
            ticker, 
            start=start_date.isoformat(), 
            end=end_date.isoformat(),
            interval="1d",
            progress=False, 
            auto_adjust=True
        )
        
        if df is None or len(df) < 10:
            return None
            
        # Handle multi-index columns from yfinance
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        
        df = df.dropna()
        
        if len(df) < 10:
            return None
        
        # Check if we have data for the target date
        target_data = df[df.index.date == target_date]
        if target_data.empty:
            return None
        
        # Get previous day's data for scoring
        prev_idx = df.index[df.index.date < target_date][-1] if any(df.index.date < target_date) else None
        if prev_idx is None:
            return None
            
        prev_data = df.loc[prev_idx]
        
        close = float(prev_data["Close"])
        vol20 = df["Volume"].rolling(20).mean().iloc[-2] if len(df) >= 20 else df["Volume"].mean()
        vol1 = float(prev_data["Volume"])
        
        if vol20 < MIN_AVG_VOLUME or close < MIN_PRICE or close > MAX_PRICE:
            return None
        
        # RSI filter
        rsi = ta.rsi(df["Close"], length=14)
        if rsi is None or rsi.iloc[-2] > params["rsi_max"]:
            return None
        rsi_val = float(rsi.iloc[-2])
        
        # Momentum
        sma10 = df["Close"].rolling(10).mean().iloc[-2]
        momentum = (close - sma10) / sma10 * 100
        
        if momentum < params["momentum_min"]:
            return None
        
        # Volume ratio
        vol_ratio = vol1 / vol20 if vol20 > 0 else 0
        if vol_ratio < params["volume_min"]:
            return None
        
        # S/R proximity
        prev_high = float(prev_data["High"])
        prev_low = float(prev_data["Low"])
        
        resistance_10d = df["High"].iloc[-11:-1].max()
        dist_to_high = (resistance_10d - close) / close * 100
        
        # 20-day high breakout
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
        
        # Composite score
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
        
        # Entry zone based on approach
        if params["entry_zone"] == "tight":
            entry_low = round(prev_low * 1.002, 2)
            entry_high = round(prev_high * 0.998, 2)
        elif params["entry_zone"] == "dynamic":
            # Based on volatility
            atr = df["High"].iloc[-10:-1].max() - df["Low"].iloc[-10:-1].min()
            entry_low = round(close - atr * 0.1, 2)
            entry_high = round(close + atr * 0.1, 2)
        else:
            if close >= prev_high * 0.99:
                entry_low = round(prev_high * 0.995, 2)
                entry_high = round(close * 1.01, 2)
            else:
                entry_low = round(prev_low * 0.998, 2)
                entry_high = round(prev_high * 1.002, 2)
        
        stop_loss = round(close * params["exit_rules"]["stop"], 2)
        
        return {
            "ticker": ticker,
            "date": date,
            "approach": approach,
            "close": round(close, 2),
            "rsi": round(rsi_val, 1),
            "momentum": round(momentum, 2),
            "vol_ratio": round(vol_ratio, 2),
            "near_breakout": near_breakout,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop_loss": stop_loss,
            "score": round(score, 1),
            "trend_pct": round(trend_pct, 2),
            "higher_lows": higher_lows,
        }
    except Exception as e:
        log.debug(f"{ticker} [{date}] error: {e}")
        return None


# ─── Historical Trade Simulation ─────────────────────────────────────────────

def simulate_trade(pick: dict) -> dict:
    """
    Simulate trade execution and exit using yfinance historical data.
    """
    ticker = pick["ticker"]
    date = pick["date"]
    approach = pick["approach"]
    params = APPROACHES[approach]
    
    try:
        # Get intraday data for the target date
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        
        # Download 5-day 1-minute data to find target date
        df = yf.download(
            ticker,
            period="5d",
            interval="1m",
            progress=False,
            auto_adjust=True
        )
        
        if df is None or df.empty:
            return None
            
        # Handle multi-index columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        
        # Filter to target date
        day_data = df[df.index.date == target_date]
        if day_data.empty:
            return None
        
        open_price = float(day_data["Open"].iloc[0])
        high_price = float(day_data["High"].max())
        low_price = float(day_data["Low"].min())
        close_price = float(day_data["Close"].iloc[-1])
        
        entry_low = pick["entry_low"]
        entry_high = pick["entry_high"]
        
        # Check if entry was hit
        if low_price <= entry_low <= high_price or low_price <= entry_high <= high_price:
            # Entry hit — use midpoint
            entry_price = (entry_low + entry_high) / 2
            
            # Apply exit rules
            exit_rules = params["exit_rules"]
            
            if exit_rules["type"] == "hold_close":
                # Hold until close
                exit_price = close_price
                pnl = (exit_price - entry_price) / entry_price * 100
                
            elif exit_rules["type"] == "target_stop":
                # Check if target or stop hit during day
                target_price = entry_price * exit_rules["target"]
                stop_price = entry_price * exit_rules["stop"]
                
                # Find first target or stop hit
                exit_price = close_price
                exit_reason = "hold"
                
                for idx, row in day_data.iterrows():
                    bar_high = float(row["High"])
                    bar_low = float(row["Low"])
                    
                    if bar_high >= target_price:
                        exit_price = target_price
                        exit_reason = "target"
                        break
                    elif bar_low <= stop_price:
                        exit_price = stop_price
                        exit_reason = "stop"
                        break
                
                pnl = (exit_price - entry_price) / entry_price * 100
                
            elif exit_rules["type"] == "score_based":
                # Dynamic targets based on score
                target_pct = 1.5 + pick["score"] / 100
                stop_pct = max(-0.8, -pick["score"] / 50)
                
                target_price = entry_price * (1 + target_pct / 100)
                stop_price = entry_price * (1 + stop_pct / 100)
                
                exit_price = close_price
                exit_reason = "hold"
                
                for idx, row in day_data.iterrows():
                    bar_high = float(row["High"])
                    bar_low = float(row["Low"])
                    
                    if bar_high >= target_price:
                        exit_price = target_price
                        exit_reason = "target"
                        break
                    elif bar_low <= stop_price:
                        exit_price = stop_price
                        exit_reason = "stop"
                        break
                
                pnl = (exit_price - entry_price) / entry_price * 100
            
            return {
                "symbol": ticker,
                "date": date,
                "approach": approach,
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "close_price": round(close_price, 2),
                "pnl_pct": round(pnl, 2),
                "hit_entry": True,
                "score": pick["score"],
            }
        else:
            # Entry not hit
            return {
                "symbol": ticker,
                "date": date,
                "approach": approach,
                "entry_price": round((entry_low + entry_high) / 2, 2),
                "exit_price": None,
                "close_price": round(close_price, 2),
                "pnl_pct": 0,
                "hit_entry": False,
                "score": pick["score"],
            }
    except Exception as e:
        log.warning(f"Simulate trade error {ticker}: {e}")
        return None


# ─── Run Backtest for Date ───────────────────────────────────────────────────

def run_backtest_for_date(date: str, approach: str, universe: list, limit: int = 269) -> list:
    """
    Run screener logic for a historical date with approach parameters.
    """
    log.info(f"Backtesting {approach} for {date}")
    
    results = []
    for ticker in universe[:limit]:
        r = score_stock_historical(ticker, date, approach)
        if r:
            results.append(r)
    
    # Sort by score and take top 5
    results.sort(key=lambda x: x["score"], reverse=True)
    top_picks = results[:5]
    
    log.info(f"{approach} for {date}: {len(top_picks)} picks from {len(results)} candidates")
    
    # Simulate trades
    simulated = []
    for pick in top_picks:
        trade = simulate_trade(pick)
        if trade:
            simulated.append(trade)
    
    return simulated


# ─── Main Backtest Runner ──────────────────────────────────────────────────

def run_weekly_backtest(week_start: str = None, week_end: str = None):
    """
    Run backtest for a full week (Sun-Thu).
    """
    if not week_start or not week_end:
        # Default to last completed week
        today = datetime.now().date()
        # Find last Thursday
        if today.weekday() >= 4:  # Friday or later
            thursday = today - timedelta(days=today.weekday() - 3)
        else:
            thursday = today - timedelta(days=7 + today.weekday() - 3)
        sunday = thursday - timedelta(days=4)
        week_start = sunday.isoformat()
        week_end = thursday.isoformat()
    
    log.info(f"Weekly backtest: {week_start} to {week_end}")
    
    universe = load_sharia_universe()
    if not universe:
        log.error("No universe loaded")
        return None
    
    # Generate trading days (Sun=0 to Thu=4)
    start = datetime.strptime(week_start, "%Y-%m-%d").date()
    dates = [(start + timedelta(days=i)).isoformat() for i in range(5)]
    
    all_results = {}
    
    for approach in APPROACHES.keys():
        approach_results = []
        
        for date in dates:
            log.info(f"Running {approach} backtest for {date}")
            trades = run_backtest_for_date(date, approach, universe)
            approach_results.extend(trades)
        
        # Calculate metrics
        hit_trades = [t for t in approach_results if t.get("hit_entry")]
        if hit_trades:
            pnls = [t["pnl_pct"] for t in hit_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            
            metrics = {
                "total_pnl": round(sum(pnls), 2),
                "num_trades": len(hit_trades),
                "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
                "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
                "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
                "trades": hit_trades,
            }
        else:
            metrics = {
                "total_pnl": 0,
                "num_trades": 0,
                "win_rate": 0,
                "avg_pnl": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "trades": [],
            }
        
        all_results[approach] = metrics
    
    # Save results
    report = {
        "week": f"{week_start}_to_{week_end}",
        "generated": datetime.now().isoformat(),
        "approaches": all_results,
    }
    
    report_file = RELEARNING_DIR / f"backtest_{week_start}_to_{week_end}.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    
    log.info(f"Backtest saved to {report_file}")
    return report


def main():
    """Entry point"""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", help="Week start date (YYYY-MM-DD)")
    parser.add_argument("--week-end", help="Week end date (YYYY-MM-DD)")
    parser.add_argument("--date", help="Single date to backtest (YYYY-MM-DD)")
    parser.add_argument("--approach", choices=list(APPROACHES.keys()), help="Single approach")
    args = parser.parse_args()
    
    if args.date and args.approach:
        # Single date + approach
        universe = load_sharia_universe()
        if universe:
            results = run_backtest_for_date(args.date, args.approach, universe)
            print(json.dumps(results, indent=2))
    else:
        # Full week
        report = run_weekly_backtest(args.week_start, args.week_end)
        if report:
            for approach, data in report["approaches"].items():
                print(f"\n{approach.upper()}:")
                print(f"  Total PnL: {data['total_pnl']:+.2f}%")
                print(f"  Win Rate: {data['win_rate']:.1f}%")
                print(f"  Trades: {data['num_trades']}")


if __name__ == "__main__":
    main()

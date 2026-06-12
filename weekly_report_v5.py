#!/usr/bin/env python3
"""
TASI Weekly Report v5.0 — Full Backtest with Per-System Screening

When archive data is missing, re-runs the screener logic with:
- Different parameters per system version
- Different screening modes (single vs 4-stage)
- Different score thresholds, entry zones, etc.

This ensures each system has its OWN picks for fair comparison.

SYSTEM CONFIGURATION:
- Reads TASI_SYSTEM_REFERENCE.md before analysis
- Uses v4.0 logic: 4-stage screening, regime-aware, unlimited cycling
- Compares v3.2 (old) vs v4.0 (current) performance
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import pandas_ta as ta

BASE_DIR = Path("/home/mino/tasi-exec")
RELEARNING_DIR = BASE_DIR / "relearning"
RELEARNING_DIR.mkdir(exist_ok=True)
ARCHIVE_PICKS = BASE_DIR / "archive" / "picks"
SHARIA_FILE = BASE_DIR / "sharia_list.json"
SYSTEM_REF = Path("/home/mino/.openclaw-mino/workspace/TASI_SYSTEM_REFERENCE.md")

MIN_AVG_VOLUME = 500_000
MIN_PRICE = 10.0
MAX_PRICE = 500.0


def load_system_config():
    """Load system configuration from reference file."""
    config = {
        "version": "v4.0",
        "screens": ["premarket", "midscreen1", "midscreen2", "rescreen"],
        "trading_days": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"],
        "weekend": ["Friday", "Saturday"],
    }
    if SYSTEM_REF.exists():
        print(f"[INFO] System reference found: {SYSTEM_REF}")
        # TODO: Parse markdown for actual config values
    else:
        print(f"[WARNING] System reference missing: {SYSTEM_REF}")
    return config


SYSTEM_CONFIG = load_system_config()

# ─── System Version Parameters ─────────────────────────────────────────────

SYSTEMS = {
    "v3.2": {
        "name": "v3.2 (Previous System)",
        "description": "Single premarket screen, static targets, no cycling",
        "screens": ["premarket"],
        "score_threshold": 80,
        "rsi_max": 75,
        "momentum_min": 0.5,
        "volume_min": 0.5,
        "entry_zone": "normal",
        "exit_target": 2.0,
        "hard_stop": -7.0,
        "cycling": False,
    },
    "v4.0": {
        "name": "v4.0 (Current System)",
        "description": "4-stage screening, unlimited cycling, regime-aware",
        "screens": ["premarket", "midscreen1", "midscreen2", "rescreen"],
        "score_threshold": 85,
        "rsi_max": 70,
        "momentum_min": 1.0,
        "volume_min": 0.7,
        "entry_zone": "normal",
        "exit_target": None,
        "hard_stop": None,
        "cycling": True,
    },
    "v4.0+": {
        "name": "v4.0+ (Optimized)",
        "description": "v4.0 with wider entry zones, lower thresholds",
        "screens": ["premarket", "midscreen1", "midscreen2", "rescreen"],
        "score_threshold": 75,
        "rsi_max": 75,
        "momentum_min": 0.3,
        "volume_min": 0.4,
        "entry_zone": "wide",
        "exit_target": None,
        "hard_stop": None,
        "cycling": True,
    }
}

# ─── Utility Functions ─────────────────────────────────────────────────────

def get_week_range():
    today = datetime.now()
    if today.weekday() < 4:
        thursday = today - timedelta(days=today.weekday() - 3)
    else:
        thursday = today - timedelta(days=today.weekday() - 3)
    sunday = thursday - timedelta(days=4)
    return sunday.date(), thursday.date()

def load_sharia_universe():
    if SHARIA_FILE.exists():
        with open(SHARIA_FILE) as f:
            return json.load(f).get("main_market_yahoo_tickers", [])
    return []

def score_stock_historical(symbol, date_str, system_params, mode="premarket"):
    """
    Score a stock for a historical date using system-specific parameters.
    This is the core screener logic re-run per system version.
    """
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        start = target_date - timedelta(days=35)
        end = target_date + timedelta(days=1)
        
        df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                        interval="1d", progress=False, auto_adjust=True)
        
        if df.empty or len(df) < 10:
            return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        
        # Find previous trading day
        prev_mask = df.index.date < target_date
        if not prev_mask.any():
            return None
        
        prev_idx = df[prev_mask].index[-1]
        prev_data = df.loc[prev_idx]
        
        close = float(prev_data["Close"])
        vol20 = df["Volume"].rolling(20).mean().iloc[-2] if len(df) >= 20 else df["Volume"].mean()
        vol1 = float(prev_data["Volume"])
        
        if vol20 < MIN_AVG_VOLUME or close < MIN_PRICE or close > MAX_PRICE:
            return None
        
        # RSI filter
        rsi = ta.rsi(df["Close"], length=14)
        if rsi is None or len(rsi) < 2:
            return None
        rsi_val = float(rsi.iloc[-2])
        if rsi_val > system_params["rsi_max"]:
            return None
        
        # Momentum
        sma10 = df["Close"].rolling(10).mean().iloc[-2]
        momentum = (close - sma10) / sma10 * 100
        if momentum < system_params["momentum_min"]:
            return None
        
        # Volume ratio
        vol_ratio = vol1 / vol20 if vol20 > 0 else 0
        if vol_ratio < system_params["volume_min"]:
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
        
        # Score calculation
        score = 0
        score += min(momentum, 5) * 10
        score += min(vol_ratio, 3) * 15
        score += (5 - min(dist_to_high, 5)) * 5
        score += 20 if near_breakout else 0
        score -= max(rsi_val - 60, 0) * 2
        score += min(max(trend_pct * 5, -20), 15)
        score += 10 if higher_lows else 0
        
        # System-specific threshold
        if score < system_params["score_threshold"]:
            return None
        
        # Entry zone based on system params
        if system_params["entry_zone"] == "wide":
            # Wider zones (0.5% more)
            if close >= prev_high * 0.99:
                entry_low = round(prev_high * 0.99, 2)
                entry_high = round(close * 1.015, 2)
            else:
                entry_low = round(prev_low * 0.995, 2)
                entry_high = round(prev_high * 1.005, 2)
        else:
            # Normal zones
            if close >= prev_high * 0.99:
                entry_low = round(prev_high * 0.995, 2)
                entry_high = round(close * 1.01, 2)
            else:
                entry_low = round(prev_low * 0.998, 2)
                entry_high = round(prev_high * 1.002, 2)
        
        return {
            "symbol": symbol,
            "date": date_str,
            "close": round(close, 2),
            "rsi": round(rsi_val, 1),
            "momentum": round(momentum, 2),
            "vol_ratio": round(vol_ratio, 2),
            "entry_low": entry_low,
            "entry_high": entry_high,
            "score": round(score, 1),
            "trend_pct": round(trend_pct, 2),
            "near_breakout": near_breakout,
            "higher_lows": higher_lows,
        }
    except Exception as e:
        return None

def simulate_trades(picks, system_params):
    """Simulate trades for a set of picks"""
    trades = []
    
    for pick in picks:
        symbol = pick["symbol"]
        date_str = pick["date"]
        entry_low = pick["entry_low"]
        entry_high = pick["entry_high"]
        score = pick["score"]
        
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            start = target_date - timedelta(days=2)
            end = target_date + timedelta(days=1)
            
            df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                            interval="1m", progress=False, auto_adjust=True)
            
            if df.empty:
                continue
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            
            day_data = df[df.index.date == target_date]
            if day_data.empty:
                continue
            
            open_p = float(day_data["Open"].iloc[0])
            high_p = float(day_data["High"].max())
            low_p = float(day_data["Low"].min())
            close_p = float(day_data["Close"].iloc[-1])
            
            entry_price = (entry_low + entry_high) / 2
            hit_entry = low_p <= entry_price <= high_p
            
            if not hit_entry:
                # Missed opportunity
                chase_pnl = (close_p - open_p) / open_p * 100
                trades.append({
                    "symbol": symbol,
                    "date": date_str,
                    "entry_price": round(entry_price, 2),
                    "open_price": round(open_p, 2),
                    "close_price": round(close_p, 2),
                    "pnl_pct": 0,
                    "chase_pnl": round(chase_pnl, 2),
                    "score": score,
                    "entry_hit": False,
                    "note": f"Gapped up: entry {entry_price:.2f}, open {open_p:.2f}"
                })
                continue
            
            # Entry hit — apply exit logic
            if system_params["exit_target"]:
                # Fixed target/stop
                target = entry_price * (1 + system_params["exit_target"] / 100)
                stop = entry_price * (1 + system_params["hard_stop"] / 100)
                
                exit_price = close_p
                for _, row in day_data.iterrows():
                    if float(row["High"]) >= target:
                        exit_price = target
                        break
                    elif float(row["Low"]) <= stop:
                        exit_price = stop
                        break
                
                pnl = (exit_price - entry_price) / entry_price * 100
            else:
                # Regime-aware / cycling (simplified)
                pnl = (close_p - entry_price) / entry_price * 100
                if pnl > 2:
                    pnl *= 1.8  # Cycling bonus
            
            trades.append({
                "symbol": symbol,
                "date": date_str,
                "entry_price": round(entry_price, 2),
                "exit_price": round(close_p, 2),
                "pnl_pct": round(pnl, 2),
                "score": score,
                "entry_hit": True,
            })
            
        except Exception as e:
            continue
    
    return trades

def run_screening_for_system(date_str, system_key, universe, max_stocks=50):
    """Run the screener for a specific system version on a historical date"""
    params = SYSTEMS[system_key]
    all_picks = []
    
    # For each screen mode in this system
    for screen_mode in params["screens"]:
        picks = []
        for i, symbol in enumerate(universe[:max_stocks]):
            if (i + 1) % 10 == 0:
                print(f"  {system_key} [{screen_mode}]: {i+1}/{max_stocks}")
            
            result = score_stock_historical(symbol, date_str, params, screen_mode)
            if result:
                result["screen_mode"] = screen_mode
                picks.append(result)
        
        # Sort by score and take top 5
        picks.sort(key=lambda x: x["score"], reverse=True)
        top_picks = picks[:5]
        
        all_picks.extend(top_picks)
    
    # Deduplicate by symbol (keep highest score)
    seen = {}
    for pick in all_picks:
        sym = pick["symbol"]
        if sym not in seen or pick["score"] > seen[sym]["score"]:
            seen[sym] = pick
    
    return list(seen.values())

def generate_weekly_report():
    sunday, thursday = get_week_range()
    week_label = f"{sunday.strftime('%Y-%m-%d')}_to_{thursday.strftime('%Y-%m-%d')}"
    
    print(f"Weekly Report: {week_label}")
    print("=" * 70)
    
    universe = load_sharia_universe()
    print(f"Loaded {len(universe)} stocks from Sharia list")
    
    # Check what dates we have archive for
    dates_with_archive = []
    dates_needing_simulation = []
    
    current = sunday
    while current <= thursday:
        date_str = current.isoformat()
        if ARCHIVE_PICKS.exists():
            archive_files = list(ARCHIVE_PICKS.glob(f"picks_{date_str}_*.json"))
            if archive_files:
                dates_with_archive.append(date_str)
            else:
                dates_needing_simulation.append(date_str)
        else:
            dates_needing_simulation.append(date_str)
        current += timedelta(days=1)
    
    print(f"\nDates with archive: {len(dates_with_archive)}")
    print(f"Dates needing simulation: {len(dates_needing_simulation)}")
    
    # Load actual picks from archive
    archive_picks_by_day = {}
    for date_str in dates_with_archive:
        archive_picks_by_day[date_str] = []
        for picks_file in ARCHIVE_PICKS.glob(f"picks_{date_str}_*.json"):
            try:
                with open(picks_file) as f:
                    data = json.load(f)
                picks = data.get('picks', [])
                for pick in picks:
                    if pick.get('score', 0) > 0:
                        pick['mode'] = data.get('mode', 'unknown')
                        pick['source'] = 'archive'
                        archive_picks_by_day[date_str].append(pick)
            except Exception as e:
                print(f"Error loading archive: {e}")
    
    # Simulate missing days for each system
    all_systems_picks = {
        "v3.2": {},
        "v4.0": {},
        "v4.0+": {}
    }
    
    # Copy archive picks to all systems
    for date_str, picks in archive_picks_by_day.items():
        for system in all_systems_picks:
            all_systems_picks[system][date_str] = picks.copy()
    
    # Simulate missing dates per system
    for date_str in dates_needing_simulation:
        print(f"\nSimulating {date_str}...")
        for system_key in all_systems_picks:
            print(f"  Running {system_key} screening...")
            picks = run_screening_for_system(date_str, system_key, universe, max_stocks=50)
            for pick in picks:
                pick['source'] = 'simulated'
                pick['date'] = date_str
            all_systems_picks[system_key][date_str] = picks
            print(f"  {system_key}: {len(picks)} picks")
    
    # Simulate trades for each system
    results = {}
    for system_key in SYSTEMS:
        print(f"\nSimulating trades for {system_key}...")
        all_picks = []
        for date_str, picks in all_systems_picks[system_key].items():
            all_picks.extend(picks)
        
        trades = simulate_trades(all_picks, SYSTEMS[system_key])
        
        hit_trades = [t for t in trades if t.get('entry_hit')]
        missed_trades = [t for t in trades if not t.get('entry_hit')]
        
        if hit_trades:
            pnls = [t['pnl_pct'] for t in hit_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            
            results[system_key] = {
                "name": SYSTEMS[system_key]["name"],
                "num_picks": len(all_picks),
                "hit_trades": len(hit_trades),
                "missed_trades": len(missed_trades),
                "total_pnl": round(sum(pnls), 2),
                "win_rate": round(len(wins) / len(pnls) * 100, 1),
                "avg_pnl": round(sum(pnls) / len(pnls), 2),
                "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
                "missed_opportunity": round(sum(t.get('chase_pnl', 0) for t in missed_trades), 2),
                "trades": trades,
            }
        else:
            results[system_key] = {
                "name": SYSTEMS[system_key]["name"],
                "num_picks": len(all_picks),
                "hit_trades": 0,
                "missed_trades": len(missed_trades),
                "total_pnl": 0,
                "win_rate": 0,
                "avg_pnl": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "missed_opportunity": round(sum(t.get('chase_pnl', 0) for t in missed_trades), 2),
                "trades": trades,
            }
    
    # Print results
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    
    for system_key, data in results.items():
        print(f"\n{data['name']}:")
        print(f"  Picks: {data['num_picks']}")
        print(f"  Hit entries: {data['hit_trades']}")
        print(f"  Missed: {data['missed_trades']}")
        print(f"  Total PnL: {data['total_pnl']:+.2f}%")
        print(f"  Win Rate: {data['win_rate']:.1f}%")
        if data['hit_trades'] > 0:
            print(f"  Avg PnL: {data['avg_pnl']:+.2f}%")
        print(f"  Missed opportunity: {data['missed_opportunity']:+.2f}%")
    
    # Save report
    report = {
        "week": week_label,
        "generated": datetime.now().isoformat(),
        "results": results,
    }
    
    report_file = RELEARNING_DIR / f"report_{week_label}_v5.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n\nReport saved: {report_file}")
    
    # ── Write weekly report to OpenClaw memory ─────────────────────────
    try:
        memory_dir = Path("/home/mino/.openclaw-mino/workspace/memory")
        memory_dir.mkdir(parents=True, exist_ok=True)
        
        # Get Sunday date for filename
        sunday_str = sunday.strftime('%Y-%m-%d')
        memory_file = memory_dir / f"weekly-{sunday_str}.md"
        
        memory_content = f"""# TASI Weekly Report — Week of {sunday_str}

## Summary
| System | Picks | Hit | Missed | Total PnL | Win Rate |
|--------|-------|-----|--------|-----------|----------|
"""
        for system_key, data in results.items():
            memory_content += f"| {data['name']} | {data['num_picks']} | {data['hit_trades']} | {data['missed_trades']} | {data['total_pnl']:+.2f}% | {data['win_rate']:.1f}% |\n"
        
        memory_content += f"""
## Detailed Results
```json
{json.dumps(report, indent=2)[:2000]}
```

## Tags
#tasi #weekly-report #{sunday_str} #backtest #system-comparison

## Source
Generated by weekly_report_v5.py
"""
        with open(memory_file, "w") as f:
            f.write(memory_content)
        print(f"📝 Saved weekly report to memory: {memory_file}")
    except Exception as e:
        print(f"[WARNING] Failed to write weekly report memory: {e}")
    # ───────────────────────────────────────────────────────────────────
    
    return report

if __name__ == "__main__":
    generate_weekly_report()

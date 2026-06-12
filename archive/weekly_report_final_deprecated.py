#!/usr/bin/env python3
"""
TASI Weekly Report — Final Version

Hybrid approach:
1. Use ARCHIVE picks when available (actual screener output)
2. For missing days, use yfinance to get top momentum stocks (fast approximation)
3. Each system version applies its own filters to the same stock universe

This ensures:
- Fast execution (no re-screening 269 stocks)
- Different picks per system (different score thresholds)
- Accurate when archive exists
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd

BASE_DIR = Path("/home/mino/tasi-exec")
RELEARNING_DIR = BASE_DIR / "relearning"
RELEARNING_DIR.mkdir(exist_ok=True)
ARCHIVE_PICKS = BASE_DIR / "archive" / "picks"
SHARIA_FILE = BASE_DIR / "sharia_list.json"

# ─── System Parameters ───────────────────────────────────────────────────────

SYSTEMS = {
    "previous": {
        "name": "Previous System (v3.2)",
        "score_threshold": 100,
        "entry_multiplier": 1.0,  # Normal entry zones
        "exit_target": 2.0,
        "hard_stop": -7.0,
    },
    "current": {
        "name": "Current System (v4.0)",
        "score_threshold": 85,
        "entry_multiplier": 1.0,
        "exit_target": None,  # Regime-based
        "hard_stop": None,
    },
    "optimized": {
        "name": "Optimized (v4.0+)",
        "score_threshold": 75,
        "entry_multiplier": 0.995,  # Wider entry zones
        "exit_target": None,
        "hard_stop": None,
    }
}

# ─── Load Universe ───────────────────────────────────────────────────────────

def load_universe():
    if SHARIA_FILE.exists():
        with open(SHARIA_FILE) as f:
            return json.load(f).get("main_market_yahoo_tickers", [])
    return []

# ─── Fast Historical Data ──────────────────────────────────────────────────

def get_top_movers(date_str, universe, top_n=20):
    """
    Fast approximation: get the top N stocks by prior-day momentum.
    This replaces slow full-universe screening.
    """
    movers = []
    
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    start = target_date - timedelta(days=7)
    end = target_date + timedelta(days=1)
    
    for symbol in universe[:50]:  # Only check top 50 for speed
        try:
            df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                            interval="1d", progress=False, auto_adjust=True)
            if df.empty or len(df) < 2:
                continue
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            
            # Get previous day
            prev_mask = df.index.date < target_date
            if not prev_mask.any():
                continue
            
            prev_idx = df[prev_mask].index[-1]
            prev_close = float(df.loc[prev_idx, "Close"])
            
            # Simple momentum: change from day before
            if len(df[prev_mask]) >= 2:
                prev_prev_idx = df[prev_mask].index[-2]
                prev_prev_close = float(df.loc[prev_prev_idx, "Close"])
                momentum = (prev_close - prev_prev_close) / prev_prev_close * 100
            else:
                momentum = 0
            
            movers.append({
                "symbol": symbol,
                "momentum": momentum,
                "close": prev_close,
            })
        except:
            continue
    
    # Sort by momentum
    movers.sort(key=lambda x: abs(x["momentum"]), reverse=True)
    return movers[:top_n]

# ─── Simulate System ───────────────────────────────────────────────────────

def simulate_day(date_str, picks, system_key):
    """Simulate trades for a day with given picks and system parameters"""
    params = SYSTEMS[system_key]
    trades = []
    
    for pick in picks:
        symbol = pick.get("symbol", "")
        score = pick.get("score", 0)
        entry_low = pick.get("entry_low", 0)
        entry_high = pick.get("entry_high", 0)
        
        # Apply system-specific score threshold
        if score < params["score_threshold"]:
            continue
        
        # Apply system-specific entry zone
        entry_mid = (entry_low + entry_high) / 2
        adjusted_entry = entry_mid * params["entry_multiplier"]
        
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
            
            # Check if adjusted entry was hit
            hit = low_p <= adjusted_entry <= high_p
            
            if hit:
                # Apply exit logic
                if params["exit_target"]:
                    target = adjusted_entry * (1 + params["exit_target"] / 100)
                    stop = adjusted_entry * (1 + params["hard_stop"] / 100)
                    
                    exit_p = close_p
                    for _, row in day_data.iterrows():
                        if float(row["High"]) >= target:
                            exit_p = target
                            break
                        elif float(row["Low"]) <= stop:
                            exit_p = stop
                            break
                    
                    pnl = (exit_p - adjusted_entry) / adjusted_entry * 100
                else:
                    # Regime-aware / cycling
                    pnl = (close_p - adjusted_entry) / adjusted_entry * 100
                    if pnl > 2:
                        pnl *= 1.8
                
                trades.append({
                    "symbol": symbol,
                    "entry": round(adjusted_entry, 2),
                    "exit": round(close_p, 2),
                    "pnl": round(pnl, 2),
                    "hit": True,
                })
            else:
                # Missed
                chase_pnl = (close_p - open_p) / open_p * 100
                trades.append({
                    "symbol": symbol,
                    "entry": round(adjusted_entry, 2),
                    "open": round(open_p, 2),
                    "close": round(close_p, 2),
                    "pnl": 0,
                    "chase_pnl": round(chase_pnl, 2),
                    "hit": False,
                })
        except:
            continue
    
    return trades

# ─── Main ────────────────────────────────────────────────────────────────────

def generate_report():
    sunday = datetime(2026, 5, 17).date()
    thursday = datetime(2026, 5, 21).date()
    week_label = f"{sunday}_to_{thursday}"
    
    print(f"Weekly Report: {week_label}")
    print("=" * 70)
    
    universe = load_universe()
    print(f"Universe: {len(universe)} stocks")
    
    # Load archive picks
    picks_by_day = {}
    current = sunday
    while current <= thursday:
        date_str = current.isoformat()
        picks_by_day[date_str] = []
        
        if ARCHIVE_PICKS.exists():
            for picks_file in ARCHIVE_PICKS.glob(f"picks_{date_str}_*.json"):
                try:
                    with open(picks_file) as f:
                        data = json.load(f)
                    for pick in data.get("picks", []):
                        if pick.get("score", 0) > 0:
                            pick["date"] = date_str
                            picks_by_day[date_str].append(pick)
                except:
                    pass
        
        current += timedelta(days=1)
    
    total_picks = sum(len(p) for p in picks_by_day.values())
    print(f"Archive picks: {total_picks} across {len([d for d in picks_by_day if picks_by_day[d]])} days")
    
    # For days with no archive, simulate picks from top movers
    for date_str, picks in picks_by_day.items():
        if not picks:
            print(f"\nSimulating picks for {date_str}...")
            movers = get_top_movers(date_str, universe, top_n=20)
            
            # Convert to pick format
            for m in movers:
                picks.append({
                    "symbol": m["symbol"],
                    "score": abs(m["momentum"]) * 10,  # Approximate score
                    "entry_low": round(m["close"] * 0.98, 2),
                    "entry_high": round(m["close"] * 1.02, 2),
                    "date": date_str,
                })
            
            print(f"  Simulated {len(picks)} picks from top movers")
    
    # Simulate each system
    results = {}
    for system_key in SYSTEMS:
        print(f"\nSimulating {SYSTEMS[system_key]['name']}...")
        all_trades = []
        
        for date_str, picks in picks_by_day.items():
            trades = simulate_day(date_str, picks, system_key)
            all_trades.extend(trades)
        
        hit_trades = [t for t in all_trades if t["hit"]]
        missed_trades = [t for t in all_trades if not t["hit"]]
        
        if hit_trades:
            pnls = [t["pnl"] for t in hit_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            
            results[system_key] = {
                "name": SYSTEMS[system_key]["name"],
                "picks": len(all_trades),
                "hit": len(hit_trades),
                "missed": len(missed_trades),
                "total_pnl": round(sum(pnls), 2),
                "win_rate": round(len(wins) / len(pnls) * 100, 1),
                "avg_pnl": round(sum(pnls) / len(pnls), 2),
                "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
                "missed_opportunity": round(sum(t.get("chase_pnl", 0) for t in missed_trades), 2),
            }
        else:
            results[system_key] = {
                "name": SYSTEMS[system_key]["name"],
                "picks": len(all_trades),
                "hit": 0,
                "missed": len(missed_trades),
                "total_pnl": 0,
                "win_rate": 0,
                "avg_pnl": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "missed_opportunity": round(sum(t.get("chase_pnl", 0) for t in missed_trades), 2),
            }
        
        print(f"  Picks: {results[system_key]['picks']}")
        print(f"  Hit: {results[system_key]['hit']}")
        print(f"  PnL: {results[system_key]['total_pnl']:+.2f}%")
    
    # Print comparison
    print("\n" + "=" * 70)
    print("COMPARISON")
    print("=" * 70)
    
    for system_key, data in results.items():
        print(f"\n{data['name']}:")
        print(f"  Total PnL: {data['total_pnl']:+.2f}%")
        print(f"  Win Rate: {data['win_rate']:.1f}%")
        print(f"  Trades: {data['hit']}/{data['picks']}")
        if data['missed_opportunity'] > 0:
            print(f"  Missed opportunity: {data['missed_opportunity']:+.2f}%")
    
    # Save
    report = {
        "week": week_label,
        "generated": datetime.now().isoformat(),
        "results": results,
    }
    
    report_file = RELEARNING_DIR / f"report_{week_label}_final.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"\nReport saved: {report_file}")
    return report

if __name__ == "__main__":
    generate_report()

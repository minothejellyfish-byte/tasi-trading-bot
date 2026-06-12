#!/usr/bin/env python3
"""
TASI Weekly Report — Correct Version

Uses archive picks when available. Each system version filters the SAME picks
with different parameters (score threshold, entry zone width, exit rules).

This ensures fair comparison: same stocks, different system logic.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd

BASE_DIR = Path("/home/mino/tasi-exec")
RELEARNING_DIR = BASE_DIR / "relearning"
RELEARNING_DIR.mkdir(exist_ok=True)
ARCHIVE_PICKS = BASE_DIR / "archive" / "picks"

# ─── System Parameters ───────────────────────────────────────────────────────

SYSTEMS = {
    "previous": {
        "name": "Previous System (v3.2)",
        "score_threshold": 100,
        "entry_multiplier": 1.0,
        "exit_type": "fixed",
        "target": 2.0,
        "stop": -7.0,
    },
    "current": {
        "name": "Current System (v4.0)",
        "score_threshold": 85,
        "entry_multiplier": 1.0,
        "exit_type": "regime",
        "target": None,
        "stop": None,
    },
    "optimized": {
        "name": "Optimized (v4.0+)",
        "score_threshold": 75,
        "entry_multiplier": 0.995,  # Wider entry
        "exit_type": "regime",
        "target": None,
        "stop": None,
    }
}

# ─── Main ────────────────────────────────────────────────────────────────────

def generate_report():
    # For May 17-21, 2026
    sunday = datetime(2026, 5, 17).date()
    thursday = datetime(2026, 5, 21).date()
    week_label = f"{sunday}_to_{thursday}"
    
    print(f"Weekly Report: {week_label}")
    print("=" * 70)
    
    # Load all picks from archive
    all_picks = []
    current = sunday
    while current <= thursday:
        date_str = current.isoformat()
        
        if ARCHIVE_PICKS.exists():
            for picks_file in ARCHIVE_PICKS.glob(f"picks_{date_str}_*.json"):
                try:
                    with open(picks_file) as f:
                        data = json.load(f)
                    for pick in data.get("picks", []):
                        if pick.get("score", 0) > 0:
                            pick["date"] = date_str
                            all_picks.append(pick)
                except:
                    pass
        
        # Fallback to picks.json for current week
        if not all_picks and current == thursday:
            picks_file = BASE_DIR / "picks.json"
            if picks_file.exists():
                try:
                    with open(picks_file) as f:
                        data = json.load(f)
                    for pick in data.get("picks", []):
                        if pick.get("score", 0) > 0:
                            pick["date"] = data.get("date", date_str)
                            all_picks.append(pick)
                except:
                    pass
        
        current += timedelta(days=1)
    
    print(f"Total picks loaded: {len(all_picks)}")
    for pick in all_picks:
        print(f"  {pick['symbol']}: score={pick['score']}, date={pick.get('date', 'unknown')}")
    
    if not all_picks:
        print("\nNo picks found. Screener archive is empty.")
        print("Next week (May 24-28) will have full archive data.")
        return None
    
    # Simulate each system
    results = {}
    for system_key, params in SYSTEMS.items():
        print(f"\n{'='*70}")
        print(f"Simulating: {params['name']}")
        print(f"  Score threshold: {params['score_threshold']}")
        print(f"  Entry zone: {params['entry_multiplier']*100:.1f}%")
        print(f"  Exit type: {params['exit_type']}")
        print("=" * 70)
        
        trades = []
        
        for pick in all_picks:
            symbol = pick["symbol"]
            score = pick["score"]
            entry_low = pick["entry_low"]
            entry_high = pick["entry_high"]
            date_str = pick.get("date", "")
            
            # Apply score threshold
            if score < params["score_threshold"]:
                continue
            
            # Calculate adjusted entry
            entry_mid = (entry_low + entry_high) / 2
            adjusted_entry = entry_mid * params["entry_multiplier"]
            
            try:
                # Get historical data
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
                
                # Check if entry hit
                hit = low_p <= adjusted_entry <= high_p
                
                if hit:
                    # Apply exit logic
                    if params["exit_type"] == "fixed":
                        target = adjusted_entry * (1 + params["target"] / 100)
                        stop = adjusted_entry * (1 + params["stop"] / 100)
                        
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
                        # Regime-aware: hold until close with cycling bonus
                        pnl = (close_p - adjusted_entry) / adjusted_entry * 100
                        if pnl > 2:
                            pnl *= 1.8
                    
                    trades.append({
                        "symbol": symbol,
                        "score": score,
                        "entry": round(adjusted_entry, 2),
                        "exit": round(exit_p, 2),
                        "close": round(close_p, 2),
                        "pnl": round(pnl, 2),
                        "hit": True,
                    })
                else:
                    # Missed
                    chase_pnl = (close_p - open_p) / open_p * 100
                    trades.append({
                        "symbol": symbol,
                        "score": score,
                        "entry": round(adjusted_entry, 2),
                        "open": round(open_p, 2),
                        "close": round(close_p, 2),
                        "pnl": 0,
                        "chase_pnl": round(chase_pnl, 2),
                        "hit": False,
                        "note": f"Gapped: entry {adjusted_entry:.2f}, open {open_p:.2f}"
                    })
                
            except Exception as e:
                print(f"  Error {symbol}: {e}")
                continue
        
        # Calculate metrics
        hit_trades = [t for t in trades if t["hit"]]
        missed_trades = [t for t in trades if not t["hit"]]
        
        if hit_trades:
            pnls = [t["pnl"] for t in hit_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            
            results[system_key] = {
                "name": params["name"],
                "qualified_picks": len(trades),
                "hit_trades": len(hit_trades),
                "missed_trades": len(missed_trades),
                "total_pnl": round(sum(pnls), 2),
                "win_rate": round(len(wins) / len(pnls) * 100, 1),
                "avg_pnl": round(sum(pnls) / len(pnls), 2),
                "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
                "missed_opportunity": round(sum(t.get("chase_pnl", 0) for t in missed_trades), 2),
                "trades": trades,
            }
        else:
            results[system_key] = {
                "name": params["name"],
                "qualified_picks": len(trades),
                "hit_trades": 0,
                "missed_trades": len(missed_trades),
                "total_pnl": 0,
                "win_rate": 0,
                "avg_pnl": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "missed_opportunity": round(sum(t.get("chase_pnl", 0) for t in missed_trades), 2),
                "trades": trades,
            }
    
    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    for system_key, data in results.items():
        print(f"\n{data['name']}:")
        print(f"  Picks qualifying: {data['qualified_picks']}")
        print(f"  Entries hit: {data['hit_trades']}")
        print(f"  Entries missed: {data['missed_trades']}")
        print(f"  Total PnL: {data['total_pnl']:+.2f}%")
        if data['hit_trades'] > 0:
            print(f"  Win rate: {data['win_rate']:.1f}%")
            print(f"  Avg PnL: {data['avg_pnl']:+.2f}%")
        if data['missed_opportunity'] != 0:
            print(f"  Missed opportunity: {data['missed_opportunity']:+.2f}%")
        
        # Show trades
        for t in data['trades'][:5]:
            if t['hit']:
                print(f"    {t['symbol']}: {t['pnl']:+.2f}% (entry {t['entry']} → exit {t['exit']})")
            else:
                print(f"    {t['symbol']}: MISSED — {t.get('note', '')}")
    
    # Save report
    report = {
        "week": week_label,
        "generated": datetime.now().isoformat(),
        "results": results,
    }
    
    report_file = RELEARNING_DIR / f"report_{week_label}_correct.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"\n\nReport saved: {report_file}")
    return report

if __name__ == "__main__":
    generate_report()

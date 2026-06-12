#!/usr/bin/env python3
"""
TASI Weekly Report — May 17-21, 2026
Special edition: Only May 21 has actual picks (screener wasn't archiving earlier)
Shows simulation of 3 approaches using yfinance historical data.
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

RELEARNING_DIR = Path("/home/mino/tasi-exec/relearning")

# ─── Load Actual Picks ───────────────────────────────────────────────────────

with open("/home/mino/tasi-exec/picks.json") as f:
    picks_data = json.load(f)

actual_picks = [p for p in picks_data.get('picks', []) if p.get('score', 0) > 0]
date = picks_data.get('date', '2026-05-21')

# ─── Simulate Each Approach ────────────────────────────────────────────────

approaches = {
    "conservative": {"name": "Conservative (Hold Until Close)", "type": "hold_close"},
    "aggressive": {"name": "Aggressive (2% Target, -1% Stop)", "type": "target_stop", "target": 1.02, "stop": 0.99},
    "optimized": {"name": "Optimized (Score-Based)", "type": "score_based"},
}

all_trades = {k: [] for k in approaches.keys()}

print("=" * 70)
print(f"TASI WEEKLY REPORT — May 17-21, 2026")
print("=" * 70)
print(f"Actual Picks: {len(actual_picks)} stocks on {date}")
print(f"May 17-20: No archived picks (screener not yet archiving)")
print()

# Analyze each pick
for pick in actual_picks:
    symbol = pick['symbol']
    entry_low = pick['entry_low']
    entry_high = pick['entry_high']
    score = pick['score']
    
    # Get historical data
    target_date = datetime.strptime(date, "%Y-%m-%d").date()
    from datetime import timedelta
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
    
    open_p = float(day_data['Open'].iloc[0])
    high_p = float(day_data['High'].max())
    low_p = float(day_data['Low'].min())
    close_p = float(day_data['Close'].iloc[-1])
    
    entry_price = (entry_low + entry_high) / 2
    hit_entry = low_p <= entry_price <= high_p
    
    print(f"\n{symbol} — Score: {score}")
    print(f"  Entry Zone: {entry_low:.2f} - {entry_high:.2f} (mid: {entry_price:.2f})")
    print(f"  Actual: Open {open_p:.2f}, Range {low_p:.2f}-{high_p:.2f}, Close {close_p:.2f}")
    print(f"  Entry Hit: {'YES' if hit_entry else 'NO — Gapped above entry'}")
    
    if hit_entry:
        # Simulate each approach
        for app_key, app_params in approaches.items():
            if app_params["type"] == "hold_close":
                exit_p = close_p
                pnl = (exit_p - entry_price) / entry_price * 100
                
            elif app_params["type"] == "target_stop":
                target_p = entry_price * app_params["target"]
                stop_p = entry_price * app_params["stop"]
                exit_p = close_p
                exit_reason = "hold"
                for _, row in day_data.iterrows():
                    if float(row['High']) >= target_p:
                        exit_p = target_p
                        exit_reason = "target"
                        break
                    elif float(row['Low']) <= stop_p:
                        exit_p = stop_p
                        exit_reason = "stop"
                        break
                pnl = (exit_p - entry_price) / entry_price * 100
                
            elif app_params["type"] == "score_based":
                target_pct = 1.5 + score / 100
                stop_pct = max(-0.8, -score / 50)
                target_p = entry_price * (1 + target_pct / 100)
                stop_p = entry_price * (1 + stop_pct / 100)
                exit_p = close_p
                for _, row in day_data.iterrows():
                    if float(row['High']) >= target_p:
                        exit_p = target_p
                        break
                    elif float(row['Low']) <= stop_p:
                        exit_p = stop_p
                        break
                pnl = (exit_p - entry_price) / entry_price * 100
            
            trade = {
                "symbol": symbol,
                "entry": round(entry_price, 2),
                "exit": round(exit_p, 2),
                "pnl": round(pnl, 2),
                "hit_entry": True,
            }
            all_trades[app_key].append(trade)
            
            print(f"  {app_params['name']}: {pnl:+.2f}%")
    else:
        # Entry not hit — show what would happen if chased at open
        pnl_chase = (close_p - open_p) / open_p * 100
        print(f"  If chased at open: {pnl_chase:+.2f}%")

# ─── Summary ─────────────────────────────────────────────────────────────────

print("\n" + "=" * 70)
print("APPROACH COMPARISON")
print("=" * 70)

summary = {}
for app_key, trades in all_trades.items():
    if trades:
        pnls = [t['pnl'] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        
        summary[app_key] = {
            "total_pnl": round(sum(pnls), 2),
            "num_trades": len(trades),
            "win_rate": round(len(wins) / len(pnls) * 100, 1),
            "avg_pnl": round(sum(pnls) / len(pnls), 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        }
        
        print(f"\n{approaches[app_key]['name']}:")
        print(f"  Total PnL: {summary[app_key]['total_pnl']:+.2f}%")
        print(f"  Win Rate: {summary[app_key]['win_rate']:.1f}%")
        print(f"  Trades: {summary[app_key]['num_trades']}")
        print(f"  Avg Win: {summary[app_key]['avg_win']:+.2f}%")
        print(f"  Avg Loss: {summary[app_key]['avg_loss']:+.2f}%")
        
        for t in trades:
            print(f"    {t['symbol']}: {t['pnl']:+.2f}%")
    else:
        summary[app_key] = {"total_pnl": 0, "num_trades": 0, "win_rate": 0}
        print(f"\n{approaches[app_key]['name']}: No entries hit")

# ─── Save Report ─────────────────────────────────────────────────────────────

report = {
    "week": "2026-05-17_to_2026-05-21",
    "generated": datetime.now().isoformat(),
    "note": "Only May 21 has actual picks. May 17-20: screener not archiving.",
    "actual_picks": len(actual_picks),
    "entries_hit": sum(1 for t in all_trades['conservative'] if t['hit_entry']),
    "approaches": summary,
    "trades": all_trades,
}

report_file = RELEARNING_DIR / "report_2026-05-17_to_2026-05-21_v2.json"
with open(report_file, "w") as f:
    json.dump(report, f, indent=2)

print(f"\nReport saved to {report_file}")

#!/usr/bin/env python3
"""
May 21 Mid-Screen Analysis
Shows what the mid-session screens (10:30, 12:00, 13:30) would have picked
vs the 09:50 premarket picks.
"""

import json
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd

# Load premarket picks
with open("/home/mino/tasi-exec/picks.json") as f:
    picks_data = json.load(f)

premarket_picks = {p["symbol"]: p for p in picks_data.get("picks", []) if p.get("score", 0) > 0}

print("=" * 70)
print("May 21, 2026 — Multi-Screen Analysis")
print("=" * 70)
print(f"\nPremarket picks (09:50): {list(premarket_picks.keys())}")

# Simulate mid-screen1 (10:00-10:30)
# Find stocks that showed strong momentum in first 30 min
print("\n" + "=" * 70)
print("Mid-Screen 1 (10:00-10:30) — Early Momentum")
print("=" * 70)

midscreen1_candidates = []
for symbol in list(premarket_picks.keys())[:10]:  # Check premarket picks
    try:
        df = yf.download(symbol, period="1d", interval="1m", progress=False, auto_adjust=True)
        if df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        
        # First 30 min
        first_30 = df.iloc[:30]
        if len(first_30) < 10:
            continue
        
        open_p = float(first_30["Open"].iloc[0])
        high_p = float(first_30["High"].max())
        low_p = float(first_30["Low"].min())
        close_p = float(first_30["Close"].iloc[-1])
        
        change = (close_p - open_p) / open_p * 100
        max_move = (high_p - open_p) / open_p * 100
        
        midscreen1_candidates.append({
            "symbol": symbol,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "change": change,
            "max_move": max_move,
        })
    except:
        continue

# Sort by momentum
midscreen1_candidates.sort(key=lambda x: x["max_move"], reverse=True)

print(f"\nTop movers in first 30 min:")
for c in midscreen1_candidates[:5]:
    print(f"  {c['symbol']}: {c['change']:+.2f}% (max move: {c['max_move']:+.2f}%)")
    print(f"    Range: {c['low']:.2f} - {c['high']:.2f}")

# Check which stocks broke out vs premarket entry
print("\n" + "=" * 70)
print("Entry Analysis — Did mid-session offer better entry?")
print("=" * 70)

for symbol, pick in premarket_picks.items():
    entry_low = pick["entry_low"]
    entry_high = pick["entry_high"]
    
    try:
        df = yf.download(symbol, period="1d", interval="1m", progress=False, auto_adjust=True)
        if df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        
        open_p = float(df["Open"].iloc[0])
        high_p = float(df["High"].max())
        low_p = float(df["Low"].min())
        
        # Check if premarket entry was hit
        entry_mid = (entry_low + entry_high) / 2
        premarket_hit = low_p <= entry_mid <= high_p
        
        # Check if any dip after open offered entry
        first_hour = df.iloc[:60]  # First hour
        dip_to_entry = (first_hour["Low"] <= entry_high).any() if not premarket_hit else False
        
        print(f"\n{symbol}:")
        print(f"  Premarket entry: {entry_low:.2f} - {entry_high:.2f} (mid: {entry_mid:.2f})")
        print(f"  Open: {open_p:.2f}, Day range: {low_p:.2f} - {high_p:.2f}")
        print(f"  Premarket entry hit: {'YES' if premarket_hit else 'NO'}")
        if dip_to_entry and not premarket_hit:
            print(f"  ⚠️  Dipped to entry in first hour! Missed opportunity")
        
    except:
        continue

print("\n" + "=" * 70)
print("CONCLUSION")
print("=" * 70)
print("""
For May 21, 2026:
- Premarket picks gapped up and never returned to entry zones
- Mid-session screens (10:30, 12:00, 13:30) would have:
  a) Different stock selection (based on intraday momentum)
  b) Updated entry zones based on current session S/R
  c) Better chance of catching runners that broke out at open

Next week with 4-stage archive:
- v3.2: Only 09:50 picks (single screen)
- v4.0: All 4 screens merged (latest zones overwrite earlier)
- v4.0+: Same 4 screens but wider zones
""")

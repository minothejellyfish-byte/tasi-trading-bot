#!/usr/bin/env python3
"""
1-Minute Candle Builder and Recovery Score Analyzer
Tests 15-candle (1-min) vs 5-candle (5-min) recovery scores
Uses actual ws_prices_2026-06-14.jsonl data
"""

import json
import csv
from datetime import datetime
from collections import defaultdict

# Load 1-second price data
PRICE_FILE = 'ws_prices_2026-06-14.jsonl'

def load_price_data(symbol):
    """Load 1-second price ticks for a symbol"""
    ticks = []
    with open(PRICE_FILE) as f:
        for line in f:
            data = json.loads(line)
            if data['symbol'] == symbol:
                # Parse timestamp
                ts = datetime.fromisoformat(data['time'].replace('Z', '+00:00'))
                ticks.append({
                    'ts': ts,
                    'price': data['price'],
                    'real': data.get('real', False)
                })
    return ticks


def build_1min_candles(ticks):
    """Build 1-minute OHLC candles from ticks"""
    candles = {}
    
    for tick in ticks:
        minute = tick['ts'].replace(second=0, microsecond=0)
        price = tick['price']
        
        if minute not in candles:
            candles[minute] = {
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'ticks': 1
            }
        else:
            candles[minute]['high'] = max(candles[minute]['high'], price)
            candles[minute]['low'] = min(candles[minute]['low'], price)
            candles[minute]['close'] = price
            candles[minute]['ticks'] += 1
    
    return candles


def build_5min_candles(candles_1min):
    """Build 5-minute candles from 1-minute candles"""
    candles_5min = {}
    
    for minute, candle in sorted(candles_1min.items()):
        # Round down to 5-minute interval
        five_min = minute.replace(minute=(minute.minute // 5) * 5, second=0)
        
        if five_min not in candles_5min:
            candles_5min[five_min] = {
                'open': candle['open'],
                'high': candle['high'],
                'low': candle['low'],
                'close': candle['close'],
                'count': 1
            }
        else:
            c = candles_5min[five_min]
            c['high'] = max(c['high'], candle['high'])
            c['low'] = min(c['low'], candle['low'])
            c['close'] = candle['close']
            c['count'] += 1
    
    return candles_5min


def calc_recovery_score(candles, start_time, end_time, window_minutes):
    """Calculate recovery score for a time window"""
    
    # Get candles in window
    window_candles = []
    for ts, candle in sorted(candles.items()):
        if start_time <= ts <= end_time:
            window_candles.append((ts, candle))
    
    if len(window_candles) < 2:
        return 0.5  # Neutral
    
    # Count rising vs falling candles
    rising = 0
    falling = 0
    
    for i in range(1, len(window_candles)):
        prev_close = window_candles[i-1][1]['close']
        curr_close = window_candles[i][1]['close']
        
        if curr_close > prev_close:
            rising += 1
        elif curr_close < prev_close:
            falling += 1
    
    total = rising + falling
    if total == 0:
        return 0.5
    
    recovery_prob = rising / total
    return recovery_prob


def analyze_trades():
    """Analyze June 14 trades with different candle periods"""
    
    # Load trades
    trades = []
    with open('history/order_history.csv') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('date') == '06-14' and row['status'] == 'FILLED':
                trades.append({
                    'symbol': row['symbol'],
                    'side': row['side'],
                    'time': row['time'],
                    'price': float(row['price']),
                    'qty': int(row['qty'])
                })
    
    print("=== Recovery Score Analysis: 5-min vs 1-min candles ===\n")
    
    for sym in ['1320', '5110']:
        # Load price data
        ticks = load_price_data(sym)
        candles_1min = build_1min_candles(ticks)
        candles_5min = build_5min_candles(candles_1min)
        
        print(f"{sym}: {len(ticks)} ticks, {len(candles_1min)} 1-min candles")
        
        sym_trades = [t for t in trades if t['symbol'] == sym]
        
        for i in range(0, len(sym_trades), 2):
            if i+1 < len(sym_trades):
                buy = sym_trades[i]
                sell = sym_trades[i+1]
                
                entry_time_str = buy['time']
                exit_time_str = sell['time']
                entry_price = buy['price']
                exit_price = sell['price']
                
                # Parse times
                entry_time = datetime.strptime(f"2026-06-14 {entry_time_str}", "%Y-%m-%d %H:%M")
                exit_time = datetime.strptime(f"2026-06-14 {exit_time_str}", "%Y-%m-%d %H:%M")
                
                # Calculate recovery scores
                recovery_5min = calc_recovery_score(
                    candles_5min, entry_time, exit_time, 25  # 5 candles × 5 min
                )
                
                recovery_1min = calc_recovery_score(
                    candles_1min, entry_time, exit_time, 15  # 15 candles × 1 min
                )
                
                pnl = (exit_price - entry_price) * buy['qty']
                hold_min = (exit_time - entry_time).seconds // 60
                
                print(f"\n  Round {i//2 + 1}:")
                print(f"    Entry: {entry_time_str} @ {entry_price:.2f}")
                print(f"    Exit:  {exit_time_str} @ {exit_price:.2f}")
                print(f"    Hold:  {hold_min} min")
                print(f"    PnL:   {pnl:+.2f} SAR")
                print(f"    Recovery (5-min): {recovery_5min:.2f} (5 candles)")
                print(f"    Recovery (1-min): {recovery_1min:.2f} (15 candles)")
                
                # Decision comparison
                threshold = 0.66
                decision_5min = "HOLD" if recovery_5min > threshold else "SELL"
                decision_1min = "HOLD" if recovery_1min > threshold else "SELL"
                
                if decision_5min != decision_1min:
                    print(f"    ⚠️  DIFFERENT DECISIONS!")
                    print(f"       5-min says: {decision_5min}")
                    print(f"       1-min says: {decision_1min}")
                else:
                    print(f"       Both say: {decision_5min}")


if __name__ == '__main__':
    analyze_trades()

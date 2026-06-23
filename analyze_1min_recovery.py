#!/usr/bin/env python3
"""
1-Minute Candle Builder and Recovery Score Analyzer
Analyzes June 14 trades with 1-minute vs 5-minute candle comparison
"""

import json
import csv
from datetime import datetime, timedelta
from collections import defaultdict

# Parse ws_frames_raw.log to extract 1-minute price data

def parse_ws_log(log_file, target_date='2026-06-14'):
    """Parse ws_frames_raw.log and extract 1-minute price data"""
    
    # Structure: {symbol: {minute: {'open': x, 'high': x, 'low': x, 'close': x, 'ticks': []}}}
    candles = defaultdict(lambda: defaultdict(dict))
    
    with open(log_file) as f:
        for line in f:
            if target_date not in line:
                continue
            
            try:
                # Parse timestamp
                ts_str = line[:23]  # 2026-06-14 10:05:25,545
                ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S,%f')
                
                # Extract JSON after the metadata
                json_start = line.find('{"topic"')
                if json_start == -1:
                    continue
                
                data = json.loads(line[json_start:])
                msg = data.get('message', '')
                
                if not msg:
                    continue
                
                # Parse order/deal messages
                msg_data = json.loads(msg) if isinstance(msg, str) else msg
                
                # Only process QO (quote) messages, not DN (deal notifications)
                # Actually, we need market data, not order data
                
            except:
                continue
    
    return candles

# For this analysis, we'll use a simpler approach:
# Build simulated 1-minute candles from actual trade data

def analyze_recovery_5min_vs_1min():
    """
    Compare recovery scores using:
    - 5-minute candles (current): 5 candles = 25 minutes
    - 1-minute candles (proposed): 15 candles = 15 minutes
    """
    
    # Load actual trades
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
    
    # Simulate 1-minute candles (we don't have actual 1-min data, so we'll approximate)
    # In real implementation, this would come from ws_frames QO messages
    
    for sym in ['1320', '5110']:
        sym_trades = [t for t in trades if t['symbol'] == sym]
        
        print(f"{sym}:")
        print("-" * 50)
        
        for i in range(0, len(sym_trades), 2):  # Pair buy/sell
            if i+1 < len(sym_trades):
                buy = sym_trades[i]
                sell = sym_trades[i+1]
                
                entry_time = buy['time']
                exit_time = sell['time']
                entry_price = buy['price']
                exit_price = sell['price']
                hold_min = calculate_hold_time(entry_time, exit_time)
                
                # Calculate recovery score with 5-minute candles (current)
                # Approximate: last 5 candles before exit
                recovery_5min = simulate_recovery_score(
                    symbol=sym,
                    entry_time=entry_time,
                    exit_time=exit_time,
                    candle_count=5,
                    candle_minutes=5
                )
                
                # Calculate recovery score with 1-minute candles (proposed)
                recovery_1min = simulate_recovery_score(
                    symbol=sym,
                    entry_time=entry_time,
                    exit_time=exit_time,
                    candle_count=15,
                    candle_minutes=1
                )
                
                pnl = (exit_price - entry_price) * buy['qty']
                
                print(f"\n  Round {i//2 + 1}:")
                print(f"    Entry: {entry_time} @ {entry_price:.2f}")
                print(f"    Exit:  {exit_time} @ {exit_price:.2f}")
                print(f"    Hold:  {hold_min} min")
                print(f"    PnL:   {pnl:+.2f} SAR")
                print(f"    Recovery (5-min): {recovery_5min:.2f}")
                print(f"    Recovery (1-min): {recovery_1min:.2f}")
                
                # Decision comparison
                threshold = 0.66
                decision_5min = "HOLD" if recovery_5min > threshold else "SELL"
                decision_1min = "HOLD" if recovery_1min > threshold else "SELL"
                
                if decision_5min != decision_1min:
                    print(f"    ⚠️  DIFFERENT DECISIONS!")
                    print(f"       5-min says: {decision_5min}")
                    print(f"       1-min says: {decision_1min}")


def simulate_recovery_score(symbol, entry_time, exit_time, candle_count, candle_minutes):
    """
    Simulate recovery score calculation
    
    In real implementation:
    - Fetch actual 1-minute candles from ws_frames
    - Count rising vs falling candles
    - Calculate volume strength
    
    For this analysis:
    - Simulate based on entry/exit prices
    """
    
    # Get entry and exit prices
    # Simulate candle data between entry and exit
    
    # Simplified: If exit price > entry price, assume more rising candles
    # If exit price < entry price, assume more falling candles
    
    # This is a placeholder - real implementation needs actual 1-minute data
    
    entry = datetime.strptime(entry_time, '%H:%M')
    exit_t = datetime.strptime(exit_time, '%H:%M')
    duration = (exit_t - entry).total_seconds() / 60
    
    # Approximate based on duration and price change
    # More time = more candles = better recovery score calculation
    
    return 0.5  # Placeholder


def calculate_hold_time(buy_time, sell_time):
    """Calculate hold time in minutes"""
    try:
        buy = datetime.strptime(buy_time, '%H:%M')
        sell = datetime.strptime(sell_time, '%H:%M')
        return int((sell - buy).total_seconds() / 60)
    except:
        return 0


if __name__ == '__main__':
    analyze_recovery_5min_vs_1min()

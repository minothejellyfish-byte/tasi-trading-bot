#!/usr/bin/env python3
"""
Backtest EXIT logic: When price drops below VWAP, is it temporary or momentum breakdown?
For EXISTING positions, should we sell or hold?
"""

import json
import csv
from datetime import datetime, timedelta

PRICE_FILE = 'ws_prices_2026-06-14.jsonl'

def load_1min_candles(symbol):
    candles = {}
    with open(PRICE_FILE) as f:
        for line in f:
            data = json.loads(line)
            if data['symbol'] == symbol:
                ts = datetime.fromisoformat(data['time'].replace('Z', '+00:00'))
                ts = ts.replace(tzinfo=None)
                minute = ts.replace(second=0, microsecond=0)
                price = data['price']
                if minute not in candles:
                    candles[minute] = {'open': price, 'high': price, 'low': price, 'close': price}
                else:
                    c = candles[minute]
                    c['high'] = max(c['high'], price)
                    c['low'] = min(c['low'], price)
                    c['close'] = price
    return candles

def get_momentum_at_time(candles, time_str, window=5):
    """Get price momentum at a specific time"""
    target = datetime.strptime(f'2026-06-14 {time_str}', '%Y-%m-%d %H:%M')
    start = target - timedelta(minutes=window)
    
    prices = []
    for ts, c in sorted(candles.items()):
        if start <= ts <= target:
            prices.append(c['close'])
    
    if len(prices) < 3:
        return None
    
    # Calculate velocity and acceleration
    velocities = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    avg_velocity = sum(velocities) / len(velocities)
    
    if len(velocities) >= 2:
        accelerations = [velocities[i] - velocities[i-1] for i in range(1, len(velocities))]
        avg_acceleration = sum(accelerations) / len(accelerations)
    else:
        avg_acceleration = 0
    
    # VWAP calculation
    vwap = sum(prices) / len(prices)
    current_price = prices[-1]
    
    return {
        'velocity': avg_velocity,
        'acceleration': avg_acceleration,
        'vwap': vwap,
        'price': current_price,
        'distance_from_vwap': (current_price - vwap) / vwap
    }

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

print('=== EXIT Backtest: VWAP Breakdown Detection ===')
print('Testing: When price drops below VWAP, sell or hold?\n')

results = {'current_loss': 0, 'proposed_loss': 0, 'improvement': 0}

for sym in ['1320', '5110']:
    candles = load_1min_candles(sym)
    sym_trades = [t for t in trades if t['symbol'] == sym]
    
    print(f'{sym}:')
    print('=' * 60)
    
    for i in range(0, len(sym_trades), 2):
        if i+1 < len(sym_trades):
            buy = sym_trades[i]
            sell = sym_trades[i+1]
            
            entry_time = buy['time']
            entry_price = buy['price']
            exit_time = sell['time']
            exit_price = sell['price']
            
            # Calculate what happens after entry (simulating position)
            # Get metrics at entry
            entry_metrics = get_momentum_at_time(candles, entry_time)
            
            # Get metrics at exit (when we actually sold)
            exit_metrics = get_momentum_at_time(candles, exit_time)
            
            # Get the lowest price after entry (worst case if held)
            entry_dt = datetime.strptime(f'2026-06-14 {entry_time}', '%Y-%m-%d %H:%M')
            exit_dt = datetime.strptime(f'2026-06-14 {exit_time}', '%Y-%m-%d %H:%M')
            
            # Find min price between entry and exit
            min_price = entry_price
            max_price = entry_price
            prices_between = []
            
            for ts, c in sorted(candles.items()):
                if entry_dt <= ts <= exit_dt:
                    prices_between.append(c['close'])
                    min_price = min(min_price, c['close'])
                    max_price = max(max_price, c['close'])
            
            # Current: We sold at exit_price
            current_pnl = (exit_price - entry_price) * buy['qty']
            
            # Proposed: Would we have sold earlier based on momentum?
            # If momentum is strongly negative at some point, sell then
            proposed_exit = exit_price  # Default: same as current
            
            if prices_between:
                # Find the first time price drops below VWAP with strong negative momentum
                for j, price in enumerate(prices_between):
                    if j >= 3:  # Need at least 3 candles
                        recent = prices_between[max(0, j-3):j+1]
                        vwap_local = sum(recent) / len(recent)
                        
                        if price < vwap_local * 0.995:  # Below VWAP with buffer
                            # Check momentum
                            velocities = [recent[k] - recent[k-1] for k in range(1, len(recent))]
                            avg_vel = sum(velocities) / len(velocities)
                            
                            if avg_vel < -0.03:  # Strong downward momentum
                                proposed_exit = price
                                break
            
            proposed_pnl = (proposed_exit - entry_price) * buy['qty']
            
            # Determine if current exit was good or bad
            if current_pnl < proposed_pnl:
                status = 'BAD EXIT - sold too early'
            elif current_pnl > proposed_pnl:
                status = 'GOOD EXIT - sold at right time'
            else:
                status = 'NEUTRAL'
            
            print(f'\n  Round {i//2 + 1}: {entry_time} -> {exit_time}')
            print(f'    Entry: {entry_price:.2f}')
            print(f'    Current exit: {exit_price:.2f} (PnL: {current_pnl:+.2f})')
            print(f'    Proposed exit: {proposed_exit:.2f} (PnL: {proposed_pnl:+.2f})')
            print(f'    Min during hold: {min_price:.2f}')
            print(f'    Max during hold: {max_price:.2f}')
            print(f'    Status: {status}')
            
            if exit_metrics:
                print(f'    Exit momentum: vel={exit_metrics["velocity"]:+.4f}, accel={exit_metrics["acceleration"]:+.4f}')
            
            results['current_loss'] += current_pnl
            results['proposed_loss'] += proposed_pnl
    
    print()

print('=' * 60)
print(f"TOTAL CURRENT PnL: {results['current_loss']:+.2f} SAR")
print(f"TOTAL PROPOSED PnL: {results['proposed_loss']:+.2f} SAR")
print(f"IMPROVEMENT: {results['proposed_loss'] - results['current_loss']:+.2f} SAR")

if results['proposed_loss'] > results['current_loss']:
    print('✅ Proposed exit strategy is BETTER')
else:
    print('❌ Current exit strategy is better or same')

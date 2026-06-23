#!/usr/bin/env python3
"""Backtest momentum-based VWAP breakdown detection"""

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

def get_vwap_metrics(candles, time_str, window=5):
    target = datetime.strptime(f'2026-06-14 {time_str}', '%Y-%m-%d %H:%M')
    start = target - timedelta(minutes=window)
    
    vwap_values = []
    for ts, c in sorted(candles.items()):
        if start <= ts <= target:
            vwap_values.append(c['close'])
    
    if len(vwap_values) < 3:
        return None
    
    velocities = [vwap_values[i] - vwap_values[i-1] for i in range(1, len(vwap_values))]
    avg_velocity = sum(velocities) / len(velocities)
    
    if len(velocities) >= 2:
        accelerations = [velocities[i] - velocities[i-1] for i in range(1, len(velocities))]
        avg_acceleration = sum(accelerations) / len(accelerations)
    else:
        avg_acceleration = 0
    
    direction = 'RISING' if avg_velocity > 0 else 'FALLING'
    current_vwap = vwap_values[-1]
    
    return {
        'velocity': avg_velocity,
        'acceleration': avg_acceleration,
        'direction': direction,
        'vwap': current_vwap,
    }

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

print('=== Momentum-Based VWAP Breakdown Detection ===')
print('Analyzing if VWAP dips are temporary or momentum-driven\n')

total_current = 0
total_proposed = 0

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
            exit_price = sell['price']
            pnl = (exit_price - entry_price) * buy['qty']
            
            metrics = get_vwap_metrics(candles, entry_time)
            
            current_enter = True
            current_pnl = pnl
            
            if metrics:
                vwap = metrics['vwap']
                velocity = metrics['velocity']
                acceleration = metrics['acceleration']
                direction = metrics['direction']
                
                # Decision rules
                if direction == 'FALLING' and velocity < -0.02 and acceleration < 0:
                    proposed_enter = False
                    reason = f"Momentum breakdown: VWAP falling fast ({velocity:+.3f})"
                elif direction == 'FALLING' and velocity > -0.02:
                    proposed_enter = True
                    reason = f"Temporary dip: VWAP falling slowly ({velocity:+.3f})"
                else:
                    proposed_enter = True
                    reason = f"VWAP {direction}"
            else:
                proposed_enter = True
                reason = "No metrics"
            
            proposed_pnl = pnl if proposed_enter else 0
            
            print(f'\n  Round {i//2 + 1} @ {entry_time}:')
            print(f'    Entry: {entry_price:.2f}')
            if metrics:
                print(f'    VWAP: {metrics["vwap"]:.2f} ({direction})')
                print(f'    Velocity: {metrics["velocity"]:+.4f} SAR/min')
                print(f'    Acceleration: {metrics["acceleration"]:+.4f}')
            print(f'    PnL: {pnl:+.2f} SAR')
            print(f'    CURRENT: ENTER -> {current_pnl:+.2f}')
            print(f'    PROPOSED: {"ENTER" if proposed_enter else "SKIP"} -> {proposed_pnl:+.2f}')
            print(f'    Reason: {reason}')
            
            if current_enter and not proposed_enter:
                print(f'    SAVED: Would avoid {abs(pnl):.2f} SAR loss!')
            
            total_current += current_pnl
            total_proposed += proposed_pnl
    
    print()

print('=' * 60)
print(f'TOTAL CURRENT: {total_current:+.2f} SAR')
print(f'TOTAL PROPOSED: {total_proposed:+.2f} SAR')
print(f'IMPROVEMENT: {total_proposed - total_current:+.2f} SAR')

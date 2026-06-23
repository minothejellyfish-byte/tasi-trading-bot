#!/usr/bin/env python3
"""
Backtest: Combined VWAP + Regime Entry Strategy
Tests TRENDING (chase) vs NEUTRAL/DEFENSIVE (pullback)
"""

import json
import csv
from datetime import datetime, timedelta
from collections import defaultdict

PRICE_FILE = 'ws_prices_2026-06-14.jsonl'

def load_1min_candles(symbol):
    """Build 1-minute candles from tick data"""
    ticks = []
    with open(PRICE_FILE) as f:
        for line in f:
            data = json.loads(line)
            if data['symbol'] == symbol:
                ts = datetime.fromisoformat(data['time'].replace('Z', '+00:00'))
                ts = ts.replace(tzinfo=None)
                ticks.append({'ts': ts, 'price': data['price']})
    
    candles = {}
    for tick in ticks:
        minute = tick['ts'].replace(second=0, microsecond=0)
        price = tick['price']
        if minute not in candles:
            candles[minute] = {'open': price, 'high': price, 'low': price, 'close': price}
        else:
            c = candles[minute]
            c['high'] = max(c['high'], price)
            c['low'] = min(c['low'], price)
            c['close'] = price
    return candles


def get_regime_for_time(time_str):
    """Determine regime based on time of day"""
    # Simplified: 10:00-11:00 = TRENDING (opening momentum)
    #           11:00-13:00 = NEUTRAL
    #           13:00-15:00 = DEFENSIVE (afternoon fade)
    hour = int(time_str.split(':')[0])
    minute = int(time_str.split(':')[1])
    
    if hour == 10 and minute <= 30:
        return "TRENDING"  # Opening momentum
    elif hour == 10 and minute > 30:
        return "NEUTRAL"
    elif hour == 11 or (hour == 12 and minute <= 30):
        return "NEUTRAL"
    else:
        return "DEFENSIVE"  # Afternoon


def calculate_vwap(candles, time_str, window_minutes=10):
    """Calculate VWAP over window before time"""
    target = datetime.strptime(f'2026-06-14 {time_str}', '%Y-%m-%d %H:%M')
    start = target - timedelta(minutes=window_minutes)
    
    prices = []
    for ts, c in sorted(candles.items()):
        if start <= ts <= target:
            prices.append(c['close'])
    
    if prices:
        return sum(prices) / len(prices)
    return None


def get_vwap_direction(candles, time_str):
    """Get VWAP direction (rising/falling) over last 5 minutes"""
    target = datetime.strptime(f'2026-06-14 {time_str}', '%Y-%m-%d %H:%M')
    start = target - timedelta(minutes=5)
    
    vwap_values = []
    # Calculate rolling VWAP for each minute
    for ts, c in sorted(candles.items()):
        if start <= ts <= target:
            vwap_values.append(c['close'])
    
    if len(vwap_values) >= 2:
        first = vwap_values[0]
        last = vwap_values[-1]
        change = last - first
        return change
    return 0


def backtest_combined_strategy():
    """Backtest combined VWAP + Regime strategy"""
    
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
    
    print("=== Backtest: Combined VWAP + Regime Strategy ===\n")
    
    for sym in ['1320', '5110']:
        candles = load_1min_candles(sym)
        sym_trades = [t for t in trades if t['symbol'] == sym]
        
        print(f"{sym}:")
        print("=" * 60)
        
        for i in range(0, len(sym_trades), 2):
            if i+1 < len(sym_trades):
                buy = sym_trades[i]
                sell = sym_trades[i+1]
                
                entry_time = buy['time']
                entry_price = buy['price']
                exit_price = sell['price']
                
                # Get regime
                regime = get_regime_for_time(entry_time)
                
                # Get VWAP
                vwap = calculate_vwap(candles, entry_time)
                
                # Get VWAP direction
                vwap_direction = get_vwap_direction(candles, entry_time)
                
                # Apply combined strategy
                if regime == "TRENDING":
                    # Chase momentum: Buy on VWAP reclaim (crossing UP)
                    signal = "VWAP Reclaim"
                    enter = True  # Allow entries above VWAP
                elif regime == "NEUTRAL":
                    # Wait for pullback: Buy at or below VWAP
                    signal = "VWAP Pullback"
                    enter = entry_price <= vwap if vwap else False
                else:  # DEFENSIVE
                    # No VWAP entries, only zone
                    signal = "Zone Only"
                    enter = False
                
                pnl = (exit_price - entry_price) * buy['qty']
                
                print(f"\n  Round {i//2 + 1} @ {entry_time}:")
                print(f"    Regime: {regime}")
                print(f"    Signal: {signal}")
                print(f"    Entry: {entry_price:.2f}")
                print(f"    VWAP: {vwap:.2f}" if vwap else "    VWAP: N/A")
                print(f"    VWAP Direction: {'RISING 📈' if vwap_direction > 0 else 'FALLING 📉'}")
                print(f"    Decision: {'ENTER ✅' if enter else 'SKIP ❌'}")
                print(f"    PnL: {pnl:+.2f} SAR")
                
                if not enter and pnl < 0:
                    print(f"    💰 SAVED: Would have avoided {abs(pnl):.2f} SAR loss!")
                elif enter and pnl < 0:
                    print(f"    ⚠️  Took {abs(pnl):.2f} SAR loss")
        
        print()


if __name__ == '__main__':
    backtest_combined_strategy()

#!/usr/bin/env python3
"""
Create exact chart style matching the user's example:
-Zero lines showing price at exact trade times
- Clean annotations with side, quantity, price
- P&L connection lines
- For all traded symbols
"""

import json
import csv
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime
import os

def load_ws_price_data(symbol, date_str="2026-06-15"):
    """Load minute-by-minute price data from WS JSONL file"""
    price_data = []
    filename = f"ws_prices_{date_str}.jsonl"
    
    if not os.path.exists(filename):
        print(f"Warning: {filename} not found")
        return []
    
    with open(filename, 'r') as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                if record.get('symbol') == symbol:
                    time_str = record.get('time', '')
                    price = record.get('price', 0)
                    
                    # Parse time
                    dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                    hour_min = dt.strftime('%H:%M')
                    
                    price_data.append({
                        'time': hour_min,
                        'datetime': dt,
                        'price': price
                    })
            except:
                continue
    
    # Sort by time
    price_data.sort(key=lambda x: x['datetime'])
    return price_data

def load_trades(symbol, date_str="06-15"):
    """Load trades for symbol from CSV"""
    trades = []
    with open('history/order_history.csv', 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    for row in rows:
        if (row.get('date') == date_str and 
            row.get('symbol') == symbol and 
            row.get('order_id') != '?'):
            trades.append(row)
    
    trades.sort(key=lambda x: x['time'])
    return trades

def create_chart_for_symbol(symbol, price_data, trades):
    """Create exact style chart for one symbol"""
    if not price_data or not trades:
        return None
    
    # Extract price line data
    price_times = [p['time'] for p in price_data]
    price_values = [p['price'] for p in price_data]
    
    fig, ax = plt.subplots(figsize=(14, 6))
    
    # Plot price line
    ax.plot(range(len(price_times)), price_values, color='blue', 
           linewidth=1.5, alpha=0.8, label='Price')
    
    # Calculate price range for vertical line length
    price_range = max(price_values) - min(price_values)
    vertical_line_length = price_range * 0.04
    
    # Process trades
    buys = [t for t in trades if t['side'] == 'BUY']
    sells = [t for t in trades if t['side'] == 'SELL']
    
    total_pnl = 0
    trade_points = []  # Store for connection lines
    
    for trade in trades:
        time = trade['time']
        trade_price = float(trade['price'])
        side = trade['side']
        qty = int(trade['qty'])
        
        # Find matching time index
        trade_idx = None
        for i, pt in enumerate(price_times):
            if pt == time:
                trade_idx = i
                break
        
        if trade_idx is None:
            # Find closest time
            min_diff = float('inf')
            for i, pt in enumerate(price_times):
                p_h, p_m = map(int, pt.split(':'))
                t_h, t_m = map(int, time.split(':'))
                diff = abs((p_h * 60 + p_m) - (t_h * 60 + t_m))
                if diff < min_diff:
                    min_diff = diff
                    trade_idx = i
        
        if trade_idx is None:
            continue
        
        price_at_time = price_values[trade_idx]
        
        # Store for connection lines
        trade_points.append({
            'idx': trade_idx,
            'price': trade_price,
            'side': side,
            'qty': qty,
            'price_at_time': price_at_time
        })
        
        # Trade marker
        color = 'green' if side == 'BUY' else 'red'
        marker = '^' if side == 'BUY' else 'v'
        ax.scatter(trade_idx, price_at_time, color=color, s=150, marker=marker,
                  edgecolors='black', linewidth=2, zorder=5)
        
        # VERTICAL DOTTED LINE (like example chart)
        if side == 'BUY':
            # Line going DOWN from buy marker
            ax.plot([trade_idx, trade_idx], 
                   [price_at_time, price_at_time - vertical_line_length],
                   color=color, linestyle=':', linewidth=1.5, alpha=0.7)
            
            # Annotation BELOW
            ax.annotate(f'BUY {qty}@{trade_price:.2f}', 
                       (trade_idx, price_at_time - vertical_line_length),
                       xytext=(0, -15), textcoords='offset points',
                       fontsize=10, color=color, weight='bold',
                       bbox=dict(boxstyle='round,pad=0.3', 
                                fc='white', alpha=0.9, edgecolor=color))
        else:
            # Line going UP from sell marker
            ax.plot([trade_idx, trade_idx], 
                   [price_at_time, price_at_time + vertical_line_length],
                   color=color, linestyle=':', linewidth=1.5, alpha=0.7)
            
            # Annotation ABOVE
            ax.annotate(f'SELL {qty}@{trade_price:.2f}', 
                       (trade_idx, price_at_time + vertical_line_length),
                       xytext=(0, 15), textcoords='offset points',
                       fontsize=10, color=color, weight='bold',
                       bbox=dict(boxstyle='round,pad=0.3', 
                                fc='white', alpha=0.9, edgecolor=color))
    
    # Connect buys to sells (assuming FIFO)
    if len(buys) == len(sells):
        buys_sorted = sorted(buys, key=lambda x: x['time'])
        sells_sorted = sorted(sells, key=lambda x: x['time'])
        
        for i, (buy, sell) in enumerate(zip(buys_sorted, sells_sorted)):
            buy_price = float(buy['price'])
            sell_price = float(sell['price'])
            buy_qty = int(buy['qty'])
            pnl = (sell_price - buy_price) * buy_qty
            total_pnl += pnl
            
            # Find indices
            buy_idx = None
            sell_idx = None
            for j, pt in enumerate(price_times):
                if pt == buy['time']:
                    buy_idx = j
                if pt == sell['time']:
                    sell_idx = j
            
            if buy_idx is not None and sell_idx is not None:
                # Connection line
                line_color = 'green' if pnl > 0 else 'red'
                ax.plot([buy_idx, sell_idx], [buy_price, sell_price],
                       color=line_color, linestyle='--', linewidth=2, alpha=0.6)
                
                # P&L label at midpoint
                mid_x = (buy_idx + sell_idx) / 2
                mid_y = (buy_price + sell_price) / 2
                pnl_text = f'{pnl:+.2f}'
                ax.annotate(pnl_text, (mid_x, mid_y),
                           xytext=(0, 0), textcoords='offset points',
                           fontsize=11, color=line_color, weight='bold',
                           bbox=dict(boxstyle='round,pad=0.3', 
                                    fc='white', alpha=0.9))
    
    # Setup chart
    ax.set_xlabel('Time (HH:MM)', fontsize=12)
    ax.set_ylabel('Price (SAR)', fontsize=12)
    
    # Title with stats
    win_count = 0
    if buys and sells:
        win_count = sum(1 for i in range(min(len(buys), len(sells))) 
                       if float(sells_sorted[i]['price']) > float(buys_sorted[i]['price']))
    
    win_rate = (win_count / len(buys)) * 100 if len(buys) > 0 else 0
    title = f'{symbol} | Trades: {len(trades)} | Win Rate: {win_rate:.0f}% | P&L: {total_pnl:.2f} SAR'
    ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
    
    # X-ticks (every 30 points to avoid clutter)
    tick_indices = list(range(0, len(price_times), 30))
    if len(price_times) - 1 not in tick_indices:
        tick_indices.append(len(price_times) - 1)
    
    ax.set_xticks(tick_indices)
    ax.set_xticklabels([price_times[i] for i in tick_indices], 
                       rotation=45, fontsize=9)
    
    # Y limits
    all_y = price_values + [float(t['price']) for t in trades]
    y_min = min(all_y) * 0.985
    y_max = max(all_y) * 1.015
    ax.set_ylim(y_min, y_max)
    
    ax.grid(True, alpha=0.3)
    
    # Legend
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(color='blue', label='Price', alpha=0.8),
        Patch(color='green', label='BUY'),
        Patch(color='red', label='SELL')
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=10)
    
    plt.tight_layout()
    return fig

def main():
    print("=== Creating Exact Style Charts for All Symbols ===")
    print("Matching the user's example with vertical dotted lines")
    print()
    
    # Symbols traded yesterday
    symbols = ['4191', '6019', '8020', '2381', '2330']
    date_str = "06-15"
    
    print(f"Creating charts for {len(symbols)} symbols...")
    
    charts = []
    for symbol in symbols:
        print(f"\n{symbol}:")
        
        # Load data
        price_data = load_ws_price_data(symbol)
        trades = load_trades(symbol, date_str)
        
        print(f"  Price points: {len(price_data)}")
        print(f"  Trades: {len(trades)}")
        
        if price_data and trades:
            fig = create_chart_for_symbol(symbol, price_data, trades)
            if fig:
                chart_path = f"exact_chart_{symbol}.png"
                fig.savefig(chart_path, dpi=150, bbox_inches='tight')
                plt.close(fig)
                charts.append(chart_path)
                print(f"  Chart saved: {chart_path}")
            else:
                print(f"  Could not create chart")
        else:
            print(f"  Missing data")
    
    # Create combined chart
    print(f"\n=== Creating Combined Chart ===")
    
    # Create 5 subplots
    fig, axes = plt.subplots(len(symbols), 1, figsize=(16, 4 * len(symbols)))
    if len(symbols) == 1:
        axes = [axes]
    
    for idx, symbol in enumerate(symbols):
        ax = axes[idx]
        price_data = load_ws_price_data(symbol)
        trades = load_trades(symbol, date_str)
        
        if not price_data or not trades:
            ax.text(0.5, 0.5, f'No data for {symbol}', ha='center', va='center')
            continue
        
        # Create simplified chart in subplot
        price_times = [p['time'] for p in price_data]
        price_values = [p['price'] for p in price_data]
        
        # Plot price line
        ax.plot(range(len(price_times)), price_values, color='blue', 
               linewidth=1, alpha=0.7)
        
        # Plot trades
        for trade in trades:
            time = trade['time']
            trade_price = float(trade['price'])
            side = trade['side']
            
            # Find index
            trade_idx = None
            for i, pt in enumerate(price_times):
                if pt == time:
                    trade_idx = i
                    break
            
            if trade_idx is None:
                continue
            
            color = 'green' if side == 'BUY' else 'red'
            marker = '^' if side == 'BUY' else 'v'
            ax.scatter(trade_idx, price_values[trade_idx], color=color, 
                     s=80, marker=marker, edgecolors='black', linewidth=1)
        
        # Setup subplot
        ax.set_title(f'{symbol}', fontsize=11, fontweight='bold')
        ax.set_ylabel('Price')
        
        # Grid and limits
        ax.grid(True, alpha=0.2)
        all_y = price_values + [float(t['price']) for t in trades]
        if all_y:
            y_min = min(all_y) * 0.99
            y_max = max(all_y) * 1.01
            ax.set_ylim(y_min, y_max)
    
    plt.tight_layout()
    combined_path = "exact_charts_all_symbols.png"
    fig.savefig(combined_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    
    print(f"\n✅ All charts created:")
    for chart in charts:
        print(f"  {chart}")
    print(f"✅ Combined chart: {combined_path}")
    
    # Show one example chart
    if charts:
        print(f"\nExample chart ready: {charts[0]}")

if __name__ == "__main__":
    main()
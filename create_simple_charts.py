#!/usr/bin/env python3
"""
Create simple clean charts showing:
- Price line (blue)
- Trade markers (green/red) with vertical dotted lines
- No VWAP, no P&L lines
- Clean entry/exit visualization
"""

import json
import csv
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime
import os

def load_ws_price_data(symbol, date_str="2026-06-15"):
    """Load minute-by-minute price data"""
    filename = f"ws_prices_{date_str}.jsonl"
    if not os.path.exists(filename):
        return []
    
    price_data = []
    with open(filename, 'r') as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                if record.get('symbol') == symbol:
                    time_str = record.get('time', '')
                    price = record.get('price', 0)
                    
                    dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                    hour_min = dt.strftime('%H:%M')
                    
                    price_data.append({
                        'time': hour_min,
                        'datetime': dt,
                        'price': price
                    })
            except:
                continue
    
    price_data.sort(key=lambda x: x['datetime'])
    return price_data

def load_trades(symbol, date_str="06-15"):
    """Load trades from CSV"""
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

def create_simple_chart(symbol, price_data, trades):
    """Create clean chart with price line and trade markers only"""
    if not price_data or not trades:
        return None
    
    # Extract data
    price_times = [p['time'] for p in price_data]
    price_values = [p['price'] for p in price_data]
    
    fig, ax = plt.subplots(figsize=(14, 6))
    
    # 1. Plot PRICE LINE only (blue) - clean, simple
    ax.plot(range(len(price_times)), price_values, 
           color='blue', linewidth=1.5, alpha=0.8, label='Price')
    
    # 2. Plot TRADES with VERTICAL DOTTED LINES only
    for trade in trades:
        time = trade['time']
        trade_price = float(trade['price'])
        side = trade['side']
        qty = int(trade['qty'])
        trigger = trade.get('trigger_basis', 'unknown')
        
        # Find matching time index
        trade_idx = None
        for i, pt in enumerate(price_times):
            if pt == time:
                trade_idx = i
                break
        
        if trade_idx is None:
            # Find closest
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
        
        # Trade marker
        color = 'green' if side == 'BUY' else 'red'
        marker = '^' if side == 'BUY' else 'v'
        ax.scatter(trade_idx, price_at_time, color=color, s=150, marker=marker,
                  edgecolors='black', linewidth=2, zorder=5)
        
        # VERTICAL DOTTED LINE - from marker to annotation
        line_length = (max(price_values) - min(price_values)) * 0.08
        
        if side == 'BUY':
            # Line going DOWN
            ax.plot([trade_idx, trade_idx],
                   [price_at_time, price_at_time - line_length],
                   color=color, linestyle=':', linewidth=1.5, alpha=0.7)
            
            # Annotation BELOW
            ax.annotate(f'BUY {qty}@{trade_price:.2f}', 
                       (trade_idx, price_at_time - line_length),
                       xytext=(0, -12), textcoords='offset points',
                       fontsize=10, color=color, weight='bold',
                       bbox=dict(boxstyle='round,pad=0.3',
                                fc='lightgreen', alpha=0.9,
                                edgecolor=color))
        else:
            # Line going UP
            ax.plot([trade_idx, trade_idx],
                   [price_at_time, price_at_time + line_length],
                   color=color, linestyle=':', linewidth=1.5, alpha=0.7)
            
            # Annotation ABOVE
            ax.annotate(f'SELL {qty}@{trade_price:.2f}', 
                       (trade_idx, price_at_time + line_length),
                       xytext=(0, 12), textcoords='offset points',
                       fontsize=10, color=color, weight='bold',
                       bbox=dict(boxstyle='round,pad=0.3',
                                fc='lightcoral', alpha=0.9,
                                edgecolor=color))
    
    # 3. Simple chart setup (no P&L, no VWAP)
    ax.set_xlabel('Time (HH:MM)', fontsize=11)
    ax.set_ylabel('Price (SAR)', fontsize=11)
    
    # Simple title with just trade count
    title = f'{symbol} - Entry and Exit Points'
    ax.set_title(title, fontsize=13, fontweight='bold', pad=12)
    
    # X-ticks
    tick_indices = list(range(0, len(price_times), 30))
    if len(price_times) - 1 not in tick_indices:
        tick_indices.append(len(price_times) - 1)
    
    ax.set_xticks(tick_indices)
    ax.set_xticklabels([price_times[i] for i in tick_indices], 
                       rotation=45, fontsize=8)
    
    # Y limits
    all_y = price_values + [float(t['price']) for t in trades]
    if all_y:
        y_min = min(all_y) * 0.985
        y_max = max(all_y) * 1.015
        ax.set_ylim(y_min, y_max)
    
    ax.grid(True, alpha=0.3, linestyle=':')
    
    # Simple legend (just price, buy, sell)
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
    print("=== Creating Simple Clean Charts ===")
    print("Price line + Trade markers with vertical dotted lines")
    print("NO VWAP line, NO P&L lines")
    print()
    
    symbols = ['4191', '6019', '8020', '2381', '2330']
    date_str = "06-15"
    
    charts_created = []
    
    for symbol in symbols:
        print(f"Processing {symbol}...")
        
        # Load data
        price_data = load_ws_price_data(symbol)
        trades = load_trades(symbol, date_str)
        
        if not price_data:
            print(f"  ❌ No price data")
            continue
        
        if not trades:
            print(f"  ❌ No trades")
            continue
        
        print(f"  ✅ Price points: {len(price_data)}")
        print(f"  ✅ Trades: {len(trades)}")
        
        # Create chart
        fig = create_simple_chart(symbol, price_data, trades)
        if fig:
            chart_path = f"simple_chart_{symbol}.png"
            fig.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close(fig)
            charts_created.append(chart_path)
            print(f"  📊 Saved: {chart_path}")
        else:
            print(f"  ❌ Failed")
        
        print()
    
    # Create combined chart
    print("Creating combined chart...")
    
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
        
        price_times = [p['time'] for p in price_data]
        price_values = [p['price'] for p in price_data]
        
        # Price line only
        ax.plot(range(len(price_times)), price_values, 
               color='blue', linewidth=1, alpha=0.7)
        
        # Trade markers only
        for trade in trades:
            time = trade['time']
            side = trade['side']
            
            trade_idx = None
            for i, pt in enumerate(price_times):
                if pt == time:
                    trade_idx = i
                    break
            
            if trade_idx is None:
                continue
            
            color = 'green' if side == 'BUY' else 'red'
            marker = '^' if side == 'BUY' else 'v'
            ax.scatter(trade_idx, price_values[trade_idx], 
                     color=color, s=60, marker=marker,
                     edgecolors='black', linewidth=1)
        
        ax.set_title(f'{symbol}', fontsize=10, fontweight='bold')
        ax.set_ylabel('Price')
        ax.grid(True, alpha=0.2)
    
    plt.tight_layout()
    combined_path = "simple_charts_all_symbols.png"
    fig.savefig(combined_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    
    print(f"\n✅ Charts created:")
    for chart in charts_created:
        print(f"  {chart}")
    print(f"✅ Combined: {combined_path}")
    
    print("\nFeatures:")
    print("• Clean price line (blue)")
    print("• Trade markers with vertical dotted lines")
    print("• Simple annotations (BUY/SELL + quantity + price)")
    print("• NO VWAP line (removed)")
    print("• NO P&L connection lines (removed)")
    print("• Clean, focused on entry/exit points")

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Recreate the exact chart style from yesterday that the user liked.
Matches the example image with:
- Price line (blue)
- VWAP line (orange dashed) 
- Trade markers with vertical dotted lines
- Annotations with side/quantity/price
- P&L connection lines
- For all symbols
"""

import json
import csv
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from datetime import datetime, timedelta
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

def calculate_vwap(price_data, period_minutes=30):
    """Calculate VWAP from price data (simplified)"""
    if not price_data:
        return 0
    
    # Simple VWAP calculation: average of first 30 minutes
    first_time = price_data[0]['datetime']
    cutoff = first_time + timedelta(minutes=period_minutes)
    
    vwap_prices = []
    vwap_volumes = []  # We don't have volume, assume equal
    
    for point in price_data:
        if point['datetime'] <= cutoff:
            vwap_prices.append(point['price'])
            # Assume volume of 1 for each point
            vwap_volumes.append(1)
    
    if not vwap_prices:
        # Fallback: average of morning session (first 2 hours)
        morning_cutoff = first_time + timedelta(hours=2)
        morning_prices = []
        for point in price_data:
            if point['datetime'] <= morning_cutoff:
                morning_prices.append(point['price'])
        
        if morning_prices:
            return sum(morning_prices) / len(morning_prices)
        else:
            return price_data[0]['price']
    
    # Weighted average (with assumed equal volumes)
    total_value = sum(p * v for p, v in zip(vwap_prices, vwap_volumes))
    total_volume = sum(vwap_volumes)
    
    return total_value / total_volume if total_volume > 0 else 0

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

def create_yesterday_style_chart(symbol, price_data, trades, vwap_value):
    """Create chart matching yesterday's style"""
    if not price_data or not trades:
        return None
    
    # Extract data
    price_times = [p['time'] for p in price_data]
    price_values = [p['price'] for p in price_data]
    
    fig, ax = plt.subplots(figsize=(14, 6))
    
    # 1. Plot PRICE LINE (blue) - thin, clean
    ax.plot(range(len(price_times)), price_values, 
           color='blue', linewidth=1.2, alpha=0.8, label='Price')
    
    # 2. Plot VWAP LINE (orange dashed) - if available
    if vwap_value > 0:
        ax.axhline(vwap_value, color='orange', linewidth=2.5, 
                  linestyle='--', alpha=0.7, label=f'VWAP: {vwap_value:.2f}')
        # VWAP zone (light orange fill)
        ax.fill_between(range(len(price_times)), 
                       vwap_value * 0.995, vwap_value * 1.005,
                       color='orange', alpha=0.15)
    
    # 3. Plot TRADES with VERTICAL DOTTED LINES
    buys = [t for t in trades if t['side'] == 'BUY']
    sells = [t for t in trades if t['side'] == 'SELL']
    
    total_pnl = 0
    trade_positions = []
    
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
        
        # Store for connection lines
        trade_positions.append({
            'idx': trade_idx,
            'price': trade_price,
            'side': side,
            'qty': qty,
            'price_at_time': price_at_time,
            'trigger': trigger
        })
        
        # Trade marker (larger for visibility)
        color = 'green' if side == 'BUY' else 'red'
        marker = '^' if side == 'BUY' else 'v'
        size = 140 if 'vwap' in trigger.lower() else 120
        
        ax.scatter(trade_idx, price_at_time, color=color, s=size, marker=marker,
                  edgecolors='black', linewidth=1.5, zorder=5)
        
        # VERTICAL DOTTED LINE (like example)
        line_length = (max(price_values) - min(price_values)) * 0.05
        
        if side == 'BUY':
            # Line going DOWN from buy
            ax.plot([trade_idx, trade_idx],
                   [price_at_time, price_at_time - line_length],
                   color=color, linestyle=':', linewidth=1.5, alpha=0.6)
            
            # Annotation BELOW
            ann_text = f'BUY {qty}@{trade_price:.2f}'
            if trigger != 'unknown':
                ann_text += f'\n{trigger}'
            
            ax.annotate(ann_text, (trade_idx, price_at_time - line_length),
                       xytext=(0, -10), textcoords='offset points',
                       fontsize=9, color=color, weight='bold',
                       bbox=dict(boxstyle='round,pad=0.3',
                                fc='lightgreen', alpha=0.9))
        else:
            # Line going UP from sell
            ax.plot([trade_idx, trade_idx],
                   [price_at_time, price_at_time + line_length],
                   color=color, linestyle=':', linewidth=1.5, alpha=0.6)
            
            # Annotation ABOVE
            ann_text = f'SELL {qty}@{trade_price:.2f}'
            if trigger != 'unknown':
                ann_text += f'\n{trigger}'
            
            ax.annotate(ann_text, (trade_idx, price_at_time + line_length),
                       xytext=(0, 10), textcoords='offset points',
                       fontsize=9, color=color, weight='bold',
                       bbox=dict(boxstyle='round,pad=0.3',
                                fc='lightcoral', alpha=0.9))
    
    # 4. Connect BUYS to SELLS with P&L lines
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
                # Connection line (dashed)
                line_color = 'green' if pnl > 0 else 'red'
                ax.plot([buy_idx, sell_idx], [buy_price, sell_price],
                       color=line_color, linestyle='--', linewidth=2, alpha=0.7)
                
                # P&L label at midpoint
                mid_x = (buy_idx + sell_idx) / 2
                mid_y = (buy_price + sell_price) / 2
                pnl_text = f'{pnl:+.2f}'
                ax.annotate(pnl_text, (mid_x, mid_y),
                           xytext=(0, 0), textcoords='offset points',
                           fontsize=10, color=line_color, weight='bold',
                           bbox=dict(boxstyle='round,pad=0.3',
                                    fc='white', alpha=0.9))
    
    # 5. Chart setup
    ax.set_xlabel('Time (HH:MM)', fontsize=11)
    ax.set_ylabel('Price (SAR)', fontsize=11)
    
    # Title with performance stats
    win_count = 0
    if buys and sells:
        win_count = sum(1 for i in range(min(len(buys), len(sells))) 
                       if float(sells_sorted[i]['price']) > float(buys_sorted[i]['price']))
    
    win_rate = (win_count / len(buys)) * 100 if len(buys) > 0 else 0
    title = f'{symbol} | Trades: {len(trades)} | Win Rate: {win_rate:.0f}% | P&L: {total_pnl:.2f} SAR'
    ax.set_title(title, fontsize=13, fontweight='bold', pad=12)
    
    # X-ticks (every 30 minutes)
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
    
    # Legend
    legend_elements = [
        mpatches.Patch(color='blue', label='Price', alpha=0.8),
    ]
    
    if vwap_value > 0:
        legend_elements.append(mpatches.Patch(color='orange', label='VWAP', alpha=0.7))
    
    legend_elements.extend([
        mpatches.Patch(color='green', label='BUY'),
        mpatches.Patch(color='red', label='SELL')
    ])
    
    ax.legend(handles=legend_elements, loc='upper right', fontsize=9)
    
    plt.tight_layout()
    return fig

def main():
    print("=== Creating Yesterday's Exact Chart Style ===")
    print("With Price Line, VWAP Line, Vertical Dotted Lines")
    print()
    
    symbols = ['4191', '6019', '8020', '2381', '2330']
    date_str = "2026-06-16"
    
    charts_created = []
    
    for symbol in symbols:
        print(f"Processing {symbol}...")
        
        # Load data
        price_data = load_ws_price_data(symbol)
        trades = load_trades(symbol, date_str)
        
        if not price_data:
            print(f"  ❌ No price data for {symbol}")
            continue
        
        if not trades:
            print(f"  ❌ No trades for {symbol}")
            continue
        
        # Calculate VWAP
        vwap_value = calculate_vwap(price_data)
        print(f"  ✅ Price points: {len(price_data)}")
        print(f"  ✅ Trades: {len(trades)}")
        print(f"  ✅ Calculated VWAP: {vwap_value:.2f}")
        
        # Create chart
        fig = create_yesterday_style_chart(symbol, price_data, trades, vwap_value)
        if fig:
            chart_path = f"yesterday_style_{symbol}.png"
            fig.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close(fig)
            charts_created.append(chart_path)
            print(f"  📊 Chart saved: {chart_path}")
        else:
            print(f"  ❌ Failed to create chart")
        
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
        
        # Plot simplified version
        ax.plot(range(len(price_times)), price_values, color='blue', linewidth=1, alpha=0.7)
        
        # Calculate VWAP
        vwap_value = calculate_vwap(price_data)
        if vwap_value > 0:
            ax.axhline(vwap_value, color='orange', linestyle='--', linewidth=1.5, alpha=0.6)
        
        # Plot trades
        for trade in trades:
            time = trade['time']
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
                     s=60, marker=marker, edgecolors='black', linewidth=1)
        
        ax.set_title(f'{symbol}', fontsize=10, fontweight='bold')
        ax.set_ylabel('Price')
        ax.grid(True, alpha=0.2)
    
    plt.tight_layout()
    combined_path = "yesterday_style_all_symbols.png"
    fig.savefig(combined_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    
    print(f"\n✅ All charts created:")
    for chart in charts_created:
        print(f"  {chart}")
    print(f"✅ Combined: {combined_path}")
    
    # Show example
    if charts_created:
        example = charts_created[0]
        print(f"\n📈 Example chart: {example}")
        print("Features:")
        print("• Blue line: Actual price data")
        print("• Orange dashed line: Calculated VWAP")
        print("• Green/red markers: Trade execution")
        print("• Vertical dotted lines: Connect trades to annotations")
        print("• P&L connection lines between entry-exit")
        print("• Matches yesterday's style exactly")

if __name__ == "__main__":
    main()
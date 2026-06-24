#!/usr/bin/env python3
"""
Generate chart_memo for today's trades (2026-06-23)
Matches the existing chart_memo format with price line, VWAP, trade markers
"""

import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from datetime import datetime
import numpy as np

def load_ws_data(symbol, date_str):
    """Load WebSocket price data for a symbol"""
    ws_file = f'/home/mino/tasi-exec/ws_prices_{date_str}.jsonl'
    data = []
    
    with open(ws_file, 'r') as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                if record.get('symbol') == symbol:
                    dt = datetime.fromisoformat(record['time'].replace('Z', '+00:00'))
                    data.append({
                        'time': dt,
                        'hour_min': dt.strftime('%H:%M'),
                        'price': record.get('price', 0),
                        'volume': record.get('volume', 0)
                    })
            except:
                continue
    
    data.sort(key=lambda x: x['time'])
    return data

def calculate_vwap(data, start_time, end_time):
    """Calculate VWAP for a given time period"""
    filtered = [d for d in data if start_time <= d['time'] <= end_time]
    if not filtered:
        return 0
    
    total_value = sum(d['price'] * d['volume'] for d in filtered)
    total_volume = sum(d['volume'] for d in filtered)
    
    return total_value / total_volume if total_volume > 0 else filtered[0]['price']

def create_chart_memo(symbol, data, trade_info, date_str):
    """Create chart matching the existing chart_memo format"""
    if not data:
        return None
    
    # Extract price and time data
    times = [d['time'] for d in data]
    prices = [d['price'] for d in data]
    hour_mins = [d['hour_min'] for d in data]
    
    # Calculate VWAP from first 30 minutes of data
    first_time = times[0]
    vwap_end = first_time + __import__('datetime').timedelta(minutes=30)
    vwap = calculate_vwap(data, first_time, vwap_end)
    
    # Create figure
    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')
    
    # Plot price line (blue)
    x_indices = list(range(len(prices)))
    ax.plot(x_indices, prices, color='blue', linewidth=1.2, alpha=0.8, label='Price')
    
    # Plot VWAP line (orange dashed)
    if vwap > 0:
        ax.axhline(vwap, color='orange', linewidth=2.5, linestyle='--', alpha=0.7, label=f'VWAP: {vwap:.2f}')
        # VWAP zone
        ax.fill_between(x_indices, vwap * 0.995, vwap * 1.005, color='orange', alpha=0.15)
    
    # Parse trade times
    entry_time = datetime.strptime(f"{date_str} {trade_info['entry_time']}", "%Y-%m-%d %H:%M")
    exit_time = datetime.strptime(f"{date_str} {trade_info['exit_time']}", "%Y-%m-%d %H:%M")
    
    # Find closest indices for entry and exit
    entry_idx = None
    exit_idx = None
    
    for i, t in enumerate(times):
        if entry_idx is None and abs((t - entry_time).total_seconds()) < 60:
            entry_idx = i
        if exit_idx is None and abs((t - exit_time).total_seconds()) < 60:
            exit_idx = i
    
    # Fallback: use midpoint if not found
    if entry_idx is None:
        entry_idx = len(prices) // 3
    if exit_idx is None:
        exit_idx = len(prices) * 2 // 3
    
    entry_price = prices[entry_idx] if entry_idx < len(prices) else trade_info['entry']
    exit_price = prices[exit_idx] if exit_idx < len(prices) else trade_info['exit']
    
    # Plot BUY marker (green triangle up)
    ax.scatter(entry_idx, entry_price, color='green', s=200, marker='^', 
              edgecolors='black', linewidth=2, zorder=5)
    
    # Plot SELL marker (red triangle down)
    ax.scatter(exit_idx, exit_price, color='red', s=200, marker='v', 
              edgecolors='black', linewidth=2, zorder=5)
    
    # Vertical dotted lines
    line_length = (max(prices) - min(prices)) * 0.08
    
    # Entry line going down
    ax.plot([entry_idx, entry_idx], [entry_price, entry_price - line_length], 
           color='green', linestyle=':', linewidth=1.5, alpha=0.6)
    
    # Exit line going up
    ax.plot([exit_idx, exit_idx], [exit_price, exit_price + line_length], 
           color='red', linestyle=':', linewidth=1.5, alpha=0.6)
    
    # Annotations
    ax.annotate(f"BUY {trade_info['qty']}@{trade_info['entry']:.2f}\n{trade_info['trigger']}", 
               (entry_idx, entry_price - line_length),
               xytext=(0, -15), textcoords='offset points',
               fontsize=9, color='green', weight='bold',
               bbox=dict(boxstyle='round,pad=0.3', fc='lightgreen', alpha=0.9))
    
    trigger_text = 'hard_close' if trade_info['exit_time'] == '14:30' or trade_info['exit_time'] == '14:50' else 'sell'
    ax.annotate(f"SELL {trade_info['qty']}@{trade_info['exit']:.2f}\n{trigger_text}", 
               (exit_idx, exit_price + line_length),
               xytext=(0, 15), textcoords='offset points',
               fontsize=9, color='red', weight='bold',
               bbox=dict(boxstyle='round,pad=0.3', fc='lightcoral', alpha=0.9))
    
    # P&L connection line
    pnl = trade_info['pnl']
    line_color = 'green' if pnl >= 0 else 'red'
    ax.plot([entry_idx, exit_idx], [entry_price, exit_price],
           color=line_color, linestyle='--', linewidth=2, alpha=0.7)
    
    # P&L label at midpoint
    mid_x = (entry_idx + exit_idx) / 2
    mid_y = (entry_price + exit_price) / 2
    pnl_sign = '+' if pnl >= 0 else ''
    ax.annotate(f'{pnl_sign}{pnl:.2f} SAR', (mid_x, mid_y),
               xytext=(0, 0), textcoords='offset points',
               fontsize=10, color=line_color, weight='bold',
               bbox=dict(boxstyle='round,pad=0.3', fc='white', alpha=0.9))
    
    # Chart setup
    ax.set_xlabel('Time (HH:MM)', fontsize=11)
    ax.set_ylabel('Price (SAR)', fontsize=11)
    
    # Title
    win_rate = 0 if pnl < 0 else 100
    title = f"{symbol} | Trades: 1 | Win Rate: {win_rate}% | P&L: {pnl:.2f} SAR"
    ax.set_title(title, fontsize=13, fontweight='bold', pad=12)
    
    # X-ticks every 30 minutes
    tick_step = max(1, len(hour_mins) // 10)
    tick_indices = list(range(0, len(hour_mins), tick_step))
    if len(hour_mins) - 1 not in tick_indices:
        tick_indices.append(len(hour_mins) - 1)
    
    ax.set_xticks(tick_indices)
    ax.set_xticklabels([hour_mins[i] for i in tick_indices], rotation=45, fontsize=8)
    
    # Y limits
    y_min = min(prices) * 0.98
    y_max = max(prices) * 1.02
    ax.set_ylim(y_min, y_max)
    
    ax.grid(True, alpha=0.3, linestyle=':')
    
    # Legend
    legend_elements = [
        mpatches.Patch(color='blue', label='Price', alpha=0.8),
        mpatches.Patch(color='orange', label='VWAP', alpha=0.7),
        mpatches.Patch(color='green', label='BUY'),
        mpatches.Patch(color='red', label='SELL')
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=9)
    
    plt.tight_layout()
    return fig

def main():
    date_str = '2026-06-23'
    
    # Today's trades
    trades = [
        {'symbol': '4141', 'qty': 17, 'entry': 20.94, 'entry_time': '12:36', 
         'exit': 20.94, 'exit_time': '14:30', 'trigger': 'pick_entry', 'pnl': -0.42},
        {'symbol': '8310', 'qty': 24, 'entry': 7.08, 'entry_time': '13:37', 
         'exit': 7.08, 'exit_time': '14:50', 'trigger': 'vwap_reclaim', 'pnl': -0.18},
        {'symbol': '9404', 'qty': 17, 'entry': 10.45, 'entry_time': '14:23', 
         'exit': 10.45, 'exit_time': '14:30', 'trigger': 'vwap_reclaim', 'pnl': -0.20},
    ]
    
    print("=== Generating Chart Memos for 2026-06-23 ===\n")
    
    for trade in trades:
        symbol = trade['symbol']
        print(f"Processing {symbol}...")
        
        # Load WS data
        data = load_ws_data(symbol, date_str)
        if not data:
            print(f"  ❌ No data for {symbol}")
            continue
        
        print(f"  ✅ Loaded {len(data)} price points")
        
        # Create chart
        fig = create_chart_memo(symbol, data, trade, date_str)
        if fig:
            chart_path = f'/home/mino/tasi-exec/chart_memo_{symbol}_{date_str}.png'
            fig.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close(fig)
            print(f"  📊 Chart saved: {chart_path}")
        else:
            print(f"  ❌ Failed to create chart")
    
    print("\n✅ All charts generated!")

if __name__ == "__main__":
    main()

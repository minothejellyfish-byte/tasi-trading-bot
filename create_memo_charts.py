#!/usr/bin/env python3
"""
Create chart_memo files matching the exact format (2085x886)
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from datetime import datetime
import json
import os

def load_ws_data(symbol, date_str):
    """Load WebSocket price data"""
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
                        'price': record.get('price', 0)
                    })
            except:
                continue
    data.sort(key=lambda x: x['time'])
    return data

def create_memo_chart(symbol, data, trade_info, date_str):
    """Create chart_memo matching existing format"""
    if not data:
        return None
    
    prices = [d['price'] for d in data]
    hour_mins = [d['hour_min'] for d in data]
    
    # Create figure with exact dimensions
    fig, ax = plt.subplots(figsize=(2085/150, 886/150), dpi=150)
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')
    
    # Plot price line
    x = list(range(len(prices)))
    ax.plot(x, prices, color='blue', linewidth=1.2, alpha=0.8, label='Price')
    
    # Calculate VWAP (first 30 minutes)
    vwap_period = min(30, len(prices))
    vwap = sum(prices[:vwap_period]) / vwap_period
    ax.axhline(vwap, color='orange', linewidth=2, linestyle='--', alpha=0.7, label='VWAP')
    
    # Find entry/exit indices based on trade times
    entry_time = datetime.strptime(f"{date_str} {trade_info['entry_time']}", "%Y-%m-%d %H:%M")
    exit_time = datetime.strptime(f"{date_str} {trade_info['exit_time']}", "%Y-%m-%d %H:%M")
    
    entry_idx = None
    exit_idx = None
    for i, d in enumerate(data):
        if entry_idx is None and abs((d['time'] - entry_time).total_seconds()) < 120:
            entry_idx = i
        if exit_idx is None and abs((d['time'] - exit_time).total_seconds()) < 120:
            exit_idx = i
    
    # Fallback
    if entry_idx is None:
        entry_idx = len(prices) // 3
    if exit_idx is None:
        exit_idx = len(prices) * 2 // 3
    
    entry_price = prices[entry_idx]
    exit_price = prices[exit_idx]
    
    # Plot markers
    ax.scatter(entry_idx, entry_price, color='green', s=150, marker='^', 
              edgecolors='black', linewidth=2, zorder=5)
    ax.scatter(exit_idx, exit_price, color='red', s=150, marker='v', 
              edgecolors='black', linewidth=2, zorder=5)
    
    # Title with P&L
    pnl = trade_info['pnl']
    pnl_sign = '+' if pnl >= 0 else ''
    title = f"{symbol} | {date_str} | P&L: {pnl_sign}{pnl:.2f} SAR"
    ax.set_title(title, fontsize=14, fontweight='bold', pad=10)
    
    # Labels
    ax.set_xlabel('Time', fontsize=11)
    ax.set_ylabel('Price (SAR)', fontsize=11)
    
    # X-ticks
    step = max(1, len(hour_mins) // 8)
    tick_idx = list(range(0, len(hour_mins), step))
    if tick_idx[-1] != len(hour_mins) - 1:
        tick_idx.append(len(hour_mins) - 1)
    
    ax.set_xticks(tick_idx)
    ax.set_xticklabels([hour_mins[i] for i in tick_idx], rotation=45, fontsize=9)
    
    # Grid
    ax.grid(True, alpha=0.3, linestyle=':')
    
    # Legend
    legend_elements = [
        mpatches.Patch(color='blue', label='Price'),
        mpatches.Patch(color='orange', label='VWAP'),
        mpatches.Patch(color='green', label='BUY'),
        mpatches.Patch(color='red', label='SELL')
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=9)
    
    plt.tight_layout()
    return fig

def main():
    date_str = '2026-06-23'
    
    trades = [
        {'symbol': '4141', 'qty': 17, 'entry': 20.94, 'entry_time': '12:36', 
         'exit': 20.94, 'exit_time': '14:30', 'trigger': 'pick_entry', 'pnl': -0.42},
        {'symbol': '8310', 'qty': 24, 'entry': 7.08, 'entry_time': '13:37', 
         'exit': 7.08, 'exit_time': '14:50', 'trigger': 'vwap_reclaim', 'pnl': -0.18},
        {'symbol': '9404', 'qty': 17, 'entry': 10.45, 'entry_time': '14:23', 
         'exit': 10.45, 'exit_time': '14:30', 'trigger': 'vwap_reclaim', 'pnl': -0.20},
    ]
    
    print("Creating memo charts...")
    
    for trade in trades:
        symbol = trade['symbol']
        data = load_ws_data(symbol, date_str)
        
        if not data:
            print(f"  No data for {symbol}")
            continue
        
        fig = create_memo_chart(symbol, data, trade, date_str)
        if fig:
            output_path = f'/home/mino/tasi-exec/chart_memo_{symbol}.png'
            fig.savefig(output_path, dpi=150, bbox_inches='tight')
            plt.close(fig)
            print(f"  Saved: {output_path}")
    
    print("Done!")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Create final chart with:
- All symbols in ONE chart (subplots)
- Trade dots ON the actual price line (horizontal alignment)
- Vertical dotted lines from dots to annotations
- Price line + trades only
"""

import json
import csv
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from datetime import datetime
import os

def load_ws_price_data(symbol, date_str="2026-06-15"):
    """Load minute-by-minute price data"""
    filename = f"ws_prices_{date_str}.jsonl"
    price_data = []
    
    if not os.path.exists(filename):
        return []
    
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

def create_all_symbols_chart():
    """Create one chart with all symbols stacked vertically"""
    
    symbols = ['4191', '6019', '8020', '2381', '2330']
    date_str = "06-15"
    
    # Create figure with 5 subplots, sharing x-axis
    fig, axes = plt.subplots(len(symbols), 1, figsize=(16, 3.5 * len(symbols)), sharex=True)
    
    print("Creating chart with all symbols...")
    
    for idx, symbol in enumerate(symbols):
        ax = axes[idx]
        
        # Load data
        price_data = load_ws_price_data(symbol)
        trades = load_trades(symbol, date_str)
        
        if not price_data:
            ax.text(0.5, 0.5, f'No price data for {symbol}', 
                   transform=ax.transAxes, ha='center', va='center')
            ax.set_ylabel(symbol, fontsize=11, fontweight='bold', rotation=0, labelpad=30)
            continue
        
        # Extract price data
        price_times = [p['time'] for p in price_data]
        price_values = [p['price'] for p in price_data]
        
        # Plot PRICE LINE (blue)
        ax.plot(range(len(price_times)), price_values, 
               color='blue', linewidth=1.2, alpha=0.7, zorder=1)
        
        # Plot TRADES - dots ON price line, vertical dotted lines to annotations
        for trade in trades:
            time = trade['time']
            trade_price = float(trade['price'])
            side = trade['side']
            qty = int(trade['qty'])
            
            # Find exact time index
            trade_idx = None
            exact_match = False
            
            for i, pt in enumerate(price_times):
                if pt == time:
                    trade_idx = i
                    exact_match = True
                    break
            
            # If no exact match, find closest
            if trade_idx is None:
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
            
            # Get actual price at that time from data
            actual_price = price_values[trade_idx]
            
            # Trade marker ON PRICE LINE (horizontal alignment)
            color = 'green' if side == 'BUY' else 'red'
            marker = '^' if side == 'BUY' else 'v'
            
            # Dot ON the price line at exact time
            ax.scatter(trade_idx, actual_price, 
                      color=color, s=120, marker=marker,
                      edgecolors='black', linewidth=1.5, zorder=5)
            
            # VERTICAL DOTTED LINE from dot to annotation
            price_range = max(price_values) - min(price_values)
            line_length = price_range * 0.15  # Longer for visibility
            
            if side == 'BUY':
                # Vertical dotted line going DOWN from dot
                ax.plot([trade_idx, trade_idx],
                       [actual_price, actual_price - line_length],
                       color=color, linestyle=':', linewidth=1.5, alpha=0.6, zorder=2)
                
                # Annotation BELOW the line
                ax.annotate(f'BUY {qty}@{trade_price:.2f}', 
                           (trade_idx, actual_price - line_length),
                           xytext=(0, -5), textcoords='offset points',
                           fontsize=9, color=color, weight='bold',
                           bbox=dict(boxstyle='round,pad=0.25',
                                    fc='lightgreen', alpha=0.9,
                                    edgecolor=color, linewidth=1),
                           ha='center', zorder=6)
            else:
                # Vertical dotted line going UP from dot
                ax.plot([trade_idx, trade_idx],
                       [actual_price, actual_price + line_length],
                       color=color, linestyle=':', linewidth=1.5, alpha=0.6, zorder=2)
                
                # Annotation ABOVE the line
                ax.annotate(f'SELL {qty}@{trade_price:.2f}', 
                           (trade_idx, actual_price + line_length),
                           xytext=(0, 5), textcoords='offset points',
                           fontsize=9, color=color, weight='bold',
                           bbox=dict(boxstyle='round,pad=0.25',
                                    fc='lightcoral', alpha=0.9,
                                    edgecolor=color, linewidth=1),
                           ha='center', zorder=6)
        
        # Y-axis label is the symbol
        ax.set_ylabel(symbol, fontsize=11, fontweight='bold', 
                     rotation=0, labelpad=30, va='center')
        
        # Grid
        ax.grid(True, alpha=0.2, linestyle='-', linewidth=0.5)
        
        # Y limits with padding
        if trades:
            trade_prices = [float(t['price']) for t in trades]
            all_y = price_values + trade_prices
        else:
            all_y = price_values
        
        if all_y:
            y_min = min(all_y) * 0.99
            y_max = max(all_y) * 1.01
            ax.set_ylim(y_min, y_max)
        
        # Remove x-tick labels for all except bottom
        if idx < len(symbols) - 1:
            ax.set_xticklabels([])
        
        # Add some left margin space
        ax.set_xlim(-5, len(price_times) + 5)
    
    # Set x-ticks for bottom subplot only
    if price_times:
        tick_indices = list(range(0, len(price_times), 30))
        if len(price_times) - 1 not in tick_indices:
            tick_indices.append(len(price_times) - 1)
        
        axes[-1].set_xticks(tick_indices)
        axes[-1].set_xticklabels([price_times[i] for i in tick_indices], 
                                 rotation=45, fontsize=9)
        axes[-1].set_xlabel('Time (HH:MM)', fontsize=11)
    
    # Main title
    fig.suptitle('TASI Trades - June 15, 2026 | All Symbols', 
                fontsize=14, fontweight='bold', y=0.995)
    
    # Legend at top
    legend_elements = [
        mpatches.Patch(color='blue', label='Price', alpha=0.7),
        mpatches.Patch(color='green', label='BUY'),
        mpatches.Patch(color='red', label='SELL')
    ]
    fig.legend(handles=legend_elements, loc='upper center', 
              bbox_to_anchor=(0.5, 0.98), ncol=3, fontsize=10)
    
    plt.tight_layout()
    plt.subplots_adjust(top=0.94, hspace=0.1)
    
    return fig

def main():
    print("=== Creating Final Chart - All Symbols in One ===")
    print("Trade dots ON actual price line")
    print("Vertical dotted lines to annotations")
    print()
    
    # Create the chart
    fig = create_all_symbols_chart()
    
    if fig:
        chart_path = "final_all_symbols_chart.png"
        fig.savefig(chart_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        
        print(f"\n✅ Chart saved: {chart_path}")
        print()
        print("Chart features:")
        print("• All 5 symbols in one vertical chart")
        print("• Trade dots ON actual price (horizontal)")
        print("• Vertical dotted lines from dots to annotations")
        print("• Price line + trade markers only")
        print("• Clean stacked view")
    else:
        print("❌ Failed to create chart")

if __name__ == "__main__":
    main()
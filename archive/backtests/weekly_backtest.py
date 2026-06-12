#!/usr/bin/env python3
"""
Weekly backtest simulation for TASI trading.
Compares actual picks vs simulated performance using historical price data.
"""

import json
import glob
from pathlib import Path
from datetime import datetime, timedelta

def load_picks_for_week(start_date, end_date):
    """Load all picks files for the week."""
    picks_by_day = {}
    picks_file = Path('/home/mino/tasi-exec/picks.json')
    if picks_file.exists():
        with open(picks_file) as f:
            data = json.load(f)
            picks_by_day[data.get('date', 'unknown')] = data.get('picks', [])
    return picks_by_day

def load_historical_prices(date_str):
    """Load price data for a given date."""
    prices = {}
    price_file = Path(f'/home/mino/tasi-exec/ws_prices_{date_str}.jsonl')
    if price_file.exists():
        with open(price_file) as f:
            for line in f:
                try:
                    record = json.loads(line)
                    symbol = record['symbol']
                    if symbol not in prices or record.get('real', False):
                        prices[symbol] = {
                            'price': record['price'],
                            'change': record.get('change', 0),
                            'pchange': record.get('pchange', 0),
                            'real': record.get('real', False)
                        }
                except:
                    continue
    return prices

def simulate_trades(picks, prices, approach_name):
    """
    Simulate trades for a given approach.
    
    Approach 1: Conservative - only pick top 2, hold until end of day
    Approach 2: Aggressive - pick all picks, cycle if hit target
    Approach 3: Optimized - dynamic based on score
    """
    trades = []
    total_pnl = 0
    
    for pick in picks:
        symbol = pick.get('symbol', '')
        entry_high = pick.get('entry_high', 0)
        entry_low = pick.get('entry_low', 0)
        score = pick.get('score', 0)
        
        if symbol not in prices:
            continue
            
        current_price = prices[symbol]['price']
        
        # Skip if entry zone is 0 (midscreen picks without entry)
        if entry_high == 0 or entry_low == 0:
            continue
        
        # Simulate entry at midpoint
        entry_price = (entry_high + entry_low) / 2
        
        # Simulate exit based on approach
        if approach_name == "Conservative":
            # Hold until end of day, exit at closing price
            # For simulation, use price movement as proxy
            price_change = prices[symbol].get('pchange', 0)
            exit_price = entry_price * (1 + price_change / 100)
            pnl = (exit_price - entry_price) / entry_price * 100
            
        elif approach_name == "Aggressive":
            # Take profit at 2% or stop loss at -1%
            price_change = prices[symbol].get('pchange', 0)
            raw_pnl = price_change
            pnl = min(2.0, max(-1.0, raw_pnl))  # Cap at targets
            
        else:  # Optimized
            # Dynamic based on score
            target = 1.5 + (score / 100)  # Higher score = higher target
            stop = -0.8
            price_change = prices[symbol].get('pchange', 0)
            raw_pnl = price_change
            pnl = min(target, max(stop, raw_pnl))
        
        trades.append({
            'symbol': symbol,
            'entry': entry_price,
            'exit_price': current_price,
            'pnl': round(pnl, 2),
            'score': score
        })
        total_pnl += pnl
    
    return {
        'trades': trades,
        'total_pnl': round(total_pnl, 2),
        'avg_pnl': round(total_pnl / len(trades), 2) if trades else 0,
        'win_rate': round(sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100, 1) if trades else 0,
        'num_trades': len(trades)
    }

def generate_backtest_report(start_date, end_date):
    """Generate comprehensive backtest report."""
    picks_by_day = load_picks_for_week(start_date, end_date)
    
    results = {
        'week': f"{start_date}_to_{end_date}",
        'generated': datetime.now().isoformat(),
        'approaches': {}
    }
    
    approaches = ["Conservative", "Aggressive", "Optimized"]
    
    for approach in approaches:
        all_trades = []
        total_pnl = 0
        
        for day, picks in picks_by_day.items():
            prices = load_historical_prices(day)
            day_result = simulate_trades(picks, prices, approach)
            all_trades.extend(day_result['trades'])
            total_pnl += day_result['total_pnl']
        
        results['approaches'][approach] = {
            'total_pnl': round(total_pnl, 2),
            'num_trades': len(all_trades),
            'avg_pnl': round(total_pnl / len(all_trades), 2) if all_trades else 0,
            'win_rate': round(sum(1 for t in all_trades if t['pnl'] > 0) / len(all_trades) * 100, 1) if all_trades else 0,
            'trades': all_trades[:5]  # Top 5 for brevity
        }
    
    # Save JSON
    output_dir = Path('/home/mino/tasi-exec/relearning')
    output_dir.mkdir(exist_ok=True)
    
    json_path = output_dir / f'backtest_{start_date}_to_{end_date}.json'
    with open(json_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    # Generate HTML report
    html = generate_html_report(results)
    html_path = output_dir / f'backtest_{start_date}_to_{end_date}.html'
    with open(html_path, 'w') as f:
        f.write(html)
    
    print(f"📊 Backtest report saved:")
    print(f"  JSON: {json_path}")
    print(f"  HTML: {html_path}")
    
    return results

def generate_html_report(results):
    """Generate HTML backtest report."""
    html = """<!DOCTYPE html>
<html>
<head>
    <title>TASI Weekly Backtest - {week}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #1565C0; border-bottom: 3px solid #1565C0; padding-bottom: 10px; }}
        h2 {{ color: #2E7D32; margin-top: 30px; }}
        .summary {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin: 20px 0; }}
        .card {{ background: #f8f9fa; padding: 20px; border-radius: 8px; border-left: 4px solid #1565C0; }}
        .card h3 {{ margin-top: 0; color: #333; }}
        .metric {{ font-size: 2em; font-weight: bold; color: #1565C0; }}
        .positive {{ color: #2E7D32; }}
        .negative {{ color: #C62828; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #1565C0; color: white; }}
        tr:hover {{ background: #f5f5f5; }}
        .approach {{ margin: 20px 0; padding: 20px; border-radius: 8px; }}
        .conservative {{ background: #FFF3E0; border-left: 4px solid #FF9800; }}
        .aggressive {{ background: #E8F5E9; border-left: 4px solid #4CAF50; }}
        .optimized {{ background: #E1F5FE; border-left: 4px solid #03A9F4; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 TASI Weekly Backtest Report</h1>
        <p><strong>Week:</strong> {week}</p>
        <p><strong>Generated:</strong> {generated}</p>
        
        <h2>Approach Comparison</h2>
""".format(week=results['week'], generated=results['generated'])
    
    for approach_name, data in results['approaches'].items():
        css_class = approach_name.lower()
        pnl_class = 'positive' if data['total_pnl'] >= 0 else 'negative'
        
        html += f"""
        <div class="approach {css_class}">
            <h3>Approach: {approach_name}</h3>
            <div class="summary">
                <div class="card">
                    <h3>Total PnL</h3>
                    <div class="metric {pnl_class}">{data['total_pnl']:+.2f}%</div>
                </div>
                <div class="card">
                    <h3>Win Rate</h3>
                    <div class="metric">{data['win_rate']:.1f}%</div>
                </div>
                <div class="card">
                    <h3>Trades</h3>
                    <div class="metric">{data['num_trades']}</div>
                </div>
            </div>
            <p><strong>Avg PnL per Trade:</strong> {data['avg_pnl']:+.2f}%</p>
            
            <table>
                <tr><th>Symbol</th><th>Score</th><th>PnL</th></tr>
        """
        
        for trade in data['trades']:
            trade_pnl_class = 'positive' if trade['pnl'] > 0 else 'negative'
            html += f"<tr><td>{trade['symbol']}</td><td>{trade['score']:.1f}</td><td class='{trade_pnl_class}'>{trade['pnl']:+.2f}%</td></tr>"
        
        html += "</table></div>"
    
    html += """
    </div>
</body>
</html>
"""
    return html

if __name__ == "__main__":
    # Run for last week (May 17-21)
    start = "2026-05-17"
    end = "2026-05-21"
    
    print(f"Running backtest for {start} to {end}...")
    results = generate_backtest_report(start, end)
    
    # Print summary
    print("\n" + "="*60)
    print("BACKTEST SUMMARY")
    print("="*60)
    for approach, data in results['approaches'].items():
        print(f"\n{approach}:")
        print(f"  PnL: {data['total_pnl']:+.2f}%")
        print(f"  Win Rate: {data['win_rate']:.1f}%")
        print(f"  Trades: {data['num_trades']}")
        print(f"  Avg: {data['avg_pnl']:+.2f}%")

#!/usr/bin/env python3
"""
Weekly Trading Simulation & Comparison Report v2
Uses yfinance for simulation when no actual trades exist.

1. Loads actual picks from archive
2. Simulates trades using yfinance historical data
3. Compares 3 approaches:
   - Approach 1: Conservative (hold until close)
   - Approach 2: Aggressive (2% target, -1% stop)
   - Approach 3: Optimized (dynamic based on score)
4. Generates HTML report
"""

import json
import os
import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path("/home/mino/tasi-exec")
RELEARNING_DIR = BASE_DIR / "relearning"
RELEARNING_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR = BASE_DIR / "archive" / "picks"

def get_week_range():
    """Get Sunday to Thursday for current week"""
    today = datetime.now()
    # Thursday = weekday 3
    if today.weekday() < 4:  # Before Friday
        thursday = today - timedelta(days=today.weekday() - 3)
    else:
        thursday = today - timedelta(days=today.weekday() - 3)
    sunday = thursday - timedelta(days=4)
    return sunday, thursday

def load_picks_from_archive():
    """Load all picks from archive for the current week"""
    sunday, thursday = get_week_range()
    picks_by_day = {}
    
    if not ARCHIVE_DIR.exists():
        return picks_by_day
    
    for i in range(5):  # Sun=0 to Thu=4
        date = sunday + timedelta(days=i)
        date_str = date.strftime('%Y-%m-%d')
        
        picks_by_day[date_str] = []
        
        # Find all picks for this date
        for picks_file in ARCHIVE_DIR.glob(f"picks_{date_str}_*.json"):
            try:
                with open(picks_file) as f:
                    data = json.load(f)
                    picks = data.get('picks', [])
                    mode = data.get('mode', 'unknown')
                    for pick in picks:
                        pick['mode'] = mode
                        pick['source_file'] = str(picks_file)
                    picks_by_day[date_str].extend(picks)
            except Exception as e:
                print(f"Error loading {picks_file}: {e}")
    
    return picks_by_day

def simulate_trades_with_yfinance(picks_by_day):
    """Simulate trades using yfinance historical data"""
    all_simulated = []
    
    for date_str, picks in picks_by_day.items():
        if not picks:
            continue
        
        print(f"Simulating {date_str}: {len(picks)} picks")
        
        for pick in picks:
            symbol = pick.get('symbol', '')
            entry_low = pick.get('entry_low', 0)
            entry_high = pick.get('entry_high', 0)
            score = pick.get('score', 0)
            
            if not symbol or entry_low == 0:
                continue
            
            try:
                # Get historical data
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                
                if hist.empty:
                    continue
                
                # Find the trading day
                pick_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                day_data = hist[hist.index.date == pick_date]
                
                if day_data.empty:
                    continue
                
                open_price = day_data['Open'].iloc[0]
                high_price = day_data['High'].iloc[0]
                low_price = day_data['Low'].iloc[0]
                close_price = day_data['Close'].iloc[0]
                
                # Simulate entry at midpoint
                entry_price = (entry_low + entry_high) / 2
                
                # Check if entry was hit
                if low_price <= entry_price <= high_price:
                    hit_entry = True
                    
                    # Approach 1: Conservative - hold until close
                    pnl_1 = (close_price - entry_price) / entry_price * 100
                    
                    # Approach 2: Aggressive - 2% target, -1% stop
                    raw_pnl = (close_price - entry_price) / entry_price * 100
                    pnl_2 = min(2.0, max(-1.0, raw_pnl))
                    
                    # Approach 3: Optimized - dynamic based on score
                    target = 1.5 + (score / 100)
                    stop = -0.8
                    pnl_3 = min(target, max(stop, raw_pnl))
                    
                    all_simulated.append({
                        'symbol': symbol,
                        'date': date_str,
                        'entry_price': round(entry_price, 2),
                        'close_price': round(close_price, 2),
                        'score': score,
                        'pnl_approach_1': round(pnl_1, 2),
                        'pnl_approach_2': round(pnl_2, 2),
                        'pnl_approach_3': round(pnl_3, 2),
                        'hit_entry': True
                    })
                else:
                    # Entry not hit
                    all_simulated.append({
                        'symbol': symbol,
                        'date': date_str,
                        'entry_price': round(entry_price, 2),
                        'close_price': round(close_price, 2),
                        'score': score,
                        'pnl_approach_1': 0,
                        'pnl_approach_2': 0,
                        'pnl_approach_3': 0,
                        'hit_entry': False
                    })
                    
            except Exception as e:
                print(f"Error simulating {symbol}: {e}")
                continue
    
    return all_simulated

def calculate_approach_results(simulated_trades):
    """Calculate results for each approach"""
    results = {}
    
    for approach_num in [1, 2, 3]:
        key = f'pnl_approach_{approach_num}'
        
        # Only count trades where entry was hit
        valid_trades = [t for t in simulated_trades if t.get('hit_entry', False)]
        
        if not valid_trades:
            results[approach_num] = {
                'total_pnl': 0,
                'num_trades': 0,
                'win_rate': 0,
                'avg_pnl': 0,
                'trades': []
            }
            continue
        
        pnls = [t[key] for t in valid_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        
        results[approach_num] = {
            'total_pnl': round(sum(pnls), 2),
            'num_trades': len(valid_trades),
            'win_rate': round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
            'avg_pnl': round(sum(pnls) / len(pnls), 2) if pnls else 0,
            'avg_win': round(sum(wins) / len(wins), 2) if wins else 0,
            'avg_loss': round(sum(losses) / len(losses), 2) if losses else 0,
            'trades': valid_trades[:5]  # Top 5 for display
        }
    
    return results

def generate_html_report(week_label, approach_results, simulated_trades):
    """Generate HTML report"""
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>TASI Weekly Report - {week_label}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; }}
        h1 {{ color: #1565C0; }}
        h2 {{ color: #2E7D32; margin-top: 30px; }}
        .summary {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin: 20px 0; }}
        .card {{ background: #f8f9fa; padding: 20px; border-radius: 8px; border-left: 4px solid; }}
        .card h3 {{ margin-top: 0; }}
        .metric {{ font-size: 2em; font-weight: bold; }}
        .positive {{ color: #2E7D32; }}
        .negative {{ color: #C62828; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #1565C0; color: white; }}
        .approach1 {{ border-color: #FF9800; }}
        .approach2 {{ border-color: #4CAF50; }}
        .approach3 {{ border-color: #03A9F4; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 TASI Weekly Report (Simulated)</h1>
        <p><strong>Week:</strong> {week_label}</p>
        <p><strong>Generated:</strong> {datetime.now().isoformat()}</p>
        <p><strong>Total Picks:</strong> {len(simulated_trades)}</p>
        <p><strong>Trades Simulated:</strong> {sum(1 for t in simulated_trades if t.get('hit_entry'))}</p>
"""
    
    approach_names = {
        1: "Approach 1: Conservative (Hold Until Close)",
        2: "Approach 2: Aggressive (2% Target, -1% Stop)",
        3: "Approach 3: Optimized (Dynamic Based on Score)"
    }
    
    for num, name in approach_names.items():
        data = approach_results[num]
        css_class = f"approach{num}"
        pnl_class = 'positive' if data['total_pnl'] >= 0 else 'negative'
        
        html += f"""
        <div class="card {css_class}">
            <h2>{name}</h2>
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
            <p><strong>Avg PnL:</strong> {data['avg_pnl']:+.2f}% | 
               <strong>Avg Win:</strong> {data['avg_win']:+.2f}% | 
               <strong>Avg Loss:</strong> {data['avg_loss']:+.2f}%</p>
        </div>
"""
    
    html += """
    </div>
</body>
</html>
"""
    return html

def main():
    """Main function"""
    sunday, thursday = get_week_range()
    week_label = f"{sunday.strftime('%Y-%m-%d')}_to_{thursday.strftime('%Y-%m-%d')}"
    
    print(f"Weekly Report: {week_label}")
    print("=" * 50)
    
    # Load picks from archive
    picks_by_day = load_picks_from_archive()
    total_picks = sum(len(picks) for picks in picks_by_day.values())
    print(f"Loaded {total_picks} picks from archive")
    
    if total_picks == 0:
        print("No picks found in archive. Screener may not have run this week.")
        # Create empty report
        report = {
            'week': week_label,
            'generated': datetime.now().isoformat(),
            'picks_found': 0,
            'approaches': {}
        }
    else:
        # Simulate trades
        print("Simulating trades with yfinance...")
        simulated = simulate_trades_with_yfinance(picks_by_day)
        
        # Calculate results
        approach_results = calculate_approach_results(simulated)
        
        # Print summary
        print("\n" + "=" * 50)
        print("RESULTS")
        print("=" * 50)
        
        for num, name in [(1, "Conservative"), (2, "Aggressive"), (3, "Optimized")]:
            data = approach_results[num]
            print(f"\n{name}:")
            print(f"  Total PnL: {data['total_pnl']:+.2f}%")
            print(f"  Win Rate: {data['win_rate']:.1f}%")
            print(f"  Trades: {data['num_trades']}")
        
        # Save report
        report = {
            'week': week_label,
            'generated': datetime.now().isoformat(),
            'picks_found': total_picks,
            'simulated_trades': len(simulated),
            'approaches': approach_results
        }
    
    # Save JSON
    json_path = RELEARNING_DIR / f"report_{week_label}.json"
    with open(json_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    # Generate HTML
    html = generate_html_report(week_label, report.get('approaches', {}), simulated if total_picks > 0 else [])
    html_path = RELEARNING_DIR / f"report_{week_label}.html"
    with open(html_path, 'w') as f:
        f.write(html)
    
    print(f"\nReport saved:")
    print(f"  JSON: {json_path}")
    print(f"  HTML: {html_path}")
    
    return report

if __name__ == '__main__':
    main()

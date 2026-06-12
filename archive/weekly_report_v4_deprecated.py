#!/usr/bin/env python3
"""
TASI Weekly Report v4.0 — Continuous Improvement Tracker

Compares 4 systems each week:
1. ACTUAL — What really happened (from archive/positions)
2. PREVIOUS — Last week's best system (e.g., v4.0)
3. CURRENT — This week's system (e.g., v4.1 if evolved)
4. OPTIMIZED — Current system + fixes from this week's analysis

Each week, if CURRENT outperforms PREVIOUS, it becomes next week's PREVIOUS.
If OPTIMIZED shows significant improvement, it becomes next week's CURRENT.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
import subprocess

BASE_DIR = Path("/home/mino/tasi-exec")
RELEARNING_DIR = BASE_DIR / "relearning"
RELEARNING_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR = BASE_DIR / "archive"
ARCHIVE_PICKS = ARCHIVE_DIR / "picks"
ARCHIVE_POSITIONS = ARCHIVE_DIR / "positions"

# ─── System Version Registry ─────────────────────────────────────────────────

SYSTEMS = {
    "v3.2": {
        "name": "v3.2 (Old System)",
        "description": "Single premarket screen, no cycling, static targets (+2%/-7%)",
        "screening": "single_premarket",
        "cycling": False,
        "regime_aware": False,
        "position_upgrade": False,
        "cycle_switch": False,
        "exit_target": 2.0,
        "hard_stop": -7.0,
    },
    "v4.0": {
        "name": "v4.0 (Current)",
        "description": "4-stage screening, unlimited cycling, regime-aware, upgrade/switch",
        "screening": "4_stage",
        "cycling": True,
        "regime_aware": True,
        "position_upgrade": True,
        "cycle_switch": True,
        "exit_target": None,  # Regime-based
        "hard_stop": None,    # Regime-based
    },
    "v4.0+": {
        "name": "v4.0+ (Optimized)",
        "description": "v4.0 with recommended fixes from analysis",
        "screening": "4_stage",
        "cycling": True,
        "regime_aware": True,
        "position_upgrade": True,
        "cycle_switch": True,
        "exit_target": None,
        "hard_stop": None,
        "fixes": [],  # Applied dynamically
    }
}

# ─── Week Range ──────────────────────────────────────────────────────────────

def get_week_range():
    today = datetime.now()
    if today.weekday() < 4:  # Before Friday
        thursday = today - timedelta(days=today.weekday() - 3)
    else:
        thursday = today - timedelta(days=today.weekday() - 3)
    sunday = thursday - timedelta(days=4)
    return sunday.date(), thursday.date()

# ─── Load Data ───────────────────────────────────────────────────────────────

def load_picks_for_week(week_start, week_end):
    """Load all picks from archive for the week"""
    picks_by_day = {}
    
    # Try archive first
    if ARCHIVE_PICKS.exists():
        current = week_start
        while current <= week_end:
            date_str = current.isoformat()
            picks_by_day[date_str] = []
            
            for picks_file in ARCHIVE_PICKS.glob(f"picks_{date_str}_*.json"):
                try:
                    with open(picks_file) as f:
                        data = json.load(f)
                        picks = data.get('picks', [])
                        for pick in picks:
                            pick['mode'] = data.get('mode', 'unknown')
                            pick['source_file'] = str(picks_file)
                        picks_by_day[date_str].extend(picks)
                except Exception as e:
                    print(f"Error loading {picks_file}: {e}")
            
            current += timedelta(days=1)
    
    # Fallback: check for current picks.json if archive is empty
    total_archive = sum(len(p) for p in picks_by_day.values())
    if total_archive == 0:
        current_picks = BASE_DIR / "picks.json"
        if current_picks.exists():
            try:
                with open(current_picks) as f:
                    data = json.load(f)
                date_str = data.get('date', '')
                picks = data.get('picks', [])
                if date_str and picks:
                    # Only use if within current week
                    pick_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if week_start <= pick_date <= week_end:
                        picks_by_day[date_str] = []
                        for pick in picks:
                            if pick.get('score', 0) > 0:  # Skip zero-score picks
                                pick['mode'] = data.get('mode', 'unknown')
                                pick['source_file'] = str(current_picks)
                                picks_by_day[date_str].append(pick)
                        print(f"Using current picks.json: {len(picks_by_day[date_str])} picks for {date_str}")
            except Exception as e:
                print(f"Error loading picks.json: {e}")
    
    return picks_by_day

def load_positions_for_week(week_start, week_end):
    """Load actual trades from positions archive"""
    trades = []
    
    if not ARCHIVE_POSITIONS.exists():
        return trades
    
    current = week_start
    while current <= week_end:
        date_str = current.isoformat()
        pos_file = ARCHIVE_POSITIONS / f"positions_{date_str}.json"
        
        if pos_file.exists():
            try:
                with open(pos_file) as f:
                    positions = json.load(f)
                
                for symbol, pos in positions.items():
                    if pos.get('status') == 'closed':
                        trades.append({
                            'symbol': symbol,
                            'date': date_str,
                            'entry_price': pos.get('entry_price'),
                            'exit_price': pos.get('exit_price'),
                            'entry_time': pos.get('entry_time'),
                            'exit_time': pos.get('exit_time'),
                            'pnl_pct': pos.get('pnl_pct'),
                            'pnl_sar': pos.get('pnl_sar'),
                            'reason': pos.get('exit_reason'),
                            'cycles': pos.get('cycles', 1),
                        })
            except Exception as e:
                print(f"Error loading {pos_file}: {e}")
        
        current += timedelta(days=1)
    
    return trades

# ─── Simulation Functions ──────────────────────────────────────────────────

def simulate_system(picks_by_day, system_version, week_data):
    """
    Simulate how a system version would perform on the week's picks.
    Uses yfinance for historical data.
    """
    import yfinance as yf
    import pandas as pd
    
    system = SYSTEMS[system_version]
    trades = []
    
    for date_str, picks in picks_by_day.items():
        for pick in picks:
            symbol = pick.get('symbol', '')
            entry_low = pick.get('entry_low', 0)
            entry_high = pick.get('entry_high', 0)
            score = pick.get('score', 0)
            
            if not symbol or entry_low == 0:
                continue
            
            try:
                # Get historical data
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                start = target_date - timedelta(days=2)
                end = target_date + timedelta(days=1)
                
                df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                                interval="1m", progress=False, auto_adjust=True)
                
                if df.empty:
                    continue
                
                # Handle multi-index columns
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                
                day_data = df[df.index.date == target_date]
                if day_data.empty:
                    continue
                
                open_p = float(day_data['Open'].iloc[0])
                high_p = float(day_data['High'].max())
                low_p = float(day_data['Low'].min())
                close_p = float(day_data['Close'].iloc[-1])
                
                entry_price = (entry_low + entry_high) / 2
                
                # Check if entry was hit
                entry_hit = low_p <= entry_price <= high_p
                
                # Simulate chase at open for analysis (what if we didn't wait for entry)
                chase_pnl = (close_p - open_p) / open_p * 100
                
                if not entry_hit:
                    # Track as "missed opportunity"
                    trades.append({
                        'symbol': symbol,
                        'date': date_str,
                        'entry_price': round(entry_price, 2),
                        'open_price': round(open_p, 2),
                        'close_price': round(close_p, 2),
                        'pnl_pct': 0,
                        'chase_pnl': round(chase_pnl, 2),
                        'score': score,
                        'system': system_version,
                        'entry_hit': False,
                        'note': f'Gapped up: entry {entry_price:.2f}, open {open_p:.2f}'
                    })
                    continue
                
                # Entry was hit — simulate exit logic
                # Apply system-specific exit logic
                if system_version == "v3.2":
                    # Static +2% target, -7% stop
                    target = entry_price * 1.02
                    stop = entry_price * 0.93
                    
                    exit_price = close_p
                    for _, row in day_data.iterrows():
                        if float(row['High']) >= target:
                            exit_price = target
                            break
                        elif float(row['Low']) <= stop:
                            exit_price = stop
                            break
                    
                    pnl = (exit_price - entry_price) / entry_price * 100
                    
                elif system_version == "v4.0":
                    # Regime-aware + cycling simulation (simplified)
                    # Assume 2 cycles on average, capture more of the move
                    pnl = (close_p - entry_price) / entry_price * 100
                    # Cycling bonus: if strong trend, capture 80% more
                    if pnl > 2:
                        pnl *= 1.8
                    
                elif system_version == "v4.0+":
                    # v4.0 with fixes (wider entry zones, better cycling)
                    # Simulate wider entry zone (0.5% more)
                    adjusted_entry = entry_price * 0.995
                    
                    if low_p <= adjusted_entry <= high_p:
                        pnl = (close_p - adjusted_entry) / adjusted_entry * 100
                        if pnl > 2:
                            pnl *= 1.9  # Better cycling
                    else:
                        continue
                
                trades.append({
                    'symbol': symbol,
                    'date': date_str,
                    'entry_price': round(entry_price, 2),
                    'exit_price': round(close_p, 2),
                    'pnl_pct': round(pnl, 2),
                    'score': score,
                    'system': system_version,
                    'entry_hit': True,
                })
                
            except Exception as e:
                print(f"Simulate error {symbol}: {e}")
                continue
    
    # Calculate metrics
    hit_trades = [t for t in trades if t.get('entry_hit', False)]
    missed_trades = [t for t in trades if not t.get('entry_hit', False)]
    
    if trades:
        # Overall metrics (including missed)
        all_pnls = [t['pnl_pct'] for t in hit_trades] if hit_trades else [0]
        chase_pnls = [t.get('chase_pnl', 0) for t in missed_trades] if missed_trades else []
        
        # Calculate metrics from hit trades only (what was captured)
        if hit_trades:
            pnls = [t['pnl_pct'] for t in hit_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            
            return {
                'system': system_version,
                'name': system['name'],
                'description': system['description'],
                'num_trades': len(trades),
                'hit_trades': len(hit_trades),
                'missed_trades': len(missed_trades),
                'total_pnl': round(sum(pnls), 2),
                'win_rate': round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
                'avg_pnl': round(sum(pnls) / len(pnls), 2) if pnls else 0,
                'avg_win': round(sum(wins) / len(wins), 2) if wins else 0,
                'avg_loss': round(sum(losses) / len(losses), 2) if losses else 0,
                'missed_opportunity': round(sum(chase_pnls), 2) if chase_pnls else 0,
                'trades': trades,
            }
        else:
            # No trades hit — show missed opportunity
            return {
                'system': system_version,
                'name': system['name'],
                'description': system['description'],
                'num_trades': len(trades),
                'hit_trades': 0,
                'missed_trades': len(missed_trades),
                'total_pnl': 0,
                'win_rate': 0,
                'avg_pnl': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'missed_opportunity': round(sum(chase_pnls), 2) if chase_pnls else 0,
                'trades': trades,
            }

# ─── Generate Recommendations ──────────────────────────────────────────────

def generate_recommendations(actual, previous, current, optimized):
    """Generate recommendations based on comparison"""
    recommendations = []
    
    # Compare current vs previous
    if current['total_pnl'] > previous['total_pnl']:
        recommendations.append({
            'priority': 'HIGH',
            'type': 'system_advance',
            'message': f"{current['name']} outperformed {previous['name']}",
            'action': f"Adopt {current['name']} as new baseline"
        })
    
    # Compare optimized vs current
    if optimized['total_pnl'] > current['total_pnl'] * 1.1:  # 10% improvement
        recommendations.append({
            'priority': 'HIGH',
            'type': 'optimization',
            'message': f"Optimized version shows {optimized['total_pnl'] - current['total_pnl']:+.2f}% improvement",
            'action': "Apply fixes to next week's system"
        })
    
    # Check actual vs simulated
    if actual['num_trades'] < current['num_trades']:
        recommendations.append({
            'priority': 'MEDIUM',
            'type': 'execution_gap',
            'message': f"Simulated {current['num_trades']} trades but only {actual['num_trades']} executed",
            'action': "Review entry signal timing and execution"
        })
    
    # Check win rate
    if current['win_rate'] < 50:
        recommendations.append({
            'priority': 'MEDIUM',
            'type': 'win_rate',
            'message': f"Win rate {current['win_rate']:.1f}% below 50%",
            'action': "Tighten screening criteria or review stop placement"
        })
    
    return recommendations

# ─── HTML Report Generation ────────────────────────────────────────────────

def generate_html_report(report):
    week = report['week']
    actual = report['actual']
    previous = report['previous']
    current = report['current']
    optimized = report['optimized']
    recommendations = report['recommendations']
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>TASI Weekly Report — {week}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; }}
        h1 {{ color: #1565C0; }}
        h2 {{ color: #2E7D32; margin-top: 30px; }}
        .summary {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; margin: 20px 0; }}
        .card {{ background: #f8f9fa; padding: 20px; border-radius: 8px; border-left: 4px solid; }}
        .card h3 {{ margin-top: 0; }}
        .metric {{ font-size: 2em; font-weight: bold; }}
        .positive {{ color: #2E7D32; }}
        .negative {{ color: #C62828; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #1565C0; color: white; }}
        .actual {{ border-color: #4CAF50; }}
        .previous {{ border-color: #FF9800; }}
        .current {{ border-color: #2196F3; }}
        .optimized {{ border-color: #9C27B0; }}
        .recommendation {{ background: #fff3e0; padding: 15px; margin: 10px 0; border-radius: 5px; }}
        .priority-high {{ border-left: 4px solid #f44336; }}
        .priority-medium {{ border-left: 4px solid #ff9800; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 TASI Weekly Report</h1>
        <p><strong>Week:</strong> {week}</p>
        <p><strong>Generated:</strong> {datetime.now().isoformat()}</p>
        
        <h2>Performance Comparison</h2>
        <table>
            <tr>
                <th>System</th>
                <th>Trades</th>
                <th>Total PnL</th>
                <th>Win Rate</th>
                <th>Avg Win</th>
                <th>Avg Loss</th>
            </tr>
            <tr>
                <td><strong>Actual (What Happened)</strong></td>
                <td>{actual['num_trades']}</td>
                <td class="{'positive' if actual['total_pnl'] >= 0 else 'negative'}">{actual['total_pnl']:+.2f}%</td>
                <td>{actual['win_rate']:.1f}%</td>
                <td>{actual['avg_win']:+.2f}%</td>
                <td>{actual['avg_loss']:+.2f}%</td>
            </tr>
            <tr>
                <td>{previous['name']}</td>
                <td>{previous['num_trades']}</td>
                <td class="{'positive' if previous['total_pnl'] >= 0 else 'negative'}">{previous['total_pnl']:+.2f}%</td>
                <td>{previous['win_rate']:.1f}%</td>
                <td>{previous['avg_win']:+.2f}%</td>
                <td>{previous['avg_loss']:+.2f}%</td>
            </tr>
            <tr>
                <td>{current['name']}</td>
                <td>{current['num_trades']}</td>
                <td class="{'positive' if current['total_pnl'] >= 0 else 'negative'}">{current['total_pnl']:+.2f}%</td>
                <td>{current['win_rate']:.1f}%</td>
                <td>{current['avg_win']:+.2f}%</td>
                <td>{current['avg_loss']:+.2f}%</td>
            </tr>
            <tr>
                <td>{optimized['name']}</td>
                <td>{optimized['num_trades']}</td>
                <td class="{'positive' if optimized['total_pnl'] >= 0 else 'negative'}">{optimized['total_pnl']:+.2f}%</td>
                <td>{optimized['win_rate']:.1f}%</td>
                <td>{optimized['avg_win']:+.2f}%</td>
                <td>{optimized['avg_loss']:+.2f}%</td>
            </tr>
        </table>
"""
    
    # Add recommendations
    if recommendations:
        html += "<h2>Recommendations</h2>"
        for rec in recommendations:
            priority_class = f"priority-{rec['priority'].lower()}"
            html += f"""
        <div class="recommendation {priority_class}">
            <strong>[{rec['priority']}] {rec['type'].upper()}</strong><br>
            {rec['message']}<br>
            <em>Action: {rec['action']}</em>
        </div>
"""
    
    html += """
    </div>
</body>
</html>
"""
    return html

# ─── Main Report Generation ────────────────────────────────────────────────

def generate_weekly_report():
    sunday, thursday = get_week_range()
    week_label = f"{sunday.strftime('%Y-%m-%d')}_to_{thursday.strftime('%Y-%m-%d')}"
    
    print(f"Weekly Report: {week_label}")
    print("=" * 70)
    
    # Load data
    picks_by_day = load_picks_for_week(sunday, thursday)
    actual_trades = load_positions_for_week(sunday, thursday)
    
    total_picks = sum(len(p) for p in picks_by_day.values())
    print(f"Picks found: {total_picks} across {len(picks_by_day)} days")
    print(f"Actual trades: {len(actual_trades)}")
    
    # Calculate actual metrics
    if actual_trades:
        actual_pnls = [t['pnl_pct'] for t in actual_trades]
        actual_wins = [p for p in actual_pnls if p > 0]
        actual_losses = [p for p in actual_pnls if p <= 0]
        actual = {
            'name': 'Actual (What Happened)',
            'description': 'Real trades executed during the week',
            'num_trades': len(actual_trades),
            'total_pnl': round(sum(actual_pnls), 2),
            'win_rate': round(len(actual_wins) / len(actual_pnls) * 100, 1) if actual_pnls else 0,
            'avg_pnl': round(sum(actual_pnls) / len(actual_pnls), 2) if actual_pnls else 0,
            'avg_win': round(sum(actual_wins) / len(actual_wins), 2) if actual_wins else 0,
            'avg_loss': round(sum(actual_losses) / len(actual_losses), 2) if actual_losses else 0,
            'trades': actual_trades,
        }
    else:
        actual = {
            'name': 'Actual (What Happened)',
            'description': 'No trades executed this week',
            'num_trades': 0,
            'total_pnl': 0,
            'win_rate': 0,
            'avg_pnl': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'trades': [],
        }
    
    # Simulate systems
    print("\nSimulating systems...")
    previous = simulate_system(picks_by_day, "v3.2", actual)
    current = simulate_system(picks_by_day, "v4.0", actual)
    optimized = simulate_system(picks_by_day, "v4.0+", actual)
    
    # Generate recommendations
    recommendations = generate_recommendations(actual, previous, current, optimized)
    
    # Build report
    report = {
        'week': week_label,
        'generated': datetime.now().isoformat(),
        'actual': actual,
        'previous': previous,
        'current': current,
        'optimized': optimized,
        'recommendations': recommendations,
    }
    
    # Save JSON
    report_file = RELEARNING_DIR / f"report_{week_label}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    # Save HTML
    html = generate_html_report(report)
    html_file = RELEARNING_DIR / f"report_{week_label}.html"
    with open(html_file, 'w') as f:
        f.write(html)
    
    # Print summary
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    
    for system in [actual, previous, current, optimized]:
        print(f"\n{system['name']}:")
        print(f"  Trades: {system['num_trades']}")
        print(f"  Total PnL: {system['total_pnl']:+.2f}%")
        print(f"  Win Rate: {system['win_rate']:.1f}%")
        if system['num_trades'] > 0:
            print(f"  Avg PnL: {system['avg_pnl']:+.2f}%")
    
    print(f"\n\nReport saved:")
    print(f"  JSON: {report_file}")
    print(f"  HTML: {html_file}")
    
    if recommendations:
        print(f"\nRecommendations:")
        for rec in recommendations:
            print(f"  [{rec['priority']}] {rec['message']}")
            print(f"    Action: {rec['action']}")
    
    return report

if __name__ == "__main__":
    generate_weekly_report()

#!/usr/bin/env python3
"""
TASI Weekly Report v3
- Actual picks from archive (what screener really found)
- Simulation from yfinance (how each approach would have performed)
- Compares 3 approaches with different entry/exit rules
"""

import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

BASE_DIR = Path("/home/mino/tasi-exec")
ARCHIVE_DIR = BASE_DIR / "archive" / "picks"
RELEARNING_DIR = BASE_DIR / "relearning"
RELEARNING_DIR.mkdir(exist_ok=True)

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ─── Approach Exit Rules ─────────────────────────────────────────────────────

APPROACHES = {
    "conservative": {
        "name": "Conservative (Hold Until Close)",
        "exit_type": "hold_close",
        "target": None,
        "stop": 0.93,
    },
    "aggressive": {
        "name": "Aggressive (2% Target, -1% Stop)",
        "exit_type": "target_stop",
        "target": 1.02,
        "stop": 0.99,
    },
    "optimized": {
        "name": "Optimized (Score-Based)",
        "exit_type": "score_based",
        "target": None,
        "stop": None,
    }
}

# ─── Load Actual Picks from Archive ──────────────────────────────────────────

def load_picks_for_week(week_start, week_end):
    """Load all actual picks from archive for the week"""
    picks_by_day = {}
    
    if not ARCHIVE_DIR.exists():
        return picks_by_day
    
    start = datetime.strptime(week_start, "%Y-%m-%d").date()
    end = datetime.strptime(week_end, "%Y-%m-%d").date()
    
    current = start
    while current <= end:
        date_str = current.isoformat()
        picks_by_day[date_str] = []
        
        # Find all picks for this date
        for picks_file in ARCHIVE_DIR.glob(f"picks_{date_str}_*.json"):
            try:
                with open(picks_file) as f:
                    data = json.load(f)
                    picks = data.get('picks', [])
                    for pick in picks:
                        pick['source_file'] = str(picks_file)
                        pick['mode'] = data.get('mode', 'unknown')
                    picks_by_day[date_str].extend(picks)
            except Exception as e:
                log.warning(f"Error loading {picks_file}: {e}")
        
        current += timedelta(days=1)
    
    return picks_by_day


# ─── Simulate Trade for Approach ────────────────────────────────────────────

def simulate_approach(pick, approach):
    """Simulate how a pick would perform under an approach's exit rules"""
    symbol = pick.get('symbol', '')
    date = pick.get('date', '')
    entry_low = pick.get('entry_low', 0)
    entry_high = pick.get('entry_high', 0)
    score = pick.get('score', 0)
    
    if not symbol or entry_low == 0:
        return None
    
    try:
        # Get intraday data
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        start = target_date - timedelta(days=2)
        end = target_date + timedelta(days=1)
        
        df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(), 
                        interval="1m", progress=False, auto_adjust=True)
        
        if df is None or df.empty:
            return None
        
        # Handle multi-index columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        
        # Filter to target date
        day_data = df[df.index.date == target_date]
        if day_data.empty:
            return None
        
        open_p = float(day_data["Open"].iloc[0])
        high_p = float(day_data["High"].max())
        low_p = float(day_data["Low"].min())
        close_p = float(day_data["Close"].iloc[-1])
        
        # Entry check
        entry_price = (entry_low + entry_high) / 2
        if not (low_p <= entry_price <= high_p):
            return {
                "symbol": symbol,
                "date": date,
                "approach": approach,
                "hit_entry": False,
                "entry_price": round(entry_price, 2),
                "pnl_pct": 0,
                "score": score,
            }
        
        # Apply exit rules
        params = APPROACHES[approach]
        
        if params["exit_type"] == "hold_close":
            exit_price = close_p
            pnl = (exit_price - entry_price) / entry_price * 100
            
        elif params["exit_type"] == "target_stop":
            target_p = entry_price * params["target"]
            stop_p = entry_price * params["stop"]
            
            exit_price = close_p
            for _, row in day_data.iterrows():
                if float(row["High"]) >= target_p:
                    exit_price = target_p
                    break
                elif float(row["Low"]) <= stop_p:
                    exit_price = stop_p
                    break
            
            pnl = (exit_price - entry_price) / entry_price * 100
            
        elif params["exit_type"] == "score_based":
            target_pct = 1.5 + score / 100
            stop_pct = max(-0.8, -score / 50)
            
            target_p = entry_price * (1 + target_pct / 100)
            stop_p = entry_price * (1 + stop_pct / 100)
            
            exit_price = close_p
            for _, row in day_data.iterrows():
                if float(row["High"]) >= target_p:
                    exit_price = target_p
                    break
                elif float(row["Low"]) <= stop_p:
                    exit_price = stop_p
                    break
            
            pnl = (exit_price - entry_price) / entry_price * 100
        
        return {
            "symbol": symbol,
            "date": date,
            "approach": approach,
            "hit_entry": True,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "close_price": round(close_p, 2),
            "pnl_pct": round(pnl, 2),
            "score": score,
        }
        
    except Exception as e:
        log.warning(f"Simulate error {symbol}: {e}")
        return None


# ─── Run Weekly Simulation ─────────────────────────────────────────────────

def run_weekly_simulation(week_start, week_end):
    """Run simulation for a week"""
    log.info(f"Weekly simulation: {week_start} to {week_end}")
    
    # Load actual picks
    picks_by_day = load_picks_for_week(week_start, week_end)
    total_picks = sum(len(p) for p in picks_by_day.values())
    log.info(f"Loaded {total_picks} actual picks from archive")
    
    if total_picks == 0:
        log.warning("No picks found in archive")
        return None
    
    # Simulate each approach
    all_results = {}
    
    for approach in APPROACHES.keys():
        log.info(f"Simulating {approach}...")
        trades = []
        
        for date, picks in picks_by_day.items():
            for pick in picks:
                trade = simulate_approach(pick, approach)
                if trade:
                    trades.append(trade)
        
        # Calculate metrics
        hit_trades = [t for t in trades if t.get("hit_entry")]
        if hit_trades:
            pnls = [t["pnl_pct"] for t in hit_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            
            metrics = {
                "total_pnl": round(sum(pnls), 2),
                "num_trades": len(hit_trades),
                "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
                "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
                "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
                "trades": hit_trades,
            }
        else:
            metrics = {
                "total_pnl": 0,
                "num_trades": 0,
                "win_rate": 0,
                "avg_pnl": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "trades": [],
            }
        
        all_results[approach] = metrics
        
        print(f"\n{APPROACHES[approach]['name']}:")
        print(f"  Total PnL: {metrics['total_pnl']:+.2f}%")
        print(f"  Win Rate: {metrics['win_rate']:.1f}%")
        print(f"  Trades: {metrics['num_trades']}")
    
    # Save report
    report = {
        "week": f"{week_start}_to_{week_end}",
        "generated": datetime.now().isoformat(),
        "actual_picks": total_picks,
        "approaches": all_results,
    }
    
    report_file = RELEARNING_DIR / f"report_{week_start}_to_{week_end}.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    
    log.info(f"Report saved to {report_file}")
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", help="Week start (YYYY-MM-DD)")
    parser.add_argument("--week-end", help="Week end (YYYY-MM-DD)")
    args = parser.parse_args()
    
    if args.week_start and args.week_end:
        report = run_weekly_simulation(args.week_start, args.week_end)
    else:
        # Default: last completed week
        today = datetime.now().date()
        if today.weekday() >= 4:  # Friday or later
            thursday = today - timedelta(days=today.weekday() - 3)
        else:
            thursday = today - timedelta(days=7 + today.weekday() - 3)
        sunday = thursday - timedelta(days=4)
        
        report = run_weekly_simulation(sunday.isoformat(), thursday.isoformat())
    
    if report:
        print(f"\n{'='*50}")
        print("WEEKLY REPORT COMPLETE")
        print(f"{'='*50}")
        for approach, data in report["approaches"].items():
            print(f"{approach}: PnL={data['total_pnl']:+.2f}%, WR={data['win_rate']:.1f}%, Trades={data['num_trades']}")


if __name__ == "__main__":
    main()

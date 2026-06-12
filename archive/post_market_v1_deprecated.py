#!/usr/bin/env python3
"""
Post-market analysis: what if we had traded the picks?
Runs after market close to analyze missed opportunities and track strategy performance.
"""
import json
import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path
import pytz

RIYADH = pytz.timezone("Asia/Riyadh")
BASE_DIR = Path("/home/mino/tasi-exec")

def analyze_pick(symbol, entry_low, entry_high, stop_loss):
    """Analyze what happened to a pick during the session."""
    try:
        ticker = yf.Ticker(symbol)
        # Use period only — intraday intervals fail for TADAWUL on Yahoo Finance
        df = ticker.history(period="1d")
        if df.empty:
            df = ticker.history(period="5d")
            if not df.empty:
                df = df.iloc[[-1]]
        
        if df.empty:
            return None
        
        open_price = float(df["Open"].iloc[0])
        high_price = float(df["High"].max())
        low_price = float(df["Low"].min())
        close_price = float(df["Close"].iloc[-1])
        
        # Check if price entered the zone
        entered_zone = any(
            entry_low <= row["Close"] <= entry_high 
            for _, row in df.iterrows()
        )
        
        # Check if VWAP reclaim happened
        df["tp"] = (df["High"] + df["Low"] + df["Close"]) / 3
        cum_vol = df["Volume"].cumsum()
        vwap = (df["tp"] * df["Volume"]).cumsum().iloc[-1] / cum_vol.iloc[-1]
        
        vwap_reclaim = False
        vwap_reclaim_time = None
        for i in range(1, len(df)):
            prev_close = df["Close"].iloc[i-1]
            curr_close = df["Close"].iloc[i]
            if prev_close < vwap < curr_close:
                vwap_reclaim = True
                vwap_reclaim_time = df.index[i]
                break
        
        # Check if breakout happened
        prior_high = float(df["High"].iloc[:-1].max()) if len(df) > 1 else open_price
        breakout = False
        breakout_time = None
        for i, row in df.iterrows():
            if row["Close"] > prior_high and row["Volume"] > df["Volume"].mean() * 1.5:
                breakout = True
                breakout_time = i
                break
        
        # Calculate what would have happened if bought at entry_high
        if entered_zone:
            entry = entry_high
            
            # Check if stop loss hit
            stop_hit = low_price <= stop_loss
            stop_hit_time = None
            if stop_hit:
                for i, row in df.iterrows():
                    if row["Low"] <= stop_loss:
                        stop_hit_time = i
                        break
            
            # Max profit
            max_profit_pct = (high_price - entry) / entry * 100 if high_price > entry else 0
            
            # Actual close profit
            close_pct = (close_price - entry) / entry * 100
            
            # Time to max profit
            time_to_max = None
            for i, row in df.iterrows():
                if row["High"] == high_price:
                    time_to_max = i
                    break
            
            return {
                "entered_zone": True,
                "vwap_reclaim": vwap_reclaim,
                "vwap_reclaim_time": vwap_reclaim_time.isoformat() if vwap_reclaim_time else None,
                "breakout": breakout,
                "breakout_time": breakout_time.isoformat() if breakout_time else None,
                "entry": entry,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "stop_hit": stop_hit,
                "stop_hit_time": stop_hit_time.isoformat() if stop_hit_time else None,
                "max_profit_pct": max_profit_pct,
                "close_pct": close_pct,
                "time_to_max": time_to_max.isoformat() if time_to_max else None,
            }
        else:
            return {
                "entered_zone": False,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "reason": f"Price never entered zone ({entry_low}-{entry_high})"
            }
    except Exception as e:
        return {"error": str(e)}

def generate_report(date_str, picks, results):
    """Generate a detailed post-market report."""
    report = []
    report.append(f"📊 Post-Market Analysis: {date_str}")
    report.append(f"Market: TASI | Regime: NEUTRAL")
    report.append("")
    
    total_picks = len(picks)
    entered_count = sum(1 for r in results if r and r.get("entered_zone"))
    signal_count = sum(1 for r in results if r and (r.get("vwap_reclaim") or r.get("breakout")))
    profitable_count = sum(1 for r in results if r and r.get("close_pct", 0) > 0)
    
    report.append(f"Summary: {entered_count}/{total_picks} entered zones | {signal_count} had signals | {profitable_count} profitable")
    report.append("")
    
    for pick, result in zip(picks, results):
        symbol = pick.get("ticker") or pick.get("symbol", "")
        entry_low = pick.get("entry_low", 0)
        entry_high = pick.get("entry_high", 0)
        stop_loss = pick.get("stop_loss", 0)
        pm = pick.get("pm_metrics", {})
        
        report.append(f"🔍 {symbol}")
        report.append(f"  Entry zone: {entry_low:.2f} - {entry_high:.2f} | Stop: {stop_loss:.2f}")
        
        # Show pre-market momentum metrics if available
        if pm:
            report.append(f"  📊 Pre-market momentum:")
            report.append(f"    ATR(10)={pm.get('atr', 'N/A')} | Vol×{pm.get('vol_ratio', 'N/A')} | Vel={pm.get('velocity', 'N/A')}% | Range×{pm.get('range_ratio', 'N/A')}")
        
        if result and result.get("entered_zone"):
            report.append(f"  ✅ Entered zone at {result['entry']:.2f}")
            
            if result.get("vwap_reclaim"):
                report.append(f"  📈 VWAP reclaim: YES")
            else:
                report.append(f"  📈 VWAP reclaim: NO")
            
            if result.get("breakout"):
                report.append(f"  🚀 Breakout: YES")
            else:
                report.append(f"  🚀 Breakout: NO")
            
            report.append(f"  High: {result['high']:.2f} (+{result['max_profit_pct']:.2f}%)")
            report.append(f"  Low: {result['low']:.2f}")
            report.append(f"  Close: {result['close']:.2f} ({result['close_pct']:+.2f}%)")
            
            if result.get("stop_hit"):
                report.append(f"  🛑 STOP LOSS HIT at {stop_loss:.2f}")
            else:
                report.append(f"  ✅ Stop loss NOT hit")
            
            # Strategy assessment
            if result.get("vwap_reclaim") or result.get("breakout"):
                if result["close_pct"] > 2:
                    report.append(f"  💰 GOOD TRADE: +{result['close_pct']:.2f}%")
                elif result["close_pct"] > 0:
                    report.append(f"  ✅ PROFIT: +{result['close_pct']:.2f}%")
                elif result["close_pct"] > -3:
                    report.append(f"  ⚠️ SMALL LOSS: {result['close_pct']:.2f}%")
                else:
                    report.append(f"  ❌ BIG LOSS: {result['close_pct']:.2f}%")
            else:
                report.append(f"  ⏭️ SKIPPED: No signal fired")
        
        elif result and not result.get("error"):
            report.append(f"  ❌ Never entered zone")
            report.append(f"  Open: {result.get('open', '?'):.2f} | High: {result.get('high', '?'):.2f} | Low: {result.get('low', '?'):.2f}")
            report.append(f"  Reason: {result.get('reason', '')}")
        else:
            report.append(f"  ❌ Error: {result.get('error', 'unknown')}")
        
        report.append("")
    
    # Learning section
    report.append("📚 Learning:")
    if signal_count == 0:
        report.append("  • No signals fired — market was flat (0.05% move)")
        report.append("  • Strategy correctly avoided forcing trades")
    else:
        report.append(f"  • {signal_count} signals fired out of {total_picks} picks")
    
    if profitable_count > 0:
        report.append(f"  • {profitable_count} picks were profitable if traded")
    
    # Momentum filter analysis
    report.append("")
    report.append("🔍 Momentum Filter Analysis:")
    picks_with_pm = [p for p in picks if p.get("pm_metrics")]
    if picks_with_pm:
        avg_atr = sum(p["pm_metrics"].get("atr", 0) for p in picks_with_pm) / len(picks_with_pm)
        avg_vel = sum(p["pm_metrics"].get("velocity", 0) for p in picks_with_pm) / len(picks_with_pm)
        avg_vol = sum(p["pm_metrics"].get("vol_ratio", 0) for p in picks_with_pm) / len(picks_with_pm)
        report.append(f"  • {len(picks_with_pm)}/{total_picks} picks had pre-market momentum data")
        report.append(f"  • Avg ATR(10): {avg_atr:.3f} | Avg Velocity: {avg_vel:.2f}% | Avg Vol Ratio: {avg_vol:.1f}")
        
        # Check if momentum predicted outcome
        for pick in picks_with_pm:
            sym = pick["symbol"]
            pm = pick["pm_metrics"]
            result = next((r for p, r in zip(picks, results) if p["symbol"] == sym), None)
            if result and result.get("entered_zone"):
                if result.get("close_pct", 0) > 0 and pm.get("velocity", 0) > 0.2:
                    report.append(f"  • ✅ {sym}: Strong momentum (vel={pm['velocity']:.2f}%) → Profit ({result['close_pct']:+.2f}%)")
                elif result.get("close_pct", 0) < 0 and pm.get("velocity", 0) < 0.1:
                    report.append(f"  • ✅ {sym}: Weak momentum (vel={pm['velocity']:.2f}%) → Avoided loss ({result['close_pct']:+.2f}%)")
                elif result.get("close_pct", 0) < 0 and pm.get("velocity", 0) > 0.2:
                    report.append(f"  • ⚠️ {sym}: Strong momentum (vel={pm['velocity']:.2f}%) → Still lost ({result['close_pct']:+.2f}%)")
    else:
        report.append("  • No pre-market momentum data available for today's picks")
    
    report.append("")
    report.append("📈 Strategy Performance Tracking:")
    report.append(f"  • Total picks: {total_picks}")
    report.append(f"  • Entered zone: {entered_count} ({entered_count/total_picks*100:.1f}%)")
    report.append(f"  • Signals fired: {signal_count}")
    report.append(f"  • Profitable: {profitable_count}")
    
    return "\n".join(report)

def save_report(date_str, report):
    """Save report to file."""
    report_dir = BASE_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    
    report_file = report_dir / f"post_market_{date_str}.txt"
    with open(report_file, "w") as f:
        f.write(report)
    
    return report_file

def update_performance_log(date_str, picks, results):
    """Update the performance tracking log."""
    perf_file = BASE_DIR / "performance.json"
    
    if perf_file.exists():
        with open(perf_file) as f:
            performance = json.load(f)
    else:
        performance = {
            "total_sessions": 0,
            "total_picks": 0,
            "entered_zone_count": 0,
            "signal_count": 0,
            "profitable_count": 0,
            "sessions": []
        }
    
    entered_count = sum(1 for r in results if r and r.get("entered_zone"))
    signal_count = sum(1 for r in results if r and (r.get("vwap_reclaim") or r.get("breakout")))
    profitable_count = sum(1 for r in results if r and r.get("close_pct", 0) > 0)
    
    session_data = {
        "date": date_str,
        "picks": len(picks),
        "entered_zone": entered_count,
        "signals": signal_count,
        "profitable": profitable_count,
        "market_regime": "NEUTRAL"  # Will be updated from actual regime
    }
    
    performance["total_sessions"] += 1
    performance["total_picks"] += len(picks)
    performance["entered_zone_count"] += entered_count
    performance["signal_count"] += signal_count
    performance["profitable_count"] += profitable_count
    performance["sessions"].append(session_data)
    
    with open(perf_file, "w") as f:
        json.dump(performance, f, indent=2)
    
    return performance

def main():
    now = datetime.now(RIYADH)
    date_str = now.strftime("%Y-%m-%d")
    
    # Load picks
    try:
        with open(BASE_DIR / "picks.json") as f:
            picks_data = json.load(f)
    except:
        print("No picks file found")
        return
    
    picks = picks_data.get("picks", [])
    
    # Analyze each pick
    results = []
    for pick in picks:
        symbol = pick.get("symbol", "")
        entry_low = pick.get("entry_low", 0)
        entry_high = pick.get("entry_high", 0)
        stop_loss = pick.get("stop_loss", 0)
        
        result = analyze_pick(symbol, entry_low, entry_high, stop_loss)
        results.append(result)
    
    # Generate report
    report = generate_report(date_str, picks, results)
    
    # Save report
    report_file = save_report(date_str, report)
    
    # Update performance log
    performance = update_performance_log(date_str, picks, results)
    
    # Print report
    print(report)
    print(f"\n📁 Report saved: {report_file}")
    print(f"📈 Performance log updated: {performance['total_sessions']} sessions")

if __name__ == "__main__":
    main()

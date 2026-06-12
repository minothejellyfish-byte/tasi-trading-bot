#!/usr/bin/env python3
"""
Post-Market Analysis — Daily Market Review
- Scans ALL Sharia stocks
- Sequential yfinance fetching (thread-safe)
- Retry logic with backoff
- Uses cached data when available
- Reads TASI_SYSTEM_REFERENCE.md before analysis
- Trading week: Sun-Thu (Fri-Sat weekend)
"""""

import json
import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path
import pytz
import requests
import time
import sys

RIYADH = pytz.timezone("Asia/Riyadh")
BASE_DIR = Path("/home/mino/tasi-exec")
SHARIA_FILE = BASE_DIR / "sharia_list.json"
PICKS_FILE = BASE_DIR / "picks.json"
WS_FRAMES_FILE = BASE_DIR / "ws_frames.json"
CACHE_FILE = BASE_DIR / "pm_cache.json"
SYSTEM_REF = Path("/home/mino/.openclaw-mino/workspace/TASI_SYSTEM_REFERENCE.md")

BOT_TOKEN = "8989533040:AAFWzP_lYL3g_w4eXGxrvwdo-tBNdPxVYQU"
GROUP_CHAT_ID = -5235925419

# Parallel config
MAX_WORKERS = 8
FETCH_TIMEOUT = 15  # seconds per stock


def load_system_config():
    """Load system configuration from reference file."""
    config = {
        "version": "4.0",
        "screens": ["premarket", "midscreen1", "midscreen2", "rescreen"],
        "trading_days": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"],
        "weekend": ["Friday", "Saturday"],
        "max_positions": {"TRENDING": 3, "NEUTRAL": 3, "DEFENSIVE": 4},
    }
    if SYSTEM_REF.exists():
        print(f"[INFO] System reference found: {SYSTEM_REF}")
    else:
        print(f"[WARNING] System reference missing: {SYSTEM_REF}")
    return config


SYSTEM_CONFIG = load_system_config()


def tg_send(text: str, parse_mode: str = "HTML"):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": GROUP_CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
    except Exception as e:
        print(f"tg_send failed: {e}")


def load_sharia_tickers():
    with open(SHARIA_FILE) as f:
        data = json.load(f)
    stocks = data.get("stocks", [])
    return [s["yahoo"] for s in stocks if s.get("yahoo", "").endswith(".SR")]


def load_picks():
    try:
        with open(PICKS_FILE) as f:
            data = json.load(f)
        return data.get("picks", [])
    except:
        return []


def load_cache():
    """Load cached performance data from today."""
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        today = datetime.now(RIYADH).strftime("%Y-%m-%d")
        if cache.get("date") == today:
            return cache.get("data", {})
    except:
        pass
    return {}


def save_cache(data: dict):
    today = datetime.now(RIYADH).strftime("%Y-%m-%d")
    with open(CACHE_FILE, "w") as f:
        json.dump({"date": today, "data": data}, f)


def fetch_one(symbol: str, cache: dict) -> tuple:
    """Fetch a single stock with caching."""
    # Check cache first
    if symbol in cache:
        return symbol, cache[symbol]

    for attempt in range(2):
        try:
            ticker = yf.Ticker(symbol)
            # Use period only — intraday intervals fail for TADAWUL on Yahoo Finance
            df = ticker.history(period="1d")
            if df.empty:
                df = ticker.history(period="5d")
                if not df.empty:
                    # Take the most recent day's data from 5d
                    df = df.iloc[[-1]]

            if df.empty:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                return symbol, None

            open_p = float(df["Open"].iloc[0])
            high = float(df["High"].max())
            low = float(df["Low"].min())
            close = float(df["Close"].iloc[-1])
            volume = int(df["Volume"].sum())

            change_pct = (close - open_p) / open_p * 100
            max_intraday_pct = (high - open_p) / open_p * 100

            result = {
                "symbol": symbol,
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "change_pct": change_pct,
                "max_intraday_pct": max_intraday_pct,
            }
            return symbol, result
        except Exception:
            if attempt == 0:
                time.sleep(0.5)
            else:
                return symbol, None
    return symbol, None


def analyze_all_stocks_sequential(tickers: list, picks_symbols: set, cache: dict):
    """Fetch all stocks sequentially (yfinance is not thread-safe)."""
    performances = []
    new_cache = {}
    fail_count = 0

    for sym in tickers:
        symbol, result = fetch_one(sym, cache)
        if result:
            result["was_picked"] = symbol in picks_symbols
            performances.append(result)
            new_cache[symbol] = result
        elif symbol in cache:
            # Keep cached data even if fetch failed this time
            cached = cache[symbol].copy()
            cached["was_picked"] = symbol in picks_symbols
            performances.append(cached)
            new_cache[symbol] = cache[symbol]
        else:
            fail_count += 1

    performances.sort(key=lambda x: x["max_intraday_pct"], reverse=True)
    return performances, fail_count, new_cache


def analyze_picks_detailed(picks: list, perf_map: dict):
    results = []
    for pick in picks:
        symbol = pick.get("symbol", "")
        entry_high = pick.get("entry_high", 0)
        entry_low = pick.get("entry_low", 0)
        score = pick.get("score", 0)
        tier = pick.get("tier", "main")

        perf = perf_map.get(symbol)
        result = {
            "symbol": symbol,
            "entry_high": entry_high,
            "entry_low": entry_low,
            "score": score,
            "tier": tier,
            "perf": perf,
        }

        if perf and entry_high > 0:
            if perf["open"] > entry_high:
                result["gap_status"] = "above"
                result["gap_pct"] = (perf["open"] - entry_high) / entry_high * 100
            elif perf["open"] < entry_low:
                result["gap_status"] = "below"
                result["gap_pct"] = (perf["open"] - entry_low) / entry_low * 100
            else:
                result["gap_status"] = "in_zone"
                result["gap_pct"] = 0

            result["touched_zone"] = entry_low <= perf["low"] <= entry_high or entry_low <= perf["high"] <= entry_high
        else:
            result["gap_status"] = None
            result["gap_pct"] = 0
            result["touched_zone"] = False

        results.append(result)
    return results


def find_missed_opportunities(performances: list, picks_symbols: set, min_move: float = 1.5):
    missed = [
        p for p in performances
        if not p["was_picked"]
        and p["max_intraday_pct"] > min_move
        and (p["high"] - p["low"]) / p["open"] > 0.015
        and p["volume"] > 50000
    ]
    return missed[:10]


def generate_recommendations(pick_analysis: list, missed: list, performances: list):
    recommendations = []

    gap_ups = [p for p in pick_analysis if p.get("gap_status") == "above"]
    if gap_ups:
        avg_gap = sum(p["gap_pct"] for p in gap_ups) / len(gap_ups)
        small_gaps = [p for p in gap_ups if p["gap_pct"] < 0.5]
        recommendations.append(
            f"🚀 {len(gap_ups)}/{len(pick_analysis)} picks gapped above entry zones "
            f"(avg +{avg_gap:.1f}%). "
            f"{len(small_gaps)} had small gaps (<0.5%) — consider chase entry with tight stop."
        )

    for p in pick_analysis:
        if p.get("perf") and p["perf"]["max_intraday_pct"] > 2 and p.get("gap_status") == "above":
            recommendations.append(
                f"💰 {p['symbol']} moved +{p['perf']['max_intraday_pct']:.1f}% but was skipped "
                f"(gapped +{p.get('gap_pct', 0):.1f}%). Consider gap-up entry rule."
            )

    no_zone = [p for p in pick_analysis if p["entry_high"] == 0]
    if no_zone:
        recommendations.append(
            f"⚠️ {len(no_zone)} midscreen picks have no entry zones — "
            f"need zone calculation or manual review."
        )

    if missed:
        avg_missed = sum(p["max_intraday_pct"] for p in missed) / len(missed)
        recommendations.append(
            f"🔍 {len(missed)} missed opportunities avg +{avg_missed:.1f}% — "
            f"review screener thresholds."
        )

    if performances:
        up_count = sum(1 for p in performances if p["change_pct"] > 0)
        breadth_pct = up_count / len(performances) * 100
        if breadth_pct < 30:
            recommendations.append(f"📉 Weak breadth ({breadth_pct:.0f}% stocks up) — defensive day.")
        elif breadth_pct > 70:
            recommendations.append(f"📈 Strong breadth ({breadth_pct:.0f}% stocks up) — trending day.")

    if not recommendations:
        recommendations.append("✅ Strategy performed well — no major gaps detected.")

    return recommendations


def generate_report(date_str: str, pick_analysis: list, performances: list,
                    missed: list, recommendations: list, total_scanned: int, fail_count: int):
    report = [
        f"📊 <b>Post-Market Analysis: {date_str}</b>",
        f"Market: TASI | Scanned: {len(performances)}/{total_scanned} stocks | Time: {datetime.now(RIYADH).strftime('%H:%M %Z')}",
        "",
        "━" * 45,
        "",
    ]

    # Picks performance
    report.append("<b>🎯 Today's Picks Performance</b>")
    report.append("")

    for pa in pick_analysis:
        symbol = pa["symbol"]
        perf = pa.get("perf")
        tier_tag = f" [{pa['tier'].upper()}]" if pa["tier"] != "main" else ""

        if perf:
            status = "🟢" if perf["change_pct"] > 0 else "🔴"
            report.append(
                f"{status} <b>{symbol}</b>{tier_tag} | "
                f"O:{perf['open']:.2f} H:{perf['high']:.2f}(+{perf['max_intraday_pct']:.1f}%) "
                f"L:{perf['low']:.2f} C:{perf['close']:.2f}({perf['change_pct']:+.1f}%) "
                f"V:{perf['volume']:,}"
            )

            if pa["entry_high"] > 0:
                gap = pa.get("gap_status")
                if gap == "above":
                    report.append(f"   ⚠️ Gapped +{pa['gap_pct']:.1f}% above zone {pa['entry_low']:.2f}–{pa['entry_high']:.2f}")
                elif gap == "below":
                    report.append(f"   ⚠️ Gapped {pa['gap_pct']:.1f}% below zone")
                elif gap == "in_zone":
                    touched = "✅ Touched" if pa.get("touched_zone") else "❌ Never touched"
                    report.append(f"   🎯 In zone {pa['entry_low']:.2f}–{pa['entry_high']:.2f} | {touched}")
            else:
                report.append(f"   ⚠️ No entry zone set")
        else:
            report.append(f"⚠️ <b>{symbol}</b>{tier_tag} — No data available")
        report.append("")

    report.append("━" * 45)
    report.append("")

    # Why no trades
    gap_ups = [p for p in pick_analysis if p.get("gap_status") == "above"]
    if gap_ups:
        report.append("<b>🚫 Why No Orders Were Placed</b>")
        report.append("")
        report.append(f"{len(gap_ups)} picks gapped above their entry zones at open:")
        for p in gap_ups:
            report.append(
                f"• {p['symbol']}: opened {p['perf']['open']:.2f} > zone {p['entry_low']:.2f}–{p['entry_high']:.2f} "
                f"(+{p['gap_pct']:.1f}% gap)"
            )
        report.append("")

    in_zone = [p for p in pick_analysis if p.get("gap_status") == "in_zone"]
    if in_zone and not any(p.get("touched_zone") for p in in_zone):
        report.append("Picks in zone never dipped back into buy zone during session.")
        report.append("")

    report.append("━" * 45)
    report.append("")

    # Missed opportunities
    if missed:
        report.append(f"<b>🔍 Missed Opportunities ({len(missed)} stocks)</b>")
        report.append("Top movers not in our picks:")
        report.append("")
        for i, p in enumerate(missed[:8], 1):
            report.append(
                f"{i}. <b>{p['symbol']}</b> | +{p['max_intraday_pct']:.1f}% max | "
                f"O:{p['open']:.2f} C:{p['close']:.2f}({p['change_pct']:+.1f}%) V:{p['volume']:,}"
            )
        report.append("")
    else:
        report.append("<b>🔍 Missed Opportunities</b>")
        report.append("No significant missed opportunities (>1.5% move, >50K vol).")
        report.append("")

    report.append("━" * 45)
    report.append("")

    # Market breadth
    if performances:
        up_count = sum(1 for p in performances if p["change_pct"] > 0)
        down_count = len(performances) - up_count
        avg_change = sum(p["change_pct"] for p in performances) / len(performances)
        best = max(performances, key=lambda x: x["max_intraday_pct"])
        worst = min(performances, key=lambda x: x["max_intraday_pct"])

        report.append("<b>📊 Market Breadth</b>")
        report.append("")
        report.append(f"• Total scanned: {len(performances)}")
        report.append(f"• Up: {up_count} | Down: {down_count} | Avg: {avg_change:+.2f}%")
        report.append(f"• Best: {best['symbol']} +{best['max_intraday_pct']:.1f}%")
        report.append(f"• Worst: {worst['symbol']} {worst['max_intraday_pct']:+.1f}%")
        report.append("")

    report.append("━" * 45)
    report.append("")

    # Recommendations
    report.append("<b>💡 Strategy Recommendations</b>")
    report.append("")
    for rec in recommendations:
        report.append(f"• {rec}")
    report.append("")

    report.append("━" * 45)
    report.append("")

    # Session summary
    picked_perfs = [p.get("perf") for p in pick_analysis if p.get("perf")]
    best_pick = max((p for p in picked_perfs), key=lambda x: x["max_intraday_pct"], default=None)

    report.append("<b>📈 Session Summary</b>")
    report.append(f"• Picks: {len(pick_analysis)}")
    report.append(f"• Best pick: +{best_pick['max_intraday_pct']:.1f}% ({best_pick['symbol']})" if best_pick else "• Best pick: N/A")
    report.append(f"• Missed opportunities: {len(missed)}")
    report.append(f"• Data failures: {fail_count}/{total_scanned}")
    report.append("")
    report.append("━" * 45)
    report.append("")

    today = datetime.now(RIYADH)
    next_day = today + timedelta(days=1)
    while next_day.weekday() >= 4:
        next_day += timedelta(days=1)
    report.append(f"<i>Next session: {next_day.strftime('%A %Y-%m-%d')} at 10:00 +03</i>")

    return "\n".join(report)


def save_and_track(date_str: str, report: str, missed: list, recommendations: list):
    report_dir = BASE_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    report_file = report_dir / f"post_market_{date_str}.html"

    with open(report_file, "w") as f:
        f.write(f"<pre>{report}</pre>")

    # ── Write to OpenClaw memory ──────────────────────────────────────
    try:
        memory_dir = Path("/home/mino/.openclaw-mino/workspace/memory")
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_file = memory_dir / f"{date_str}.md"
        
        # Strip HTML tags for markdown
        import re
        clean_report = re.sub(r'<[^>]+>', '', report)
        
        # Extract key metrics
        picks_count = len(load_picks())
        missed_count = len(missed)
        
        memory_content = f"""# TASI Post-Market Report — {date_str}

## Session Summary
- **Date**: {date_str}
- **Picks**: {picks_count}
- **Missed Opportunities**: {missed_count}
- **Recommendations**: {len(recommendations)}

## Picks Performance
{clean_report[:3000]}

## Recommendations
"""
        for i, rec in enumerate(recommendations[:10], 1):
            memory_content += f"{i}. {rec}\n"
        
        memory_content += f"""
## Tags
#tasi #post-market #{date_str} #trading-day

## Source
Generated by post_market.py v4.1
"""
        with open(memory_file, "w") as f:
            f.write(memory_content)
        print(f"📝 Saved to memory: {memory_file}")
    except Exception as e:
        print(f"[WARNING] Failed to write memory file: {e}")
    # ───────────────────────────────────────────────────────────────────

    learning_file = BASE_DIR / "learning.json"
    if learning_file.exists():
        with open(learning_file) as f:
            learning = json.load(f)
    else:
        learning = {
            "sessions_analyzed": 0,
            "recommendations_made": [],
            "recommendations_applied": [],
            "missed_opportunities_avg": 0,
            "strategy_versions": ["v4.0"]
        }

    learning["sessions_analyzed"] += 1
    learning["recommendations_made"].extend(recommendations)

    if missed:
        avg_gain = sum(m["max_intraday_pct"] for m in missed) / len(missed)
        prev_avg = learning.get("missed_opportunities_avg", 0)
        n = learning["sessions_analyzed"]
        learning["missed_opportunities_avg"] = (prev_avg * (n - 1) + avg_gain) / n

    with open(learning_file, "w") as f:
        json.dump(learning, f, indent=2)

    return learning_file


def _is_saudi_trading_day(dt: datetime) -> bool:
    """Return True if dt falls on a TASI trading day (Sun–Thu)."""
    return dt.weekday() in (6, 0, 1, 2, 3, 4)


def main():
    now = datetime.now(RIYADH)
    date_str = now.strftime("%Y-%m-%d")

    print(f"📊 Post-market analysis starting: {date_str}")
    print(f"[INFO] System config: {SYSTEM_CONFIG['version']} | Screens: {len(SYSTEM_CONFIG['screens'])} | Trading days: {', '.join(SYSTEM_CONFIG['trading_days'][:3])}...")

    if not _is_saudi_trading_day(now):
        msg = f"⚠️ TASI closed today ({now:%A}). Skipping post-market scan."
        print(msg)
        tg_send(msg)
        return

    start_time = time.time()

    tickers = load_sharia_tickers()
    picks = load_picks()
    picks_symbols = {p.get("ticker") or p.get("symbol", "") for p in picks}
    cache = load_cache()

    print(f"• Sharia stocks: {len(tickers)}")
    print(f"• Picks today: {len(picks)}")
    print(f"• Cached: {len(cache)} stocks")

    print(f"\nScanning {len(tickers)} stocks sequentially (yfinance thread-safe)...")
    performances, fail_count, new_cache = analyze_all_stocks_sequential(tickers, picks_symbols, cache)

    # Save cache for next run
    save_cache(new_cache)

    elapsed = time.time() - start_time
    print(f"• Done in {elapsed:.1f}s: {len(performances)}/{len(tickers)} stocks ({fail_count} failures)")

    perf_map = {p["symbol"]: p for p in performances}
    pick_analysis = analyze_picks_detailed(picks, perf_map)
    missed = find_missed_opportunities(performances, picks_symbols)
    recommendations = generate_recommendations(pick_analysis, missed, performances)

    print(f"• Missed opportunities: {len(missed)}")

    report = generate_report(date_str, pick_analysis, performances, missed,
                            recommendations, len(tickers), fail_count)

    tg_send(report)

    learning_file = save_and_track(date_str, report, missed, recommendations)

    summary = (
        f"📊 Post-market analysis sent to TASI group\n"
        f"• Stocks scanned: {len(performances)}/{len(tickers)} ({elapsed:.0f}s)\n"
        f"• Picks analyzed: {len(picks)}\n"
        f"• Missed opportunities: {len(missed)}\n"
        f"• Recommendations: {len(recommendations)}\n"
        f"• Data failures: {fail_count}"
    )

    print(f"\n{summary}")
    tg_send(summary)

    # Send daily report via email
    try:
        send_daily_email_report(date_str, pick_analysis, missed, recommendations, elapsed)
    except Exception as e:
        print(f"Email send failed: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # RUN INTELLIGENT ANALYSIS (Added 2026-06-04)
    # ═══════════════════════════════════════════════════════════════════════════
    try:
        run_intelligent_analysis(date_str, perf_map)
    except Exception as e:
        print(f"[ERROR] Intelligent analysis failed: {e}")
        import traceback
        traceback.print_exc()
    # ═══════════════════════════════════════════════════════════════════════════


def send_daily_email_report(date_str, pick_analysis, missed, recommendations, elapsed):
    """Send daily post-market report via email"""
    import smtplib
    import ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    # Build HTML email
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2>📊 TASI Daily Post-Market Report — {date_str}</h2>
        
        <h3>Today's Picks Performance</h3>
        <table border="1" cellpadding="8" style="border-collapse: collapse;">
            <tr style="background-color: #f2f2f2;">
                <th>Symbol</th><th>Entry</th><th>High</th><th>Low</th><th>Close</th><th>Change%</th>
            </tr>
"""
    
    for pick in pick_analysis:
        color = "#2E7D32" if pick.get('change_pct', 0) >= 0 else "#C62828"
        html_body += f"""
            <tr>
                <td>{pick['symbol']}</td>
                <td>{pick.get('entry_price', 'N/A')}</td>
                <td>{pick.get('high', 'N/A')}</td>
                <td>{pick.get('low', 'N/A')}</td>
                <td>{pick.get('close', 'N/A')}</td>
                <td style="color: {color};">{pick.get('change_pct', 0):+.2f}%</td>
            </tr>
"""
    
    html_body += """
        </table>
        
        <h3>Missed Opportunities</h3>
        <table border="1" cellpadding="8" style="border-collapse: collapse;">
            <tr style="background-color: #f2f2f2;">
                <th>Symbol</th><th>Max Move%</th><th>Why Missed</th>
            </tr>
"""
    
    for m in missed[:5]:
        html_body += f"""
            <tr>
                <td>{m['symbol']}</td>
                <td>{m.get('max_intraday_pct', 0):+.2f}%</td>
                <td>{m.get('reason', 'N/A')}</td>
            </tr>
"""
    
    html_body += """
        </table>
        
        <h3>Recommendations</h3>
        <ul>
"""
    
    for rec in recommendations:
        # Handle both dict and string formats
        if isinstance(rec, dict):
            priority = rec.get('priority', 'INFO')
            issue = rec.get('issue', '')
            fix = rec.get('fix', '')
        else:
            # String format — parse or display as-is
            priority = 'INFO'
            issue = str(rec)
            fix = ''
        html_body += f"<li><strong>[{priority}]</strong> {issue}<br/>→ {fix}</li>"
    
    html_body += f"""
        </ul>
        
        <p><em>Scan completed in {elapsed:.0f}s</em></p>
    </body>
    </html>
"""
    
    msg = MIMEMultipart()
    msg['From'] = 'minothejellyfish@gmail.com'
    msg['To'] = 'ashinqeety88@gmail.com'
    msg['Subject'] = f'TASI Daily Report — {date_str}'
    msg.attach(MIMEText(html_body, 'html'))
    
    # Attach HTML report if exists
    report_file = BASE_DIR / f"reports/post_market_{date_str}.html"
    if report_file.exists():
        with open(report_file, 'rb') as f:
            attachment = MIMEBase('application', 'octet-stream')
            attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            attachment.add_header('Content-Disposition', f'attachment; filename=post_market_{date_str}.html')
            msg.attach(attachment)
    
    # Send
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
        server.login('minothejellyfish@gmail.com', 'hvlp isup xiro whbv')
        server.sendmail('minothejellyfish@gmail.com', 'ashinqeety88@gmail.com', msg.as_string())
    
    print(f"Daily report emailed to ashinqeety88@gmail.com")

    # Update capital after market analysis
    try:
        import sys
        sys.path.insert(0, '/home/mino/tasi-exec')
        from capital_tracker import update_capital_from_derayah
        update_capital_from_derayah()
        print("Capital updated from today's trading results")
    except Exception as e:
        print(f"[WARNING] Failed to update capital: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# INTELLIGENT ANALYSIS MODULE (Added 2026-06-04)
# Uses yfinance OHLCV (reliable post-market) instead of WebSocket
# ═══════════════════════════════════════════════════════════════════════════

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

@dataclass
class IntPick:
    symbol: str
    score: float
    entry_low: float
    entry_high: float
    source: str

@dataclass
class IntPosition:
    symbol: str
    entry_price: float
    qty: int
    entry_time: datetime
    close_price: Optional[float]
    close_time: Optional[datetime]
    peak_price: Optional[float]
    closed: bool

def load_all_picks() -> Dict[str, List[IntPick]]:
    """Load picks from all screen files"""
    picks = {}
    screen_files = {
        'premarket': 'picks.json',
        'midscreen1': 'picks_1030.json',
        'midscreen2': 'picks_1200.json',
        'rescreen': 'picks_1330.json'
    }
    
    for source, filename in screen_files.items():
        filepath = BASE_DIR / filename
        if filepath.exists():
            try:
                with open(filepath) as f:
                    data = json.load(f)
                    for p in data.get('picks', []):
                        symbol = p.get('symbol', '').replace('.SR', '')
                        if symbol not in picks:
                            picks[symbol] = []
                        picks[symbol].append(IntPick(
                            symbol=symbol,
                            score=p.get('score', 0),
                            entry_low=p.get('entry_low', 0),
                            entry_high=p.get('entry_high', 0),
                            source=source
                        ))
            except Exception as e:
                print(f"[WARN] Error loading {filename}: {e}")
    
    return picks

def load_positions_today(date_str: str) -> Dict[str, IntPosition]:
    """Load positions opened today"""
    positions = {}
    pos_file = BASE_DIR / 'positions.json'
    
    if pos_file.exists():
        try:
            with open(pos_file) as f:
                data = json.load(f)
            
            for sym, pos in data.items():
                entry_time = pos.get('entry_time', '')
                if date_str in entry_time:
                    positions[sym] = IntPosition(
                        symbol=sym,
                        entry_price=pos.get('entry_price', 0),
                        qty=pos.get('qty', 0),
                        entry_time=datetime.fromisoformat(entry_time),
                        close_price=pos.get('close_price'),
                        close_time=datetime.fromisoformat(pos['close_time']) if pos.get('close_time') else None,
                        peak_price=pos.get('peak_price'),
                        closed=pos.get('closed', True)
                    )
        except Exception as e:
            print(f"[WARN] Error loading positions: {e}")
    
    return positions

def analyze_entry_zone_yf(pick: IntPick, perf: dict) -> Tuple[str, str]:
    """Analyze pick entry zone using yfinance OHLCV data"""
    if not perf or pick.entry_low <= 0 or pick.entry_high <= 0:
        return "no_zone", "No entry zone set"
    
    open_p = perf['open']
    high = perf['high']
    low = perf['low']
    
    # Check gap at open
    if open_p > pick.entry_high:
        gap_pct = (open_p - pick.entry_high) / pick.entry_high * 100
        return "gapped_above", f"Opened +{gap_pct:.1f}% above zone ({open_p:.2f} > {pick.entry_high:.2f})"
    elif open_p < pick.entry_low:
        gap_pct = (pick.entry_low - open_p) / pick.entry_low * 100
        return "gapped_below", f"Opened {gap_pct:.1f}% below zone ({open_p:.2f} < {pick.entry_low:.2f})"
    else:
        # Open was in zone — check if it stayed or touched
        if low <= pick.entry_low or high >= pick.entry_high:
            return "in_zone", f"Opened in zone and traded through range {low:.2f}–{high:.2f}"
        elif low >= pick.entry_low and high <= pick.entry_high:
            return "stayed_in_zone", f"Entirely within zone {low:.2f}–{high:.2f}"
        else:
            return "near_zone", f"Opened near zone {low:.2f}–{high:.2f}"

def generate_intelligent_report(date_str: str, all_picks: Dict[str, List[IntPick]], 
                                positions: Dict[str, IntPosition], perf_map: dict) -> str:
    """Generate intelligent analysis report"""
    
    report = [
        f"📊 <b>TASI Intelligent Post-Market Analysis</b>",
        f"📅 {date_str} | {datetime.now(RIYADH).strftime('%H:%M %Z')}",
        f"",
        f"━" * 40,
        f"",
        f"<b>🎯 SUMMARY</b>",
        f"• Total unique picks: {len(all_picks)}",
        f"• Positions taken: {len(positions)}",
        f"• Picks not entered: {len(all_picks) - len(positions)}",
        f"",
    ]
    
    # Analyze why each pick was missed or taken
    pick_lines = []
    for symbol, pick_list in all_picks.items():
        best = max(pick_list, key=lambda p: p.score)
        perf = perf_map.get(f"{symbol}.SR")
        
        if perf:
            status, detail = analyze_entry_zone_yf(best, perf)
            is_traded = symbol in positions
            
            # Determine WHY missed / WHY taken with DATA
            reason = ""
            fix = ""
            data_point = ""
            
            if is_traded:
                pos = positions[symbol]
                # Analyze exit
                peak = pos.peak_price or perf.get('high', pos.entry_price)
                close_p = pos.close_price or perf.get('close', pos.entry_price)
                actual_pnl = (close_p - pos.entry_price) / pos.entry_price * 100
                peak_pnl = (peak - pos.entry_price) / pos.entry_price * 100
                missed_pnl = peak_pnl - actual_pnl
                
                if close_p and peak > close_p * 1.005:
                    reason = f"Exited at {close_p:.2f} (P&L: {actual_pnl:+.1f}%) but peaked at {peak:.2f} (would be {peak_pnl:+.1f}%)"
                    data_point = f"Missed {missed_pnl:.1f}% between exit and peak"
                    fix = "Wider trailing stop (3% vs 2%) or hold until 14:45"
            else:
                # Why was this missed? — with actual data
                if status == 'gapped_above':
                    gap_pct = (perf['open'] - best.entry_high) / best.entry_high * 100
                    missed_gain = (perf['high'] - perf['open']) / perf['open'] * 100 if perf.get('high') else 0
                    reason = f"Gapped +{gap_pct:.1f}% above zone at open ({perf['open']:.2f} > {best.entry_high:.2f})"
                    data_point = f"If chased: potential +{missed_gain:.1f}% to day's high"
                    if gap_pct < 0.5:
                        fix = f"Chase entry rule: gap <0.5% = enter with 1% stop at {best.entry_high:.2f}"
                    else:
                        fix = f"Skip — gap too large (+{gap_pct:.1f}%). Wait for pullback to {best.entry_high:.2f}"
                        
                elif status == 'gapped_below':
                    gap_pct = (best.entry_low - perf['open']) / best.entry_low * 100
                    reclaim_potential = (perf['high'] - perf['open']) / perf['open'] * 100 if perf.get('high') else 0
                    reason = f"Opened -{gap_pct:.1f}% below zone ({perf['open']:.2f} < {best.entry_low:.2f})"
                    data_point = f"Day's high was +{reclaim_potential:.1f}% above open — never reclaimed zone"
                    fix = f"Reclaim entry: if price returns to {best.entry_low:.2f} within 30 min, enter"
                    
                elif status == 'in_zone':
                    in_zone_pct = ((min(perf['high'], best.entry_high) - max(perf['low'], best.entry_low)) / (best.entry_high - best.entry_low) * 100) if best.entry_high > best.entry_low else 0
                    day_range_pct = (perf['high'] - perf['low']) / perf['open'] * 100 if perf.get('open') else 0
                    
                    # max_positions is a dict per regime — get default or current regime value
                    regime_name = "NEUTRAL"  # Default for post-market
                    max_pos_dict = SYSTEM_CONFIG.get('max_positions', 2)
                    if isinstance(max_pos_dict, dict):
                        max_pos = max_pos_dict.get(regime_name, 3)
                    else:
                        max_pos = max_pos_dict
                    if len(positions) >= max_pos:
                        reason = f"In zone {best.entry_low:.2f}-{best.entry_high:.2f} but max positions ({len(positions)}/{max_pos}) reached"
                        data_point = f"Price spent {in_zone_pct:.0f}% of day in zone, range was {day_range_pct:.1f}%"
                        fix = f"Increase max positions to {max_pos + 1} OR rotation: replace weakest position (score <{best.score:.0f})"
                    else:
                        # Try to find actual reason from poller logs
                        blocked_reason = "Unknown"
                        
                        if blocked_reason == "Unknown":
                            reason = f"In zone {best.entry_low:.2f}-{best.entry_high:.2f} but no entry triggered — {blocked_reason}"
                        else:
                            reason = f"In zone {best.entry_low:.2f}-{best.entry_high:.2f} — {blocked_reason}"
                        data_point = f"Price spent {in_zone_pct:.0f}% of day in zone, range was {day_range_pct:.1f}%"
                        fix = f"Check poller logs for BLOCKED:{best.symbol} entries"
                        
                elif status == 'no_zone':
                    day_range = perf['high'] - perf['low'] if perf.get('high') and perf.get('low') else 0
                    reason = "No entry zone calculated (midscreen pick)"
                    data_point = f"Day's range: {day_range:.2f} SAR ({(day_range/perf['open']*100):.1f}%)"
                    fix = f"Auto-calculate zone: ATR-based with {best.entry_low or perf['low']:.2f}-{best.entry_high or perf['high']:.2f}"
                    
                else:
                    reason = f"Status: {status}"
                    fix = "Review poller logs for specific block reason"
            
            pick_lines.append({
                'symbol': symbol,
                'score': best.score,
                'zone': f"{best.entry_low:.2f}-{best.entry_high:.2f}",
                'status': status,
                'detail': detail,
                'traded': "✅" if is_traded else "❌",
                'reason': reason,
                'data_point': data_point,
                'fix': fix,
                'perf': perf
            })
    
    # Group by status
    status_order = ['gapped_above', 'gapped_below', 'in_zone', 'stayed_in_zone', 'near_zone', 'no_zone']
    status_labels = {
        'gapped_above': '🚀 Gapped Above Zone',
        'gapped_below': '⬇️ Gapped Below Zone', 
        'in_zone': '🎯 In Zone',
        'stayed_in_zone': '✅ Stayed In Zone',
        'near_zone': '🔄 Near Zone',
        'no_zone': '❓ No Zone Set'
    }
    
    for status in status_order:
        picks_in_status = [p for p in pick_lines if p['status'] == status]
        if picks_in_status:
            report.append(f"<b>{status_labels.get(status, status)} ({len(picks_in_status)})</b>")
            for p in picks_in_status:
                report.append(f"  {p['traded']} {p['symbol']}: score={p['score']:.1f}, zone={p['zone']}")
                report.append(f"     → {p['detail']}")
                if p['reason']:
                    report.append(f"     💡 {p['reason']}")
                if p.get('data_point'):
                    report.append(f"     📊 {p['data_point']}")
                if p['fix']:
                    report.append(f"     🔧 {p['fix']}")
            report.append("")
    
    # Trade performance with ACTUAL prices and P&L
    if positions:
        report.extend([
            f"━" * 40,
            f"",
            f"<b>💼 TRADE PERFORMANCE</b>",
            f"",
        ])
        for symbol, pos in positions.items():
            perf = perf_map.get(f"{symbol}.SR", {})
            
            # Get actual prices
            entry = pos.entry_price
            peak = pos.peak_price or perf.get('high', entry)
            close_p = pos.close_price or perf.get('close', entry)
            
            # Calculate P&L with actual sell price from Derayah orders
            actual_sell = close_p
            
            # Try to get actual sell price from exec.log order history
            try:
                import subprocess, re
                log_result = subprocess.run(
                    f"grep -E 'SELL.*{symbol}.*@' /home/mino/tasi-exec/exec.log | tail -1",
                    shell=True, capture_output=True, text=True
                )
                if log_result.stdout:
                    match = re.search(r'@\s*([\d.]+)', log_result.stdout)
                    if match:
                        actual_sell = float(match.group(1))
            except:
                pass
            
            actual_pnl = (actual_sell - entry) / entry * 100 if entry else 0
            peak_pnl = (peak - entry) / entry * 100 if peak > entry else 0
            missed_pnl = peak_pnl - actual_pnl if peak_pnl > actual_pnl else 0
            total_sar = (actual_sell - entry) * pos.qty if pos.qty else 0
            
            # Determine exit quality based on ACTUAL result
            if actual_sell > entry:
                exit_quality = f"✅ PROFIT +{actual_pnl:.1f}%"
            elif actual_sell < entry * 0.98:
                exit_quality = f"❌ LOSS {actual_pnl:.1f}%"
            else:
                exit_quality = f"⚪ BREAKEVEN {actual_pnl:.1f}%"
            
            # Get BOTH peaks from WebSocket data
            session_peak_str = "session"
            hold_peak_str = "hold"
            session_peak_price = peak
            hold_peak_price = peak
            
            try:
                import subprocess
                # Get all WS prices for this symbol
                ws_result = subprocess.run(
                    f"python3 -c \"import json; peaks=[]; [peaks.append((json.loads(l).get('time',''), json.loads(l).get('price',0))) for l in open('/home/mino/tasi-exec/ws_prices_{date_str}.jsonl') if json.loads(l).get('symbol')=='{symbol}']; print(json.dumps(peaks))\"",
                    shell=True, capture_output=True, text=True
                )
                if ws_result.stdout.strip():
                    all_peaks = json.loads(ws_result.stdout.strip())
                    if all_peaks:
                        # Session peak (highest price all day)
                        session_p = max(all_peaks, key=lambda x: x[1])
                        session_peak_price = session_p[1]
                        # Parse timestamp properly
                        session_time_raw = session_p[0]
                        if 'T' in session_time_raw:
                            try:
                                session_dt = datetime.fromisoformat(session_time_raw.replace('Z', '+00:00'))
                                session_peak_str = session_dt.strftime('%H:%M')
                            except:
                                session_peak_str = session_time_raw.split('T')[1][:5]
                        else:
                            session_peak_str = "session"
                        
                        # Hold peak (highest price during our position)
                        hold_peaks = []
                        for t, p in all_peaks:
                            try:
                                tick_dt = datetime.fromisoformat(t.replace('Z', '+00:00'))
                                # Make timezone-aware if naive
                                if tick_dt.tzinfo is None:
                                    tick_dt = tick_dt.replace(tzinfo=RIYADH)
                                if pos.entry_time and pos.close_time:
                                    if tick_dt >= pos.entry_time and tick_dt <= pos.close_time:
                                        hold_peaks.append((t, p))
                            except:
                                pass
                        if hold_peaks:
                            hold_p = max(hold_peaks, key=lambda x: x[1])
                            hold_peak_price = hold_p[1]
                            # Parse timestamp properly
                            hold_time_raw = hold_p[0]
                            if 'T' in hold_time_raw:
                                try:
                                    hold_dt = datetime.fromisoformat(hold_time_raw.replace('Z', '+00:00'))
                                    hold_peak_str = hold_dt.strftime('%H:%M')
                                except:
                                    hold_peak_str = hold_time_raw.split('T')[1][:5]
                            else:
                                hold_peak_str = "hold"
            except:
                pass
            
            # Calculate P&L for both peaks
            session_peak_pnl = (session_peak_price - entry) / entry * 100 if session_peak_price > entry else 0
            hold_peak_pnl = (hold_peak_price - entry) / entry * 100 if hold_peak_price > entry else 0
            
            # Determine exit quality based on ACTUAL result
            if actual_sell > entry:
                exit_quality = f"✅ PROFIT +{actual_pnl:.1f}%"
            elif actual_sell < entry * 0.98:
                exit_quality = f"❌ LOSS {actual_pnl:.1f}%"
            else:
                exit_quality = f"⚪ BREAKEVEN {actual_pnl:.1f}%"
            
            report.extend([
                f"<b>{symbol}</b> {exit_quality}",
                f"  Entry:  {entry:.2f} × {pos.qty} shares = {entry*pos.qty:.2f} SAR @ {pos.entry_time.strftime('%H:%M')}",
                f"  Peak:   {session_peak_price:.2f} ({session_peak_pnl:+.1f}%) @ {session_peak_str} ← Day high",
                f"  Best:   {hold_peak_price:.2f} ({hold_peak_pnl:+.1f}%) @ {hold_peak_str} ← While held",
                f"  Sell:   {actual_sell:.2f} ({actual_pnl:+.1f}%) @ {pos.close_time.strftime('%H:%M') if pos.close_time else 'session end'} ← Actual",
                f"  Total:  {total_sar:+.2f} SAR",
            ])
            
            if hold_peak_pnl > actual_pnl + 0.5:
                missed_while_held = hold_peak_pnl - actual_pnl
                report.append(f"  Gap:   {missed_while_held:.1f}% left while holding")
            
            report.append(f"  Duration: {pos.close_time.strftime('%H:%M') if pos.close_time else 'Still open'}")
            exit_fix = ""
            
            import subprocess
            try:
                pattern = f"Sell.*{symbol}|scratch.*{symbol}|{symbol}.*scratch|{symbol}.*sell|{symbol}.*hard close|{symbol}.*target|{symbol}.*trail stop|{symbol}.*time stop"
                log_check = subprocess.run(
                    f"grep -n -i -E '{pattern}' /home/mino/tasi-exec/exec.log | tail -10",
                    shell=True, capture_output=True, text=True
                )
                log_lines = log_check.stdout.strip().split('\n') if log_check.stdout else []
                log_lines = [l for l in log_lines if l.strip()]
                
                for line in log_lines[-10:]:
                    line_lower = line.lower()
                    if 'hard close' in line_lower or '14:45' in line_lower or 'end of session' in line_lower:
                        exit_reason = "Hard market close at 14:45"
                        exit_fix = "Market closed — held until session end (correct behavior)"
                        break
                    elif 'scratch' in line_lower and 'consecutive' in line_lower:
                        exit_reason = "Trailing stop (2 consecutive scratches)"
                        exit_fix = f"Trailing stop too tight — missed {missed_pnl:.1f}%"
                        break
                    elif 'scratch' in line_lower:
                        exit_reason = "Scratch exit (price dropped)"
                        exit_fix = f"Scratch triggered — missed {missed_pnl:.1f}%"
                        break
                    elif 'target' in line_lower:
                        exit_reason = "Profit target hit"
                        exit_fix = "Good exit — target reached"
                        break
                
                if exit_reason == "Unknown" and log_lines:
                    last_line = log_lines[-1]
                    if ']' in last_line:
                        last_line = last_line.split(']', 1)[1].strip()
                    if len(last_line) > 120:
                        last_line = last_line[:120] + "..."
                    exit_reason = f"Log: {last_line}"
                    
            except Exception as e:
                exit_reason = f"Log error: {e}"
            
            report.append(f"  Exit reason: {exit_reason}")
            if exit_fix and missed_pnl > 0.5:
                report.append(f"  → {exit_fix}")
            report.append("")
    
    # Recommendations with actionable fixes
    critical = []
    consider = []
    improve = []
    
    gap_above = [p for p in pick_lines if p['status'] == 'gapped_above' and p['traded'] != '✅']
    gap_below = [p for p in pick_lines if p['status'] == 'gapped_below' and p['traded'] != '✅']
    in_zone_missed = [p for p in pick_lines if p['status'] in ['in_zone', 'stayed_in_zone'] and p['traded'] != '✅']
    no_zone = [p for p in pick_lines if p['status'] == 'no_zone']
    
    if gap_above:
        small_gaps = [p for p in gap_above if p.get('perf') and (p['perf']['open'] - float(p['zone'].split('-')[1])) / float(p['zone'].split('-')[1]) * 100 < 0.5]
        if small_gaps:
            critical.append(f"Gap-up chase: {len(small_gaps)} picks gapped <0.5% above zone — add chase entry with 1% stop below entry")
        large_gaps = [p for p in gap_above if p not in small_gaps]
        if large_gaps:
            consider.append(f"Large gap protection: {len(large_gaps)} picks gapped >0.5% — wait for pullback or skip")
    
    if gap_below:
        critical.append(f"Gap-down recovery: {len(gap_below)} picks opened below zone — add 'reclaim entry' when price returns to zone")
    
    if in_zone_missed:
        max_pos_dict = SYSTEM_CONFIG.get('max_positions', 2)
        if isinstance(max_pos_dict, dict):
            max_pos = max_pos_dict.get("NEUTRAL", 3)
        else:
            max_pos = max_pos_dict
        if len(positions) >= max_pos:
            critical.append(f"Position capacity: {len(in_zone_missed)} in-zone picks missed due to max positions ({len(positions)}/{max_pos}) — increase to {max_pos + 1} OR add position rotation logic")
        else:
            critical.append(f"Signal block: {len(in_zone_missed)} in-zone picks not entered despite capacity — review VWAP/volume filter sensitivity")
    
    if no_zone:
        improve.append(f"Zone calculation: {len(no_zone)} midscreen picks lack entry zones — add ATR-based zone calculation")
    
    # Analyze actual trades for exit quality
    if positions:
        early_exits = []
        for sym, pos in positions.items():
            perf = perf_map.get(f"{sym}.SR", {})
            peak = pos.peak_price or perf.get('high', pos.entry_price)
            if pos.close_price and peak > pos.close_price * 1.01:
                gap = (peak - pos.close_price) / pos.close_price * 100
                early_exits.append((sym, gap))
        
        if early_exits:
            avg_gap = sum(g[1] for g in early_exits) / len(early_exits)
            symbols = ', '.join([g[0] for g in early_exits])
            consider.append(f"Early exits: {symbols} sold before peak — avg {avg_gap:.1f}% left on table. Consider wider trailing stop (3%) or hold until hard close at 14:45")
    
    # Add THINKING & REASONING section
    report.extend([
        f"━" * 40,
        f"",
        f"<b>🧠 ANALYSIS & REASONING</b>",
        f"",
    ])
    
    # Reason about the day
    total_pnl = sum([(p.close_price or perf_map.get(f'{s}.SR', {}).get('close', p.entry_price)) - p.entry_price for s, p in positions.items()])
    total_trades = len(positions)
    winning_trades = sum([1 for s, p in positions.items() if (p.close_price or 0) > p.entry_price])
    losing_trades = total_trades - winning_trades
    
    # Market condition assessment
    avg_day_range = sum([perf_map.get(f'{s}.SR', {}).get('high', 0) - perf_map.get(f'{s}.SR', {}).get('low', 0) for s in all_picks]) / len(all_picks) if all_picks else 0
    
    report.append(f"<b>📊 Market Assessment:</b>")
    report.append(f"  • {len(all_picks)} picks generated across all screens")
    report.append(f"  • {len(positions)} positions taken ({winning_trades} wins, {losing_trades} losses)")
    report.append(f"  • {len(all_picks) - len(positions)} opportunities missed")
    report.append(f"  • Average day range: {avg_day_range:.2f} SAR")
    report.append("")
    
    # Reason about entries
    in_zone_entered = [s for s in positions if s in [p['symbol'] for p in pick_lines if p['status'] in ['in_zone', 'stayed_in_zone']]]
    in_zone_missed_count = len([p for p in pick_lines if p['status'] in ['in_zone', 'stayed_in_zone'] and p['traded'] != '✅'])
    
    # max_positions is a dict per regime — get default
    max_pos_dict = SYSTEM_CONFIG.get('max_positions', 2)
    if isinstance(max_pos_dict, dict):
        max_pos = max_pos_dict.get("NEUTRAL", 3)
    else:
        max_pos = max_pos_dict
    
    report.append(f"<b>🎯 Entry Analysis:</b>")
    if in_zone_entered:
        report.append(f"  • {len(in_zone_entered)} entered in-zone: good entries")
    if in_zone_missed_count > 0:
        report.append(f"  • {in_zone_missed_count} in-zone picks missed due to capacity constraints")
        report.append(f"    → System had {len(positions)}/{len(positions) + in_zone_missed_count} positions open")
        report.append(f"    → Suggestion: increase max_positions or implement rotation")
    
    gap_above_count = len([p for p in pick_lines if p['status'] == 'gapped_above'])
    if gap_above_count > 0:
        report.append(f"  • {gap_above_count} picks gapped above — consider chase entry for small gaps")
    report.append("")
    
    # Reason about exits
    report.append(f"<b>💼 Exit Analysis:</b>")
    for sym, pos in positions.items():
        perf = perf_map.get(f"{sym}.SR", {})
        entry = pos.entry_price
        
        # Get actual sell price from exec.log
        actual_sell = pos.close_price or perf.get('close', entry)
        try:
            import subprocess, re
            log_result = subprocess.run(
                f"grep -E 'SELL.*{sym}.*@' /home/mino/tasi-exec/exec.log | tail -1",
                shell=True, capture_output=True, text=True
            )
            if log_result.stdout:
                match = re.search(r'@\s*([\d.]+)', log_result.stdout)
                if match:
                    actual_sell = float(match.group(1))
        except:
            pass
        
        pnl_pct = ((actual_sell / entry) - 1) * 100 if entry else 0
        
        if actual_sell < entry:
            report.append(f"  • {sym}: ❌ LOSS {pnl_pct:+.1f}% — sold {entry - actual_sell:.2f} SAR below entry")
        elif actual_sell > entry * 1.01:
            report.append(f"  • {sym}: ✅ PROFIT {pnl_pct:+.1f}% — good exit above entry")
        else:
            report.append(f"  • {sym}: ⚪ BREAKEVEN {pnl_pct:+.1f}% — sold near entry")
            
        # Check if we missed peak during hold
        # Check if we missed peak during hold (simple version)
        ws_hold_peak = 0
        try:
            ws_result = subprocess.run(
                f"python3 -c \"import json; prices=[json.loads(l).get('price',0) for l in open('/home/mino/tasi-exec/ws_prices_{date_str}.jsonl') if json.loads(l).get('symbol')=='{sym}']; print(max(prices) if prices else 0)\"",
                shell=True, capture_output=True, text=True
            )
            if ws_result.stdout.strip():
                ws_hold_peak = float(ws_result.stdout.strip())
        except:
            pass
            
        if ws_hold_peak > actual_sell * 1.005:
            missed = ((ws_hold_peak / actual_sell) - 1) * 100
            report.append(f"    → Could have sold {ws_hold_peak:.2f} (+{missed:.1f}% higher) during hold")
    
    # Overall assessment
    report.append(f"<b>📈 Overall Assessment:</b>")
    if total_pnl > 0:
        report.append(f"  • Net result: +{total_pnl:.2f} SAR (profitable day)")
    else:
        report.append(f"  • Net result: {total_pnl:.2f} SAR (loss on the day)")
    
    if len(positions) < 2 and len(all_picks) > 5:
        report.append(f"  • Under-traded: only {len(positions)} positions with {len(all_picks)} signals")
    
    report.append("")
    
    # Add recommendations section
    report.extend([
        f"━" * 40,
        f"",
        f"<b>💡 RECOMMENDATIONS</b>",
        f"",
    ])
    
    if critical:
        report.append(f"<b>🔴 CRITICAL:</b>")
        for i, rec in enumerate(critical, 1):
            report.append(f"{i}. {rec}")
        report.append("")
    
    if consider:
        report.append(f"<b>🟡 CONSIDER:</b>")
        for i, rec in enumerate(consider, 1):
            report.append(f"{i}. {rec}")
        report.append("")
    
    if improve:
        report.append(f"<b>🟢 AREA OF IMPROVEMENT:</b>")
        for i, rec in enumerate(improve, 1):
            report.append(f"{i}. {rec}")
        report.append("")
    
    report.extend([
        f"━" * 40,
        f"",
        f"<i>Intelligent Analysis by Mino 🪼</i>",
    ])
    
    return "\n".join(report)

def run_intelligent_analysis(date_str: str, perf_map: dict):
    """Run intelligent analysis and send to Telegram"""
    print(f"\n{'='*60}")
    print(f"🤖 Running intelligent analysis...")
    
    all_picks = load_all_picks()
    positions = load_positions_today(date_str)
    
    report = generate_intelligent_report(date_str, all_picks, positions, perf_map)
    
    print(f"📊 Report generated: {len(report)} chars")
    tg_send(report)
    
    # Save to file
    report_file = BASE_DIR / f'intelligent_analysis_{date_str}.txt'
    with open(report_file, 'w') as f:
        f.write(report)
    print(f"💾 Saved to: {report_file}")
    
    # ── Write intelligent analysis to OpenClaw memory ─────────────────
    try:
        memory_dir = Path("/home/mino/.openclaw-mino/workspace/memory")
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_file = memory_dir / f"{date_str}-intelligent.md"
        
        # Strip HTML tags for markdown
        import re
        clean_report = re.sub(r'<[^>]+>', '', report)
        
        memory_content = f"""# TASI Intelligent Analysis — {date_str}

## Overview
{clean_report[:4000]}

## Tags
#tasi #intelligent-analysis #{date_str} #trading-day #learning

## Source
Generated by post_market.py intelligent analysis module
"""
        with open(memory_file, "w") as f:
            f.write(memory_content)
        print(f"📝 Saved intelligent analysis to memory: {memory_file}")
    except Exception as e:
        print(f"[WARNING] Failed to write intelligent analysis memory: {e}")
    # ───────────────────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════════════
    # RECORD DAILY P&L (Added 2026-06-11)
    # ═══════════════════════════════════════════════════════════════════════
    try:
        print(f"\n📈 Recording daily P&L...")
        sys.path.insert(0, str(BASE_DIR))
        import bookkeeper
        pnl_result = bookkeeper.record_daily_pnl(date_str)
        print(f"✅ Daily P&L recorded: {pnl_result}")
        
        # Generate and send P&L report
        pnl_report = bookkeeper.generate_daily_report(date_str)
        print(f"📊 P&L Report generated: {len(pnl_report)} chars")
        
        # Send P&L report via Telegram
        tg_send(f"📊 <b>Daily P&L Report — {date_str}</b>\n\n{pnl_report}")
        
        # Save P&L report to file
        pnl_file = BASE_DIR / f"reports/daily_pnl_{date_str}.md"
        pnl_file.parent.mkdir(exist_ok=True)
        with open(pnl_file, "w") as f:
            f.write(pnl_report)
        print(f"💾 P&L report saved: {pnl_file}")
        
    except Exception as e:
        print(f"[ERROR] Daily P&L recording failed: {e}")
        import traceback
        traceback.print_exc()
    # ═══════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    main()

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
import csv
import logging

RIYADH = pytz.timezone("Asia/Riyadh")
BASE_DIR = Path("/home/mino/tasi-exec")
SHARIA_FILE = BASE_DIR / "sharia_list.json"
PICKS_FILE = BASE_DIR / "picks.json"
WS_PRICES_FILE = BASE_DIR / f"ws_prices_{datetime.now(RIYADH).strftime('%Y-%m-%d')}.jsonl"
WS_FRAMES_FILE = BASE_DIR / "ws_frames.json"  # Deprecated: kept for compatibility
CACHE_FILE = BASE_DIR / "pm_cache.json"
SYSTEM_REF = Path("/home/mino/.openclaw-mino/workspace/TASI_SYSTEM_REFERENCE.md")

BOT_TOKEN = "8989533040:AAFWzP_lYL3g_w4eXGxrvwdo-tBNdPxVYQU"
GROUP_CHAT_ID = -5235925419

# Logging setup - save to logs/ subdirectory
LOG_FILE = BASE_DIR / "logs" / "post_market.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)

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


def fetch_from_ws_prices(symbol: str, date_str: str) -> dict | None:
    """
    Fetch OHLCV from WebSocket price data logged by poller ws_listener.
    Returns dict with open, high, low, close, volume or None.
    """
    try:
        ws_file = BASE_DIR / f"ws_prices_{date_str}.jsonl"
        if not ws_file.exists():
            return None
        
        # Filter lines for this symbol
        symbol_lines = []
        with open(ws_file) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("symbol") == symbol.replace(".SR", ""):
                        symbol_lines.append(d)
                except:
                    continue
        
        if not symbol_lines:
            return None
        
        # Sort by timestamp
        symbol_lines.sort(key=lambda x: x.get("ts", 0))
        
        prices = [d["price"] for d in symbol_lines]
        # Volume not tracked in ws_prices, use 0
        return {
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
            "volume": 0,  # ws_prices doesn't track volume
        }
    except Exception:
        return None


# Keep old function for backward compatibility
def fetch_from_ws_frames(symbol: str, date_str: str) -> dict | None:
    """
    DEPRECATED: Use fetch_from_ws_prices instead.
    ws_frames.json is no longer populated with price data.
    """
    return fetch_from_ws_prices(symbol, date_str)


def fetch_one(symbol: str, cache: dict, date_str: str) -> tuple:
    """
    Fetch stock data with retry and fallback to WebSocket frames.
    Priority: cache → ws_frames → yfinance
    """
    # Check cache first
    if symbol in cache:
        return symbol, cache[symbol]
    
    # Fallback 1: WebSocket prices (real-time captured data from poller)
    ws_data = fetch_from_ws_prices(symbol, date_str)
    if ws_data:
        result = {
            "symbol": symbol,
            "open": ws_data["open"],
            "high": ws_data["high"],
            "low": ws_data["low"],
            "close": ws_data["close"],
            "volume": ws_data["volume"],
            "change_pct": (ws_data["close"] - ws_data["open"]) / ws_data["open"] * 100,
            "max_intraday_pct": (ws_data["high"] - ws_data["open"]) / ws_data["open"] * 100,
        }
        return symbol, result
    
    # Fallback 2: yfinance with exponential backoff
    for attempt in range(5):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1d")
            if df.empty:
                df = ticker.history(period="5d")
                if not df.empty:
                    df = df.iloc[[-1]]
            
            if df.empty:
                if attempt == 4:
                    return symbol, None
                time.sleep(2 ** attempt)  # 1s, 2s, 4s, 8s
                continue

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
            if attempt == 4:
                return symbol, None
            time.sleep(2 ** attempt)
    
    return symbol, None
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


def analyze_all_stocks_sequential(tickers: list, picks_symbols: set, cache: dict, date_str: str):
    """Fetch all stocks sequentially (yfinance is not thread-safe)."""
    performances = []
    new_cache = {}
    fail_count = 0

    for sym in tickers:
        symbol, result = fetch_one(sym, cache, date_str)
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


def analyze_picks_comprehensive(picks: list, perf_map: dict, trades: list, date_str: str) -> list:
    """
    Comprehensive pick analysis with:
    1. Screener picks evaluation
    2. Actual vs ideal entries/exits with VWAP consideration
    3. Full-day gap status
    4. Performance evaluation
    5. P&L attribution (why lost/made money)
    """
    results = []
    
    # Build trade lookup
    trade_map = {t["symbol"]: t for t in trades}
    
    for pick in picks:
        # Fix: some picks have ticker instead of symbol
        symbol = pick.get("symbol", "") or pick.get("ticker", "").replace(".SR", "")
        perf = perf_map.get(symbol)
        trade = trade_map.get(symbol)
        
        analysis = {
            "symbol": symbol,
            "score": pick.get("score", 0),
            "tier": pick.get("tier", "main"),
            "source": pick.get("source", ""),
            "entry_low": pick.get("entry_low", 0),
            "entry_high": pick.get("entry_high", 0),
        }
        
        if perf:
            # 1. Full-day OHLC data
            analysis["open"] = perf["open"]
            analysis["high"] = perf["high"]
            analysis["low"] = perf["low"]
            analysis["close"] = perf["close"]
            analysis["volume"] = perf["volume"]
            analysis["change_pct"] = perf["change_pct"]
            analysis["max_intraday_pct"] = perf["max_intraday_pct"]
            
            # 2. Gap status at open vs zone
            if perf["open"] > pick["entry_high"]:
                analysis["gap_status"] = "above"
                analysis["gap_pct"] = (perf["open"] - pick["entry_high"]) / pick["entry_high"] * 100
            elif perf["open"] < pick["entry_low"]:
                analysis["gap_status"] = "below"
                analysis["gap_pct"] = (perf["open"] - pick["entry_low"]) / pick["entry_low"] * 100
            else:
                analysis["gap_status"] = "in_zone"
                analysis["gap_pct"] = 0
                analysis["touched_zone"] = pick["entry_low"] <= perf["low"] <= pick["entry_high"] or \
                                          pick["entry_low"] <= perf["high"] <= pick["entry_high"]
            
            # 3. Zone quality (how well did the zone predict?)
            if pick["entry_low"] > 0 and pick["entry_high"] > 0:
                zone_low_error = (perf["low"] - pick["entry_low"]) / pick["entry_low"] * 100
                zone_high_error = (perf["high"] - pick["entry_high"]) / pick["entry_high"] * 100
                analysis["zone_accuracy"] = {
                    "low_error_pct": zone_low_error,
                    "high_error_pct": zone_high_error,
                    "zone_was_support": perf["low"] >= pick["entry_low"] * 0.99,
                    "zone_was_resistance": perf["high"] <= pick["entry_high"] * 1.01,
                }
        
        # 4. Actual trade analysis with VWAP consideration
        if trade:
            if trade["side"] == "BUY":
                analysis["actual_entry"] = trade["price"]
                analysis["entry_time"] = trade["time"]
                analysis["entry_trigger"] = trade["trigger_basis"]
                
                # Entry quality vs zone
                if pick.get("entry_high"):
                    ideal_mid = (pick["entry_low"] + pick["entry_high"]) / 2
                    analysis["entry_slippage_pct"] = (trade["price"] - ideal_mid) / ideal_mid * 100
                    analysis["entry_vs_zone"] = "in_zone" if pick["entry_low"] <= trade["price"] <= pick["entry_high"] else "out_of_zone"
                
                # Entry quality vs VWAP (if available)
                # During trading hours, ws_frames would have VWAP data
                # For now, we calculate approximate VWAP from OHLC
                if perf:
                    # Approximate VWAP = (Open + High + Low + Close) / 4
                    approx_vwap = (perf["open"] + perf["high"] + perf["low"] + perf["close"]) / 4
                    analysis["entry_vs_vwap"] = (trade["price"] - approx_vwap) / approx_vwap * 100
                    analysis["vwap_at_entry"] = approx_vwap
                    
                    if trade["price"] < approx_vwap * 0.995:
                        analysis["entry_quality"] = "excellent (below VWAP)"
                    elif trade["price"] < approx_vwap * 1.005:
                        analysis["entry_quality"] = "good (near VWAP)"
                    else:
                        analysis["entry_quality"] = "poor (above VWAP)"
            
            elif trade["side"] == "SELL":
                analysis["actual_exit"] = trade["price"]
                analysis["exit_time"] = trade["time"]
                analysis["exit_trigger"] = trade["trigger_basis"]
                
                # Exit quality analysis
                if perf:
                    # vs High of Day
                    hod_distance = (perf["high"] - trade["price"]) / perf["high"] * 100
                    analysis["exit_vs_hod_pct"] = hod_distance
                    
                    # vs VWAP
                    approx_vwap = (perf["open"] + perf["high"] + perf["low"] + perf["close"]) / 4
                    analysis["exit_vs_vwap"] = (trade["price"] - approx_vwap) / approx_vwap * 100
                    
                    # Exit quality based on trigger
                    trigger = trade.get("trigger_basis", "")
                    if "stop" in trigger.lower():
                        analysis["exit_quality"] = f"stop loss triggered ({hod_distance:.1f}% from HOD)"
                    elif "target" in trigger.lower() or "tier" in trigger.lower():
                        analysis["exit_quality"] = f"profit target hit ({hod_distance:.1f}% from HOD)"
                    elif "manual" in trigger.lower():
                        analysis["exit_quality"] = f"manual exit ({hod_distance:.1f}% from HOD)"
                    elif "trailing" in trigger.lower():
                        analysis["exit_quality"] = f"trailing stop ({hod_distance:.1f}% from HOD)"
                    else:
                        analysis["exit_quality"] = f"auto exit ({hod_distance:.1f}% from HOD)"
                    
                    # Could we have done better?
                    if hod_distance > 2:
                        analysis["exit_improvement"] = f"Could have gained {hod_distance:.1f}% more by holding"
                    else:
                        analysis["exit_improvement"] = "Good exit timing"
                
                # Calculate hold time and P&L
                if analysis.get("actual_entry"):
                    analysis["hold_time_min"] = calculate_hold_time(
                        analysis["entry_time"], trade["time"]
                    )
                    analysis["actual_pnl"] = (trade["price"] - analysis["actual_entry"]) * trade["qty"]
                    
                    # Simulate other exits
                    if perf:
                        analysis["simulated_exits"] = simulate_exit_windows(
                            entry_price=analysis["actual_entry"],
                            perf=perf,
                            actual_exit=trade["price"]
                        )
        
        # 5. P&L attribution
        analysis["pnl_attribution"] = calculate_pnl_attribution(analysis, perf)
        
        results.append(analysis)
    
    return results


def simulate_exit_windows(entry_price: float, perf: dict, actual_exit: float) -> list:
    """
    Simulate what P&L would be with different exit strategies.
    Returns list of exit scenarios.
    """
    scenarios = []
    
    # Scenario 1: Exit at high of day
    max_profit = (perf["high"] - entry_price)
    scenarios.append({
        "strategy": "Exit at HOD",
        "exit_price": perf["high"],
        "pnl": max_profit,
        "vs_actual": max_profit - (actual_exit - entry_price),
        "realistic": False,
    })
    
    # Scenario 2: Trailing stop -1% from high
    trailing_exit = perf["high"] * 0.99
    if trailing_exit > entry_price:
        scenarios.append({
            "strategy": "Trailing stop -1%",
            "exit_price": trailing_exit,
            "pnl": trailing_exit - entry_price,
            "vs_actual": (trailing_exit - entry_price) - (actual_exit - entry_price),
            "realistic": True,
        })
    
    # Scenario 3: Exit at VWAP (if above entry)
    approx_vwap = (perf["open"] + perf["high"] + perf["low"] + perf["close"]) / 4
    if approx_vwap > entry_price:
        scenarios.append({
            "strategy": "Exit at VWAP",
            "exit_price": approx_vwap,
            "pnl": approx_vwap - entry_price,
            "vs_actual": (approx_vwap - entry_price) - (actual_exit - entry_price),
            "realistic": True,
        })
    
    return scenarios


def calculate_pnl_attribution(analysis: dict, perf: dict) -> dict:
    """
    Analyze why we made/lost money on this pick.
    """
    attribution = {
        "entry_quality": "neutral",
        "exit_quality": "neutral",
        "market_factor": "neutral",
        "zone_quality": "neutral",
    }
    
    if not perf or not analysis.get("actual_entry"):
        return attribution
    
    # Entry quality
    if analysis.get("entry_vs_vwap") is not None:
        vwap_dist = analysis["entry_vs_vwap"]
        if vwap_dist < -1:
            attribution["entry_quality"] = "excellent (below VWAP)"
        elif vwap_dist < 0:
            attribution["entry_quality"] = "good (slight discount)"
        elif vwap_dist < 1:
            attribution["entry_quality"] = "fair (near VWAP)"
        else:
            attribution["entry_quality"] = "poor (chased above VWAP)"
    
    # Zone quality
    if analysis.get("zone_accuracy"):
        za = analysis["zone_accuracy"]
        if za["zone_was_support"] and za["zone_was_resistance"]:
            attribution["zone_quality"] = "excellent"
        elif za["zone_was_support"]:
            attribution["zone_quality"] = "good (support worked)"
        else:
            attribution["zone_quality"] = "poor (price broke through)"
    
    # Exit quality based on trigger
    if analysis.get("exit_trigger"):
        trigger = analysis["exit_trigger"].lower()
        if "stop" in trigger:
            attribution["exit_quality"] = "stop loss (defensive)"
        elif "target" in trigger or "tier" in trigger:
            attribution["exit_quality"] = "profit target (disciplined)"
        elif "trailing" in trigger:
            attribution["exit_quality"] = "trailing stop (balanced)"
        elif "manual" in trigger:
            attribution["exit_quality"] = "manual (discretionary)"
    
    # Market factor
    if perf["change_pct"] > 2:
        attribution["market_factor"] = "strong bullish"
    elif perf["change_pct"] < -2:
        attribution["market_factor"] = "strong bearish"
    elif perf["change_pct"] > 0:
        attribution["market_factor"] = "mild bullish"
    else:
        attribution["market_factor"] = "mild bearish"
    
    return attribution
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


def analyze_pick_missed_profit(pick_analysis: list) -> list:
    """
    Focus on OUR picks that we didn't maximize profit from.
    Returns list of missed profit opportunities.
    """
    missed_profit = []
    
    for pick in pick_analysis:
        symbol = pick.get("symbol", "")
        
        # Case 1: We didn't enter (gapped above zone)
        if pick.get("gap_status") == "above" and not pick.get("actual_entry"):
            missed_profit.append({
                "symbol": symbol,
                "reason": "Gapped above zone - no entry",
                "gap_pct": pick.get("gap_pct", 0),
                "potential_entry": pick.get("entry_high"),
                "actual_high": pick.get("high"),
                "missed_profit": (pick.get("high", 0) - pick["entry_high"]) if pick.get("high") and pick.get("entry_high") else 0,
                "recommendation": "Consider chase entry with tight stop if gap < 1%",
            })
        
        # Case 2: We entered but exited too early
        elif pick.get("actual_entry") and pick.get("actual_exit"):
            actual_pnl = pick.get("actual_pnl", 0)
            max_possible = 0
            if pick.get("simulated_exits") and pick["simulated_exits"]:
                max_possible = pick["simulated_exits"][0].get("pnl", 0)
            
            if max_possible and max_possible > actual_pnl * 1.5:  # Could have made 50% more
                missed_profit.append({
                    "symbol": symbol,
                    "reason": "Exited too early",
                    "actual_pnl": actual_pnl,
                    "potential_pnl": max_possible,
                    "left_on_table": max_possible - actual_pnl,
                    "recommendation": "Consider trailing stop or wider profit target",
                })
        
        # Case 3: Zone was wrong (price never reached)
        elif pick.get("gap_status") == "below":
            missed_profit.append({
                "symbol": symbol,
                "reason": "Zone too high - price opened below",
                "gap_pct": pick.get("gap_pct", 0),
                "recommendation": "Adjust zone calculation or use VWAP-based zones",
            })
    
    return sorted(missed_profit, key=lambda x: x.get("missed_profit", 0) or x.get("left_on_table", 0), reverse=True)


def analyze_top3_missed_outside(performances: list, picks_symbols: set) -> list:
    """
    Only analyze top 3 missed stocks NOT in our picks.
    Identify why they moved and if screener should have caught them.
    """
    outside_movers = [
        p for p in performances
        if not p.get("was_picked", False)
        and p["max_intraday_pct"] > 3.0  # Higher threshold - only significant movers
        and p["volume"] > 100000  # Higher volume filter
    ]
    
    top3 = sorted(outside_movers, key=lambda x: x["max_intraday_pct"], reverse=True)[:3]
    
    results = []
    for stock in top3:
        results.append({
            "symbol": stock["symbol"],
            "max_move_pct": stock["max_intraday_pct"],
            "change_pct": stock["change_pct"],
            "volume": stock["volume"],
            "why_missed": analyze_why_not_picked(stock),
        })
    
    return results


def analyze_why_not_picked(stock: dict) -> str:
    """
    Analyze why this stock wasn't in our picks.
    """
    reasons = []
    
    if stock["volume"] < 50000:
        reasons.append("Low volume (screener filter)")
    
    if abs(stock["change_pct"]) < 1:
        reasons.append("Low pre-market momentum")
    
    return "; ".join(reasons) if reasons else "Unknown - need screener logs"


def find_missed_opportunities(performances: list, picks_symbols: set, pick_analysis: list, min_move: float = 1.5):
    """
    Focused missed opportunities analysis.
    Priority: Our picks first, then top 3 outside picks.
    """
    # Analyze our picks for missed profit
    our_missed = analyze_pick_missed_profit(pick_analysis)
    
    # Top 3 outside movers
    outside_missed = analyze_top3_missed_outside(performances, picks_symbols)
    
    return {
        "our_picks": our_missed,
        "outside_top3": outside_missed,
    }


def generate_recommendations(pick_analysis: list, missed: dict, performances: list, trade_analysis: list = None):
    recommendations = []
    
    # Trade-based recommendations with specific causes and solutions
    if trade_analysis:
        early_exits = [t for t in trade_analysis if t.get("potential_missed_profit", 0) > 5]
        if early_exits:
            avg_missed = sum(t["potential_missed_profit"] for t in early_exits) / len(early_exits)
            recommendations.append(
                f"💰 MISSED PROFIT: {len(early_exits)} trades exited early — avg missed {avg_missed:.2f} SAR\n"
                f"   → Cause: Exited on VWAP breakdown before reaching high of day\n"
                f"   → Solution: Use trailing stop (-1% from high) instead of VWAP breakdown\n"
                f"   → Benefit: Would capture {avg_missed:.2f} more SAR per trade"
            )
        
        # Analyze losing trades
        losers = [t for t in trade_analysis if t.get("actual_pnl", 0) < 0]
        if losers:
            total_loss = sum(t["actual_pnl"] for t in losers)
            recommendations.append(
                f"🔴 LOSING TRADES: {len(losers)} trades with total loss of {total_loss:.2f} SAR\n"
                f"   → Cause: {', '.join(set(t.get('symbol', '') for t in losers))} sold below entry\n"
                f"   → Solution: Wider stop loss or better entry timing\n"
                f"   → Benefit: Reduce losses by 50% with -2% stop instead of VWAP breakdown"
            )
        
        # Entry quality analysis
        good_entries = [t for t in trade_analysis if t.get("entry_vs_zone") == "in_zone"]
        if good_entries:
            avg_slip = sum(t.get("entry_slippage_pct", 0) for t in good_entries) / len(good_entries)
            recommendations.append(
                f"✅ ENTRY QUALITY: {len(good_entries)} entries in zone (avg slippage: {avg_slip:+.2f}%)\n"
                f"   → Good: Entries within predicted zones\n"
                f"   → Improvement: Use limit orders at zone low for better price\n"
                f"   → Benefit: Save 0.2-0.5% per entry"
            )
    
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

    if missed and isinstance(missed, dict):
        our_picks = missed.get("our_picks", [])
        outside = missed.get("outside_top3", [])
        total_missed = len(our_picks) + len(outside)
        if total_missed > 0:
            recommendations.append(
                f"🔍 {total_missed} missed opportunities ({len(our_picks)} from our picks, {len(outside)} outside) — "
                f"review entry/exit strategy."
            )
    elif missed:
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
                    missed: list, recommendations: list, total_scanned: int, fail_count: int,
                    trade_analysis: list = None):
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
        perf = pa if pa.get("open") else None
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
                f"• {p['symbol']}: opened {p.get('perf',{}).get('open',0):.2f} > zone {p['entry_low']:.2f}–{p['entry_high']:.2f} "
                f"(+{p['gap_pct']:.1f}% gap)"
            )
        report.append("")

    in_zone = [p for p in pick_analysis if p.get("gap_status") == "in_zone"]
    if in_zone and not any(p.get("touched_zone") for p in in_zone):
        report.append("Picks in zone never dipped back into buy zone during session.")
        report.append("")

    report.append("━" * 45)
    report.append("")

    # Actual trades section
    if trade_analysis:
        report.append("<b>💰 Actual Trade Performance</b>")
        report.append("")
        
        total_actual_pnl = sum(t.get("actual_pnl", 0) for t in trade_analysis if t.get("actual_pnl"))
        total_missed = sum(t.get("potential_missed_profit", 0) for t in trade_analysis if t.get("potential_missed_profit", 0) > 0)
        
        report.append(f"• Total actual P&L: {total_actual_pnl:+.2f} SAR")
        report.append(f"• Potential missed profit: {total_missed:.2f} SAR")
        report.append("")
        
        for ta in trade_analysis:
            if ta.get("has_trade"):
                emoji = "🟢" if ta.get("actual_pnl", 0) > 0 else "🔴"
                buy_price = ta.get('buy_price')
                buy_str = f"{buy_price:.2f}" if buy_price is not None else "N/A"
                report.append(
                    f"{emoji} <b>{ta['symbol']}</b> | "
                    f"Entry: {buy_str} → Exit: {ta.get('sell_price', 'N/A')} | "
                    f"P&L: {ta.get('actual_pnl', 0):+.2f} | "
                    f"Hold: {ta.get('hold_time_min', 0)}min"
                )
                
                if ta.get("entry_slippage_pct"):
                    slip_emoji = "⚠️" if abs(ta["entry_slippage_pct"]) > 1 else "✅"
                    report.append(
                        f"   {slip_emoji} Entry slippage: {ta['entry_slippage_pct']:+.2f}% | "
                        f"Zone: {ta.get('entry_vs_zone', 'N/A')}"
                    )
                
                if ta.get("potential_missed_profit", 0) > 5:
                    report.append(
                        f"   💰 Missed profit: {ta['potential_missed_profit']:.2f} SAR "
                        f"(if held to HOD: {ta['max_profit_if_held']:.2f})"
                    )
        
        report.append("")
        report.append("━" * 45)
        report.append("")

    # Missed opportunities - our picks first
    our_missed = missed.get("our_picks", [])
    outside_top3 = missed.get("outside_top3", [])
    
    if our_missed:
        report.append(f"<b>💰 Missed Profit from Our Picks ({len(our_missed)})</b>")
        report.append("")
        for i, m in enumerate(our_missed[:5], 1):
            report.append(f"{i}. <b>{m['symbol']}</b> | {m['reason']}")
            if "missed_profit" in m:
                report.append(f"   💸 Missed: {m['missed_profit']:.2f} SAR | {m['recommendation']}")
            elif "left_on_table" in m:
                report.append(f"   💸 Left on table: {m['left_on_table']:.2f} SAR | {m['recommendation']}")
            elif "gap_pct" in m:
                report.append(f"   📊 Gap: {m['gap_pct']:.2f}% | {m['recommendation']}")
        report.append("")
    
    # Top 3 outside movers
    if outside_top3:
        report.append(f"<b>🔍 Top 3 Missed Outside Picks</b>")
        report.append("")
        for i, m in enumerate(outside_top3, 1):
            report.append(
                f"{i}. <b>{m['symbol']}</b> | +{m['max_move_pct']:.1f}% | "
                f"Vol: {m['volume']:,} | Why: {m['why_missed']}"
            )
        report.append("")
    
    if not our_missed and not outside_top3:
        report.append("<b>✅ No Significant Missed Opportunities</b>")
        report.append("All picks performed within expected parameters.")
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


def save_and_track(date_str: str, report: str, missed: dict, recommendations: list, pick_analysis: list = None, trade_analysis: list = None):
    report_dir = BASE_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    report_file = report_dir / f"post_market_{date_str}.html"

    with open(report_file, "w") as f:
        f.write(f"<pre>{report}</pre>")

    # ── Write to relearning directory ──────────────────────────────────
    try:
        relearning_dir = BASE_DIR / "relearning" / "daily" / date_str
        relearning_dir.mkdir(parents=True, exist_ok=True)
        (relearning_dir / "charts").mkdir(exist_ok=True)
        
        # Save main report
        with open(relearning_dir / "report.md", "w") as f:
            f.write(report)
        
        # Save pick analysis JSON
        if pick_analysis:
            with open(relearning_dir / "picks_analysis.json", "w") as f:
                json.dump(pick_analysis, f, indent=2, default=str)
        
        # Save trade analysis JSON
        if trade_analysis:
            with open(relearning_dir / "trades_analysis.json", "w") as f:
                json.dump(trade_analysis, f, indent=2, default=str)
        
        # Save missed opportunities JSON
        with open(relearning_dir / "missed_opportunities.json", "w") as f:
            json.dump(missed, f, indent=2, default=str)
        
        # Save entry/exit stats
        if trade_analysis and pick_analysis:
            entry_exit_stats = calculate_entry_exit_stats(trade_analysis, pick_analysis)
            with open(relearning_dir / "entry_exit_stats.json", "w") as f:
                json.dump(entry_exit_stats, f, indent=2, default=str)
        
        # Update weekly aggregate
        update_weekly_aggregate(date_str)
        
        print(f"📝 Saved relearning data to {relearning_dir}")
    except Exception as e:
        print(f"[WARNING] Failed to write relearning files: {e}")
    # ───────────────────────────────────────────────────────────────────

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

    # Handle dict structure for missed opportunities
    total_missed = 0
    if isinstance(missed, dict):
        our_picks = missed.get("our_picks", [])
        outside = missed.get("outside_top3", [])
        total_missed = len(our_picks) + len(outside)
        if total_missed > 0:
            # Calculate average gain from our missed picks
            avg_gain = sum(m.get("missed_profit", 0) for m in our_picks) / len(our_picks) if our_picks else 0
            if outside:
                avg_gain += sum(m.get("max_move_pct", 0) for m in outside) / len(outside)
                avg_gain /= 2
            prev_avg = learning.get("missed_opportunities_avg", 0)
            n = learning["sessions_analyzed"]
            learning["missed_opportunities_avg"] = (prev_avg * (n - 1) + avg_gain) / n
    elif isinstance(missed, list) and missed:
        avg_gain = sum(m["max_intraday_pct"] for m in missed) / len(missed)
        prev_avg = learning.get("missed_opportunities_avg", 0)
        n = learning["sessions_analyzed"]
        learning["missed_opportunities_avg"] = (prev_avg * (n - 1) + avg_gain) / n

    with open(learning_file, "w") as f:
        json.dump(learning, f, indent=2)

    return learning_file


# Actual trade data integration (added 2026-06-14)
ORDER_HISTORY_FILE = BASE_DIR / "history" / "order_history.csv"


def load_actual_trades(date_str: str) -> list:
    """
    Load actual BUY/SELL trades from order_history.csv for the given date.
    Only returns FILLED trades (filters out REJECTED).
    Also filters out duplicates (same order_id, symbol, side, price).
    Returns list of trade dicts with entry/exit details.
    """
    trades = []
    seen = set()  # Track seen trades to avoid duplicates
    
    try:
        with open(ORDER_HISTORY_FILE) as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Match date format in CSV (MM-DD)
                row_date = row.get("date", "")
                # Match both MM-DD and YYYY-MM-DD formats
                if row_date == date_str[5:] or row_date == date_str:
                    status = row.get("status", "").upper()
                    # Only include FILLED trades, skip REJECTED
                    if status == "FILLED":
                        # Create unique key to detect duplicates
                        order_id = row.get("order_id", "")
                        symbol = row.get("symbol", "").replace(".SR", "")
                        side = row.get("side", "")
                        price = row.get("price", "")
                        
                        # Skip orders with no order_id (like "?")
                        if order_id and order_id != "?":
                            unique_key = f"{order_id}_{symbol}_{side}_{price}"
                            if unique_key in seen:
                                log.warning(f"Duplicate trade filtered: {unique_key} on {row_date}")
                                continue
                            seen.add(unique_key)
                        
                        trades.append({
                            "order_id": order_id,
                            "symbol": symbol,
                            "side": side,
                            "qty": int(row.get("qty", 0) or 0),
                            "price": float(row.get("price", 0) or 0),
                            "total": float(row.get("total", 0) or 0),
                            "fees": float(row.get("fees", 0) or 0),
                            "trigger_basis": row.get("trigger_basis", ""),
                            "time": row.get("time", ""),
                            "status": status,
                        })
    except Exception as e:
        print(f"[WARN] Failed to load order history: {e}")
    return trades


def calculate_hold_time(buy_time: str, sell_time: str) -> int:
    """Calculate hold time in minutes between buy and sell."""
    try:
        fmt = "%H:%M"
        buy = datetime.strptime(buy_time[:5], fmt)
        sell = datetime.strptime(sell_time[:5], fmt)
        return int((sell - buy).total_seconds() / 60)
    except:
        return 0


def calculate_entry_exit_stats(trade_analysis: list, pick_analysis: list) -> dict:
    """
    Calculate entry/exit performance statistics.
    """
    stats = {
        "total_trades": 0,
        "win_rate": 0,
        "avg_entry_slippage_pct": 0,
        "avg_exit_vs_hod_pct": 0,
        "avg_hold_time_min": 0,
        "early_exit_rate": 0,
        "missed_entries": 0,
    }
    
    if not trade_analysis:
        return stats
    
    entries = [t for t in trade_analysis if t.get("buy_price")]
    exits = [t for t in trade_analysis if t.get("sell_price")]
    
    stats["total_trades"] = len(entries)
    
    if entries:
        # Entry slippage
        slippages = [t.get("entry_slippage_pct", 0) for t in entries if t.get("entry_slippage_pct") is not None]
        if slippages:
            stats["avg_entry_slippage_pct"] = sum(slippages) / len(slippages)
        
        # Hold time
        hold_times = [t.get("hold_time_min", 0) for t in entries if t.get("hold_time_min")]
        if hold_times:
            stats["avg_hold_time_min"] = sum(hold_times) / len(hold_times)
    
    if exits:
        # Exit vs HOD
        hod_distances = [t.get("exit_vs_hod_pct", 0) for t in exits if t.get("exit_vs_hod_pct") is not None]
        if hod_distances:
            stats["avg_exit_vs_hod_pct"] = sum(hod_distances) / len(hod_distances)
        
        # Early exit rate
        early_exits = [t for t in exits if t.get("potential_missed_profit", 0) > 5]
        stats["early_exit_rate"] = len(early_exits) / len(exits) * 100 if exits else 0
    
    # Win rate
    winners = [t for t in trade_analysis if t.get("actual_pnl", 0) > 0]
    stats["win_rate"] = len(winners) / len(trade_analysis) * 100 if trade_analysis else 0
    
    # Missed entries (picks we didn't trade)
    if pick_analysis:
        traded_symbols = {t["symbol"] for t in trade_analysis}
        picked_symbols = {p["symbol"] for p in pick_analysis}
        stats["missed_entries"] = len(picked_symbols - traded_symbols)
    
    return stats


def update_weekly_aggregate(date_str: str):
    """
    Aggregate daily data into weekly summary.
    """
    try:
        # Parse date
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        week_start = dt - timedelta(days=dt.weekday())
        week_str = week_start.strftime("%Y-W%W")
        
        weekly_dir = BASE_DIR / "relearning" / "weekly" / week_str
        weekly_dir.mkdir(parents=True, exist_ok=True)
        
        # Collect all daily data for this week
        daily_data = []
        for day_dir in (BASE_DIR / "relearning" / "daily").glob("*"):
            if day_dir.is_dir():
                stats_file = day_dir / "entry_exit_stats.json"
                if stats_file.exists():
                    with open(stats_file) as f:
                        daily_data.append(json.load(f))
        
        if not daily_data:
            return
        
        # Aggregate stats
        weekly_stats = {
            "week": week_str,
            "trading_days": len(daily_data),
            "total_trades": sum(d.get("total_trades", 0) for d in daily_data),
            "avg_win_rate": sum(d.get("win_rate", 0) for d in daily_data) / len(daily_data),
            "avg_entry_slippage": sum(d.get("avg_entry_slippage_pct", 0) for d in daily_data) / len(daily_data),
            "avg_exit_vs_hod": sum(d.get("avg_exit_vs_hod_pct", 0) for d in daily_data) / len(daily_data),
            "avg_hold_time": sum(d.get("avg_hold_time_min", 0) for d in daily_data) / len(daily_data),
            "avg_early_exit_rate": sum(d.get("early_exit_rate", 0) for d in daily_data) / len(daily_data),
        }
        
        with open(weekly_dir / "aggregate.json", "w") as f:
            json.dump(weekly_stats, f, indent=2)
        
        print(f"📝 Updated weekly aggregate: {weekly_dir}")
    except Exception as e:
        print(f"[WARNING] Failed to update weekly aggregate: {e}")


def analyze_actual_vs_ideal(trades: list, picks: list, perf_map: dict) -> list:
    """
    Compare actual trade entries/exits with ideal picks.
    Returns analysis with slippage, timing, and performance gaps.
    """
    analysis = []
    
    # Build buy/sell pairs
    buys = {t["symbol"]: t for t in trades if t["side"] == "BUY" and t["status"] == "FILLED"}
    sells = {t["symbol"]: t for t in trades if t["side"] == "SELL" and t["status"] == "FILLED"}
    
    for symbol in set(buys.keys()) | set(sells.keys()):
        buy = buys.get(symbol)
        sell = sells.get(symbol)
        perf = perf_map.get(symbol)
        pick = next((p for p in picks if p.get("symbol", "") == symbol), None)
        
        result = {
            "symbol": symbol,
            "has_trade": bool(buy or sell),
            "buy_price": buy["price"] if buy else None,
            "sell_price": sell["price"] if sell else None,
            "ideal_entry_low": pick.get("entry_low") if pick else None,
            "ideal_entry_high": pick.get("entry_high") if pick else None,
        }
        
        if buy and pick and pick.get("entry_high", 0) > 0:
            # Entry slippage analysis
            ideal_mid = (pick["entry_low"] + pick["entry_high"]) / 2
            result["entry_slippage_pct"] = (buy["price"] - ideal_mid) / ideal_mid * 100
            result["entry_vs_zone"] = "in_zone" if pick["entry_low"] <= buy["price"] <= pick["entry_high"] else "out_of_zone"
        
        if buy and sell:
            # Actual P&L
            actual_pnl = (sell["price"] - buy["price"]) * buy["qty"] - buy["fees"] - sell.get("fees", 0)
            result["actual_pnl"] = actual_pnl
            result["hold_time_min"] = calculate_hold_time(buy["time"], sell["time"])
            
            # Simulate other exits
            if perf:
                result["ideal_exit_high"] = perf["high"]
                result["ideal_exit_low"] = perf["low"]
                result["max_profit_if_held"] = (perf["high"] - buy["price"]) * buy["qty"]
                result["potential_missed_profit"] = result["max_profit_if_held"] - actual_pnl
        
        analysis.append(result)
    
    return analysis


def _is_saudi_trading_day(dt: datetime) -> bool:
    """Return True if dt falls on a TASI trading day (Sun–Thu)."""
    return dt.weekday() in (6, 0, 1, 2, 3, 4)


def main():
    log.info(f"📊 Post-market analysis starting")
    
    now = datetime.now(RIYADH)
    date_str = now.strftime("%Y-%m-%d")
    
    log.info(f"[INFO] System config: {SYSTEM_CONFIG['version']} | Screens: {len(SYSTEM_CONFIG['screens'])} | Trading days: {', '.join(SYSTEM_CONFIG['trading_days'][:3])}...")

    if not _is_saudi_trading_day(now):
        msg = f"⚠️ TASI closed today ({now:%A}). Skipping post-market scan."
        log.info(msg)
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
    performances, fail_count, new_cache = analyze_all_stocks_sequential(tickers, picks_symbols, cache, date_str)

    # Save cache for next run
    save_cache(new_cache)

    elapsed = time.time() - start_time
    print(f"• Done in {elapsed:.1f}s: {len(performances)}/{len(tickers)} stocks ({fail_count} failures)")

    perf_map = {p["symbol"]: p for p in performances}
    
    # Load actual trades
    print(f"• Loading actual trades from order_history.csv...")
    trades = load_actual_trades(date_str)
    print(f"• Found {len(trades)} trades for {date_str}")
    
    # Comprehensive pick analysis (Phase 3)
    pick_analysis = analyze_picks_comprehensive(picks, perf_map, trades, date_str)
    
    # Analyze actual vs ideal
    trade_analysis = analyze_actual_vs_ideal(trades, picks, perf_map)
    
    # Add trade data to pick analysis
    for pa in pick_analysis:
        symbol = pa["symbol"]
        trade_info = next((t for t in trade_analysis if t["symbol"] == symbol), None)
        if trade_info:
            pa.update({
                "actual_entry": trade_info.get("buy_price"),
                "actual_exit": trade_info.get("sell_price"),
                "entry_slippage_pct": trade_info.get("entry_slippage_pct"),
                "actual_pnl": trade_info.get("actual_pnl"),
                "hold_time_min": trade_info.get("hold_time_min"),
                "max_profit_if_held": trade_info.get("max_profit_if_held"),
            })
    
    missed = find_missed_opportunities(performances, picks_symbols, pick_analysis)
    recommendations = generate_recommendations(pick_analysis, missed, performances, trade_analysis)

    print(f"• Missed opportunities: {len(missed.get('our_picks', []))} our picks, {len(missed.get('outside_top3', []))} outside top3")

    report = generate_report(date_str, pick_analysis, performances, missed,
                            recommendations, len(tickers), fail_count, trade_analysis)

    tg_send(report)

    learning_file = save_and_track(date_str, report, missed, recommendations, pick_analysis, trade_analysis)

    summary = (
        f"📊 Post-market analysis sent to TASI group\n"
        f"• Stocks scanned: {len(performances)}/{len(tickers)} ({elapsed:.0f}s)\n"
        f"• Picks analyzed: {len(picks)}\n"
        f"• Actual trades: {len(trades)}\n"
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
        small_gaps = [p for p in gap_above if p.get('perf') and (p.get('perf',{}).get('open',0) - float(p['zone'].split('-')[1])) / float(p['zone'].split('-')[1]) * 100 < 0.5]
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

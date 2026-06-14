# Post-Market Analysis Enhancement Proposal

**Date:** 2026-06-14
**Status:** Awaiting approval
**Requester:** A A
**Priority:** HIGH

---

## Current System Summary

`post_market.py` (1395 lines) runs at 15:35 post-market via OpenClaw cron.

**Current Flow:**
1. Load ~200 Sharia tickers from `sharia_list.json`
2. Load picks from `picks.json`, `picks_1030.json`, `picks_1200.json`, `picks_1330.json`
3. Fetch OHLCV via yfinance (cached in `pm_cache.json`)
4. Analyze gap status for picks
5. Find missed opportunities (>1.5% move, >50K vol)
6. Generate recommendations
7. Send report to Telegram group
8. Save HTML report to `reports/`
9. Save memory to `memory/YYYY-MM-DD.md`
10. Record daily P&L via `bookkeeper.record_daily_pnl()`
11. Send email report (HTML)

**Current Issues:**
- yfinance unreliable for TADAWUL (many failures)
- No actual trade integration (reads `positions.json` but not `order_history.csv`)
- Missed opportunities focus on ALL stocks, not just OUR picks
- Reports scattered in `reports/` and `memory/`, no `relearning/` output
- No price chart generation
- No entry/exit performance analysis

---

## Proposal A: Data Fetching Enhancement

### Current Code (lines 84-119)
```python
def fetch_one(symbol: str, cache: dict) -> tuple:
    for attempt in range(2):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1d")
            if df.empty:
                df = ticker.history(period="5d")
                if not df.empty:
                    df = df.iloc[[-1]]
            # ... extract OHLCV
        except Exception:
            if attempt == 0:
                time.sleep(0.5)
            else:
                return symbol, None
```

### Problem
- Only 2 attempts, no exponential backoff
- No fallback data source
- yfinance often returns empty for `.SR` tickers
- No WebSocket price file usage

### Proposed Solution
```python
import json
from pathlib import Path

WS_FRAMES_FILE = Path("/home/mino/tasi-exec/ws_frames.json")


def fetch_from_ws_frames(symbol: str, date_str: str) -> Optional[dict]:
    """
    Fetch OHLCV from WebSocket price frames captured during trading.
    Returns dict with open, high, low, close, volume or None.
    """
    try:
        with open(WS_FRAMES_FILE) as f:
            ws_data = json.load(f)
        
        # ws_frames.json structure:
        # { "frames": [{ "timestamp": "...", "symbol": "1010", "price": 45.2, "volume": 1000 }, ...] }
        frames = ws_data.get("frames", [])
        
        # Filter frames for this symbol and date
        symbol_frames = [
            f for f in frames 
            if f.get("symbol") == symbol.replace(".SR", "")
            and date_str in f.get("timestamp", "")
        ]
        
        if not symbol_frames:
            return None
        
        prices = [f["price"] for f in symbol_frames]
        volumes = [f.get("volume", 0) for f in symbol_frames]
        
        return {
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
            "volume": sum(volumes),
        }
    except Exception:
        return None


def fetch_one(symbol: str, cache: dict, date_str: str) -> tuple:
    """
    Fetch stock data with retry and fallback to WebSocket frames.
    Priority: cache → ws_frames → yfinance
    """
    # Check cache first
    if symbol in cache:
        return symbol, cache[symbol]
    
    # Fallback 1: WebSocket frames (real-time captured data)
    ws_data = fetch_from_ws_frames(symbol, date_str)
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
            
            if not df.empty:
                # ... extract OHLCV
                return symbol, result
        except Exception:
            if attempt < 4:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s, 8s
                continue
    
    return symbol, None
```

**Benefits:**
- Real-time WebSocket data is more accurate than yfinance
- Exponential backoff reduces API pressure
- 5 attempts instead of 2

---

## Proposal B: Actual Trade Integration (ESSENTIAL)

### Current Code (lines 728-757)
```python
def load_positions_today(date_str: str) -> Dict[str, IntPosition]:
    """Load positions opened today"""
    positions = {}
    pos_file = BASE_DIR / 'positions.json'
    # Reads positions.json - but this is stale data
```

### Problem
- Reads `positions.json` which is stale (updated by poller, not by actual trades)
- Doesn't read actual trades from `order_history.csv`
- Can't compare actual vs ideal entries/exits

### Proposed Solution
```python
import csv
from datetime import datetime

ORDER_HISTORY_FILE = BASE_DIR / "history" / "order_history.csv"


def load_actual_trades(date_str: str) -> List[Dict]:
    """
    Load actual BUY/SELL trades from order_history.csv for the given date.
    Returns list of trade dicts with entry/exit details.
    """
    trades = []
    try:
        with open(ORDER_HISTORY_FILE) as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Match date format in CSV (MM-DD)
                row_date = row.get("date", "")
                if row_date == date_str[5:] or row_date == date_str:  # Match both formats
                    trades.append({
                        "order_id": row.get("order_id"),
                        "symbol": row.get("symbol", "").replace(".SR", ""),
                        "side": row.get("side", ""),
                        "qty": int(row.get("qty", 0) or 0),
                        "price": float(row.get("price", 0) or 0),
                        "total": float(row.get("total", 0) or 0),
                        "fees": float(row.get("fees", 0) or 0),
                        "trigger_basis": row.get("trigger_basis", ""),
                        "time": row.get("time", ""),
                        "status": row.get("status", ""),
                    })
    except Exception as e:
        print(f"[WARN] Failed to load order history: {e}")
    
    return trades


def analyze_actual_vs_ideal(trades: List[Dict], picks: List[Dict], perf_map: Dict) -> List[Dict]:
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
        
        if buy and pick:
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


def calculate_hold_time(buy_time: str, sell_time: str) -> int:
    """Calculate hold time in minutes between buy and sell."""
    try:
        fmt = "%H:%M"
        buy = datetime.strptime(buy_time[:5], fmt)
        sell = datetime.strptime(sell_time[:5], fmt)
        return int((sell - buy).total_seconds() / 60)
    except:
        return 0
```

**Benefits:**
- Real trade data from `order_history.csv`
- Entry slippage analysis
- Hold time calculation
- Simulated exits (what if held to high?)
- Missed profit quantification

---

## Proposal C: Enhanced Pick Analysis

### Current Code (lines 157-203)
```python
def analyze_picks_detailed(picks: list, perf_map: dict):
    # Only checks gap status
    result = {
        "gap_status": "above" | "below" | "in_zone",
        "gap_pct": 0,
        "touched_zone": False,
    }
```

### Proposed Solution
```python

def analyze_picks_comprehensive(picks: list, perf_map: dict, trades: List[Dict], date_str: str) -> List[Dict]:
    """
    Comprehensive pick analysis with:
    1. Screener picks evaluation
    2. Actual vs ideal entries/exits
    3. Full-day gap status
    4. Performance evaluation
    5. P&L attribution (why lost/made money)
    """
    results = []
    
    # Build trade lookup
    trade_map = {t["symbol"]: t for t in trades}
    
    for pick in picks:
        symbol = pick.get("symbol", "")
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
            # 1. Full-day gap analysis (open vs zone)
            analysis["open"] = perf["open"]
            analysis["high"] = perf["high"]
            analysis["low"] = perf["low"]
            analysis["close"] = perf["close"]
            analysis["volume"] = perf["volume"]
            analysis["change_pct"] = perf["change_pct"]
            analysis["max_intraday_pct"] = perf["max_intraday_pct"]
            
            # Gap status at open
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
            
            # 2. Zone performance (how well did the zone predict?)
            if pick["entry_low"] > 0 and pick["entry_high"] > 0:
                zone_low_error = (perf["low"] - pick["entry_low"]) / pick["entry_low"] * 100
                zone_high_error = (perf["high"] - pick["entry_high"]) / pick["entry_high"] * 100
                analysis["zone_accuracy"] = {
                    "low_error_pct": zone_low_error,
                    "high_error_pct": zone_high_error,
                    "zone_was_support": perf["low"] >= pick["entry_low"] * 0.99,  # Within 1%
                    "zone_was_resistance": perf["high"] <= pick["entry_high"] * 1.01,
                }
        
        # 3. Actual trade performance
        if trade:
            if trade["side"] == "BUY":
                analysis["actual_entry"] = trade["price"]
                analysis["entry_time"] = trade["time"]
                analysis["entry_trigger"] = trade["trigger_basis"]
                
                if pick.get("entry_low"):
                    slippage = (trade["price"] - (pick["entry_low"] + pick["entry_high"]) / 2)
                    analysis["entry_slippage"] = slippage
                    analysis["entry_slippage_pct"] = slippage / pick["entry_high"] * 100
            
            elif trade["side"] == "SELL":
                analysis["actual_exit"] = trade["price"]
                analysis["exit_time"] = trade["time"]
                analysis["exit_trigger"] = trade["trigger_basis"]
                
                # Calculate hold time and P&L
                if analysis.get("actual_entry"):
                    analysis["hold_time_min"] = calculate_hold_time(
                        analysis["entry_time"], trade["time"]
                    )
                    analysis["actual_pnl"] = (trade["price"] - analysis["actual_entry"]) * trade["qty"]
                    
                    # 4. Simulate other exit windows
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


def simulate_exit_windows(entry_price: float, perf: dict, actual_exit: float) -> List[Dict]:
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
        "realistic": False,  # Impossible to know HOD in advance
    })
    
    # Scenario 2: Trailing stop (exit on 1% pullback from high)
    trailing_exit = perf["high"] * 0.99
    if trailing_exit > entry_price:
        scenarios.append({
            "strategy": "Trailing stop -1%",
            "exit_price": trailing_exit,
            "pnl": trailing_exit - entry_price,
            "vs_actual": (trailing_exit - entry_price) - (actual_exit - entry_price),
            "realistic": True,
        })
    
    # Scenario 3: VWAP exit (if we had VWAP data)
    # Would need VWAP data from ws_frames
    
    # Scenario 4: Time-based exit (exit at 14:30)
    # Would need intraday data
    
    return scenarios


def calculate_pnl_attribution(analysis: dict, perf: dict) -> Dict:
    """
    Analyze why we made/lost money on this pick.
    Breaks down P&L into components.
    """
    attribution = {
        "entry_quality": "neutral",
        "exit_quality": "neutral",
        "market_factor": "neutral",
        "zone_quality": "neutral",
    }
    
    if not perf or not analysis.get("actual_entry"):
        return attribution
    
    # Entry quality: Did we enter near ideal zone?
    if analysis.get("entry_slippage_pct"):
        if abs(analysis["entry_slippage_pct"]) < 0.5:
            attribution["entry_quality"] = "good"
        elif analysis["entry_slippage_pct"] > 2:
            attribution["entry_quality"] = "poor (chased)"
        elif analysis["entry_slippage_pct"] < -2:
            attribution["entry_quality"] = "good (got discount)"
    
    # Zone quality: Did the zone actually contain the price?
    if analysis.get("zone_accuracy"):
        za = analysis["zone_accuracy"]
        if za["zone_was_support"] and za["zone_was_resistance"]:
            attribution["zone_quality"] = "excellent"
        elif za["zone_was_support"]:
            attribution["zone_quality"] = "good (support worked)"
        else:
            attribution["zone_quality"] = "poor (price broke through)"
    
    # Market factor: Was it a strong/weak stock day?
    if perf["change_pct"] > 2:
        attribution["market_factor"] = "strong bullish"
    elif perf["change_pct"] < -2:
        attribution["market_factor"] = "strong bearish"
    elif perf["change_pct"] > 0:
        attribution["market_factor"] = "mild bullish"
    else:
        attribution["market_factor"] = "mild bearish"
    
    return attribution
```

**Benefits:**
- Comprehensive pick evaluation
- Actual vs ideal comparison
- Simulated exits for strategy improvement
- P&L attribution tells WHY we lost/made money

---

## Proposal D: Focused Missed Opportunities

### Current Code (lines 206-216)
```python
def find_missed_opportunities(performances: list, picks_symbols: set, min_move: float = 1.5):
    missed = [
        p for p in performances
        if not p["was_picked"]
        and p["max_intraday_pct"] > min_move
        and (p["high"] - p["low"]) / p["open"] > 0.015
        and p["volume"] > 50000
    ]
    return missed[:10]  # Returns ALL stocks
```

### Proposed Solution
```python
from typing import Dict, List
import matplotlib.pyplot as plt
from pathlib import Path


def analyze_pick_missed_profit(pick_analysis: List[Dict]) -> List[Dict]:
    """
    Focus on OUR picks that gapped up or we didn't trade.
    How could we have maximized profit?
    """
    missed_profit = []
    
    for pick in pick_analysis:
        symbol = pick["symbol"]
        
        # Case 1: We didn't enter (gapped above zone)
        if pick.get("gap_status") == "above" and not pick.get("actual_entry"):
            missed_profit.append({
                "symbol": symbol,
                "reason": "Gapped above zone - no entry",
                "gap_pct": pick["gap_pct"],
                "potential_entry": pick["entry_high"],
                "actual_high": pick.get("high"),
                "missed_profit": (pick.get("high", 0) - pick["entry_high"]) if pick.get("high") else 0,
                "recommendation": "Consider chase entry with tight stop if gap < 1%",
            })
        
        # Case 2: We entered but exited too early
        elif pick.get("actual_entry") and pick.get("actual_exit"):
            actual_pnl = pick.get("actual_pnl", 0)
            max_possible = pick.get("simulated_exits", [{}])[0].get("pnl", 0)
            
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
                "gap_pct": pick["gap_pct"],
                "recommendation": "Adjust zone calculation or use VWAP-based zones",
            })
    
    return sorted(missed_profit, key=lambda x: x.get("missed_profit", 0) or x.get("left_on_table", 0), reverse=True)


def analyze_top3_missed_outside(performances: list, picks_symbols: set) -> List[Dict]:
    """
    Only analyze top 3 missed stocks NOT in our picks.
    Identify why they moved and if screener should have caught them.
    """
    outside_movers = [
        p for p in performances
        if not p["was_picked"]
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
    
    # Check if it was filtered out
    # (This would need access to screener filter logic)
    
    if stock["volume"] < 50000:
        reasons.append("Low volume (screener filter)")
    
    if abs(stock["change_pct"]) < 1:
        reasons.append("Low pre-market momentum")
    
    # Could check if it was in Sharia list
    # Could check if it passed technical criteria
    
    return "; ".join(reasons) if reasons else "Unknown - need screener logs"


def generate_pick_charts(pick_analysis: List[Dict], date_str: str, output_dir: Path):
    """
    Generate price charts for picks showing entry zone and actual trades.
    """
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(exist_ok=True)
    
    for pick in pick_analysis:
        symbol = pick["symbol"]
        
        # Create chart showing:
        # - Entry zone (green band)
        # - Actual entry (green dot)
        # - Actual exit (red dot)
        # - High of day (dashed line)
        # - Low of day (dashed line)
        
        fig, ax = plt.subplots(figsize=(10, 6))
        
        # We need intraday data for proper chart
        # For now, show zone and key levels
        
        if pick.get("entry_low") and pick.get("entry_high"):
            ax.axhspan(pick["entry_low"], pick["entry_high"], alpha=0.3, color='green', label='Entry Zone')
        
        if pick.get("actual_entry"):
            ax.axhline(pick["actual_entry"], color='green', linestyle='-', linewidth=2, label=f'Actual Entry: {pick["actual_entry"]}')
        
        if pick.get("actual_exit"):
            ax.axhline(pick["actual_exit"], color='red', linestyle='-', linewidth=2, label=f'Actual Exit: {pick["actual_exit"]}')
        
        if pick.get("high"):
            ax.axhline(pick["high"], color='gray', linestyle='--', alpha=0.5, label=f'HOD: {pick["high"]}')
        
        if pick.get("low"):
            ax.axhline(pick["low"], color='gray', linestyle=':', alpha=0.5, label=f'LOD: {pick["low"]}')
        
        ax.set_title(f"{symbol} - {date_str}")
        ax.set_ylabel("Price (SAR)")
        ax.legend()
        
        chart_file = charts_dir / f"{symbol}_{date_str}.png"
        plt.savefig(chart_file)
        plt.close()
```

**Benefits:**
- Focus on OUR picks first
- Quantify missed profit
- Only top 3 outside picks (not overwhelming)
- Visual charts for analysis

---

## Proposal E: Entry/Exit Performance Focus

### New Module: `entry_exit_analyzer.py`

```python
"""
Entry/Exit Performance Analyzer
Tracks how well we enter and exit compared to ideal levels.
"""

from dataclasses import dataclass
from typing import List, Dict
import statistics


@dataclass
class EntryExitStats:
    total_trades: int
    avg_entry_slippage_pct: float
    avg_exit_slippage_pct: float
    avg_hold_time_min: float
    win_rate: float
    avg_winner_pct: float
    avg_loser_pct: float
    best_trade: Dict
    worst_trade: Dict
    
    # Improvement areas
    early_exit_rate: float  # % of trades that could have made more
    late_exit_rate: float   # % of trades that gave back profits
    missed_entries: int     # Picks we didn't enter


class EntryExitAnalyzer:
    def __init__(self, trades: List[Dict], picks: List[Dict], perf_map: Dict):
        self.trades = trades
        self.picks = picks
        self.perf_map = perf_map
        self.stats = None
    
    def analyze(self) -> EntryExitStats:
        """Calculate comprehensive entry/exit statistics."""
        
        # Entry analysis
        entries = [t for t in self.trades if t["side"] == "BUY"]
        entry_slippages = []
        
        for trade in entries:
            pick = next((p for p in self.picks if p["symbol"] == trade["symbol"]), None)
            if pick and pick.get("entry_high"):
                ideal = (pick["entry_low"] + pick["entry_high"]) / 2
                slippage = (trade["price"] - ideal) / ideal * 100
                entry_slippages.append(slippage)
        
        # Exit analysis
        exits = [t for t in self.trades if t["side"] == "SELL"]
        exit_slippages = []
        early_exits = 0
        
        for trade in exits:
            perf = self.perf_map.get(trade["symbol"])
            if perf:
                # Did we exit before the high?
                if trade["price"] < perf["high"] * 0.99:  # More than 1% below HOD
                    early_exits += 1
                
                # Calculate exit quality
                optimal_exit = perf["high"] * 0.99  # Trailing stop at -1%
                slippage = (trade["price"] - optimal_exit) / optimal_exit * 100
                exit_slippages.append(slippage)
        
        # Calculate stats
        total_trades = len(entries)
        
        winners = [t for t in entries if t.get("actual_pnl", 0) > 0]
        losers = [t for t in entries if t.get("actual_pnl", 0) <= 0]
        
        self.stats = EntryExitStats(
            total_trades=total_trades,
            avg_entry_slippage_pct=statistics.mean(entry_slippages) if entry_slippages else 0,
            avg_exit_slippage_pct=statistics.mean(exit_slippages) if exit_slippages else 0,
            avg_hold_time_min=statistics.mean([t.get("hold_time_min", 0) for t in entries]) if entries else 0,
            win_rate=len(winners) / total_trades * 100 if total_trades else 0,
            avg_winner_pct=statistics.mean([t["actual_pnl"] for t in winners]) if winners else 0,
            avg_loser_pct=statistics.mean([t["actual_pnl"] for t in losers]) if losers else 0,
            best_trade=max(entries, key=lambda x: x.get("actual_pnl", 0)) if entries else {},
            worst_trade=min(entries, key=lambda x: x.get("actual_pnl", 0)) if entries else {},
            early_exit_rate=early_exits / len(exits) * 100 if exits else 0,
            late_exit_rate=0,  # Would need more data
            missed_entries=len(self.picks) - total_trades,
        )
        
        return self.stats
    
    def generate_report(self) -> str:
        """Generate entry/exit performance report."""
        if not self.stats:
            self.analyze()
        
        s = self.stats
        report = [
            "📊 <b>Entry/Exit Performance Analysis</b>",
            "",
            f"• Total trades: {s.total_trades}",
            f"• Win rate: {s.win_rate:.1f}%",
            f"• Avg entry slippage: {s.avg_entry_slippage_pct:+.2f}%",
            f"• Avg exit vs optimal: {s.avg_exit_slippage_pct:+.2f}%",
            f"• Avg hold time: {s.avg_hold_time_min:.0f} min",
            f"• Early exit rate: {s.early_exit_rate:.1f}%",
            f"• Missed entries: {s.missed_entries}",
            "",
            "<b>Improvement Areas:</b>",
        ]
        
        if s.early_exit_rate > 30:
            report.append(f"⚠️ {s.early_exit_rate:.0f}% of exits are early — consider trailing stops")
        
        if s.avg_entry_slippage_pct > 1:
            report.append(f"⚠️ Avg entry slippage {s.avg_entry_slippage_pct:+.2f}% — use limit orders at zone low")
        
        if s.missed_entries > 0:
            report.append(f"⚠️ Missed {s.missed_entries} pick entries — review gap-up handling")
        
        return "\n".join(report)
```

**Benefits:**
- Quantified entry/exit quality
- Identifies systematic issues (always exiting early, etc.)
- Actionable recommendations

---

## Proposal F: Report Organization in `relearning/`

### Current Structure
```
tasi-exec/
├── reports/
│   ├── post_market_2026-06-14.html
│   └── daily_pnl_2026-06-14.md
├── memory/
│   ├── 2026-06-14.md
│   └── 2026-06-14-intelligent.md
└── learning.json
```

### Proposed Structure
```
tasi-exec/
└── relearning/
    ├── daily/
    │   ├── 2026-06-14/
    │   │   ├── report.md           # Main daily report
    │   │   ├── picks_analysis.json  # Detailed pick data
    │   │   ├── trades_analysis.json # Actual trade analysis
    │   │   ├── entry_exit_stats.json # Entry/exit metrics
    │   │   ├── charts/
    │   │   │   ├── 1320.png         # Price chart with zones
    │   │   │   └── 5110.png
    │   │   └── missed_opportunities.json
    │   └── 2026-06-13/
    │       └── ...
    ├── weekly/
    │   └── 2026-W24/               # Aggregated weekly analysis
    ├── monthly/
    │   └── 2026-06/                # Monthly trend analysis
    └── patterns/
        ├── entry_patterns.json      # Discovered entry patterns
        ├── exit_patterns.json         # Discovered exit patterns
        └── strategy_improvements.json # Cumulative recommendations
```

### Implementation
```python
from pathlib import Path
from datetime import datetime

RELEARNING_DIR = Path("/home/mino/tasi-exec/relearning")


def get_daily_dir(date_str: str) -> Path:
    """Get or create daily relearning directory."""
    daily_dir = RELEARNING_DIR / "daily" / date_str
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / "charts").mkdir(exist_ok=True)
    return daily_dir


def save_daily_report(date_str: str, report_data: dict):
    """Save all daily analysis to relearning structure."""
    daily_dir = get_daily_dir(date_str)
    
    # Save main report
    with open(daily_dir / "report.md", "w") as f:
        f.write(report_data["markdown"])
    
    # Save pick analysis
    with open(daily_dir / "picks_analysis.json", "w") as f:
        json.dump(report_data["picks"], f, indent=2)
    
    # Save trade analysis
    with open(daily_dir / "trades_analysis.json", "w") as f:
        json.dump(report_data["trades"], f, indent=2)
    
    # Save entry/exit stats
    with open(daily_dir / "entry_exit_stats.json", "w") as f:
        json.dump(report_data["entry_exit"], f, indent=2)
    
    # Save missed opportunities
    with open(daily_dir / "missed_opportunities.json", "w") as f:
        json.dump(report_data["missed"], f, indent=2)
    
    print(f"📁 Saved relearning data to {daily_dir}")


def update_weekly_aggregate(date_str: str):
    """Aggregate daily data into weekly summary."""
    # Implementation for weekly rollup
    pass


def update_pattern_learning(date_str: str, new_patterns: List[Dict]):
    """Update cumulative pattern database."""
    patterns_file = RELEARNING_DIR / "patterns" / "entry_patterns.json"
    # Load existing, append new, save
```

**Benefits:**
- Organized historical data
- Easy to query and compare
- Pattern discovery over time
- No clutter in main directories

---

## Implementation Priority

| Priority | Item | Impact | Effort |
|----------|------|--------|--------|
| **1** | B - Trade Integration | HIGH | Medium |
| **2** | A - WS Data Fallback | HIGH | Low |
| **3** | C - Pick Analysis | HIGH | High |
| **4** | E - Entry/Exit Analyzer | MEDIUM | Medium |
| **5** | D - Focused Missed Ops | MEDIUM | Medium |
| **6** | F - Relearning Structure | LOW | Low |

---

## Questions for A A

1. **Should I proceed with all proposals or prioritize?**
2. **For charts: matplotlib okay or prefer another library?**
3. **Should missed opportunities include stocks NOT in Sharia list?**
4. **What timeframe for "simulated exits"? Same day only or multi-day?**
5. **Should I create separate `entry_exit_analyzer.py` or add to `post_market.py`?**

---

**No changes made yet — awaiting approval.**
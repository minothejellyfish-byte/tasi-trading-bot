#!/usr/bin/env python3
"""
TASI 1-Month Backtest: 2026-04-17 to 2026-05-15
Tests both rule-based and probabilistic screeners across all trading days.

Performance strategy:
- Pre-download 60d daily data for ALL tickers once at start (ThreadPoolExecutor)
- Pre-download 2y data for prob model ONLY for tickers that get picked
- Slice cached data per day — no repeated downloads
"""

import json
import os
import sys
import numpy as np
import pandas as pd
import yfinance as yf
import pandas_ta as ta
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Config ──────────────────────────────────────────────────────────────────

SHARIA_FILE   = "/home/mino/tasi-exec/sharia_list.json"
OUTPUT_FILE   = "/home/mino/tasi-exec/backtest_month_output.txt"
START_DATE    = date(2026, 4, 17)
END_DATE      = date(2026, 5, 15)

MIN_AVG_VOLUME = 500_000
MIN_PRICE      = 10.0
MAX_PRICE      = 500.0
TOP_N_RULE     = 3    # take top 3 by score for prob evaluation
TOP_N_PICKS    = 2    # final picks per strategy per day
MIN_DAYS       = 15

WIN_PCT   =  2.0
STOP_PCT  = -7.0

# Known Saudi public holidays to skip (within our backtest window)
# Eid Al-Fitr 2026: approximately April 20–22 (these were Mon-Wed but we only trade Sun-Thu)
# Based on public records, the Saudi market was closed for Eid around April 28 - May 6, 2026
# Eid Al-Fitr 1447H officially started evening of March 29, 2026 — market holiday Apr 28 - May 6 is Eid Al-Adha range which is later.
# For Eid Al-Fitr 1447: Saudi market was closed ~Mar 30 - Apr 3 2026 (before our range).
# No confirmed holiday closures within Apr 17 - May 15 2026 based on available info.
# We include all Sun-Thu days. If yfinance returns no data for a given day we skip it.
HOLIDAY_SKIP: set[date] = set()

# ─── Load universe ────────────────────────────────────────────────────────────

def load_universe() -> list[str]:
    with open(SHARIA_FILE) as f:
        data = json.load(f)
    return data["main_market_yahoo_tickers"]

# ─── Trading day generation ──────────────────────────────────────────────────

def get_trading_days(start: date, end: date) -> list[date]:
    """Return all Sun-Thu between start and end (inclusive), minus known holidays."""
    days = []
    cur = start
    while cur <= end:
        # weekday(): Mon=0 ... Sun=6
        if cur.weekday() in (6, 0, 1, 2, 3) and cur not in HOLIDAY_SKIP:
            days.append(cur)
        cur += timedelta(days=1)
    return days

# ─── Data download ────────────────────────────────────────────────────────────

def download_60d(ticker: str) -> pd.DataFrame | None:
    """Download ~60d of daily OHLCV. Returns df or None on failure."""
    try:
        # We download from 2026-02-01 to cover our window with prior-day context
        df = yf.download(
            ticker,
            start="2026-02-01",
            end="2026-05-16",   # one day past end to include May 15
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if df is None or len(df) < MIN_DAYS:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna()
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        return df
    except Exception:
        return None


def download_2y(ticker: str) -> pd.DataFrame | None:
    """Download 2y of daily OHLCV for prob model."""
    try:
        df = yf.download(
            ticker,
            period="2y",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if df is None or len(df) < 30:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna()
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        return df
    except Exception:
        return None

# ─── Inlined score_stock (no yf download, no Telegram, no logging) ────────────

def score_stock_from_df(ticker: str, df: pd.DataFrame) -> dict | None:
    """
    Score a stock using pre-downloaded data sliced to pre-market view.
    Mirrors screener.py score_stock() logic exactly.
    """
    try:
        if df is None or len(df) < 10:
            return None

        close  = df["Close"].iloc[-1]
        vol20  = df["Volume"].rolling(20).mean().iloc[-1]
        vol1   = df["Volume"].iloc[-1]

        if pd.isna(vol20) or vol20 < MIN_AVG_VOLUME:
            return None
        if close < MIN_PRICE or close > MAX_PRICE:
            return None

        # RSI filter — skip overbought
        rsi = ta.rsi(df["Close"], length=14)
        if rsi is None or pd.isna(rsi.iloc[-1]) or rsi.iloc[-1] > 70:
            return None
        rsi_val = float(rsi.iloc[-1])

        # Momentum: close vs 10-day SMA
        sma10 = df["Close"].rolling(10).mean().iloc[-1]
        if pd.isna(sma10) or sma10 == 0:
            return None
        momentum = float((close - sma10) / sma10 * 100)

        # Volume surge vs 20-day avg
        vol_ratio = float(vol1 / vol20) if vol20 > 0 else 0.0

        # S/R proximity
        if len(df) < 2:
            return None
        prev_high = df["High"].iloc[-2]
        prev_low  = df["Low"].iloc[-2]

        # 10-day resistance (excluding today)
        if len(df) < 11:
            return None
        resistance_10d = df["High"].iloc[-11:-1].max()
        dist_to_high = float((resistance_10d - close) / close * 100)

        # 20-day high breakout candidate
        high20 = df["High"].rolling(20).max().iloc[-2]
        if pd.isna(high20):
            return None
        near_breakout = bool(close > high20 * 0.98)

        # Trend: linear regression slope of last 10 closes
        closes_10 = df["Close"].iloc[-10:].values.astype(float)
        if len(closes_10) < 10:
            return None
        x = np.arange(len(closes_10))
        slope = np.polyfit(x, closes_10, 1)[0]
        trend_pct = float(slope / closes_10[0] * 100) if closes_10[0] != 0 else 0.0

        # Higher lows: last 3 daily lows each higher than the one before
        lows_3 = df["Low"].iloc[-3:].values.astype(float)
        higher_lows = bool(len(lows_3) >= 3 and lows_3[1] > lows_3[0] and lows_3[2] > lows_3[1])

        # Composite score
        score = 0.0
        score += min(momentum, 5) * 10
        score += min(vol_ratio, 3) * 15
        score += (5 - min(dist_to_high, 5)) * 5
        score += 20 if near_breakout else 0
        score -= max(rsi_val - 60, 0) * 2
        score += min(max(trend_pct * 5, -20), 15)
        score += 10 if higher_lows else 0

        # Entry zone
        entry_high = round(float(prev_high) * 1.001, 2)
        entry_low  = round(float(prev_low)  * 1.001, 2)
        stop_loss  = round(float(close) * 0.93, 2)

        # Gap-up filter
        if close > entry_high * 1.01:
            return None

        return {
            "ticker":         ticker,
            "close":          round(float(close), 2),
            "rsi":            round(rsi_val, 1),
            "momentum":       round(momentum, 2),
            "vol_ratio":      round(vol_ratio, 2),
            "near_breakout":  near_breakout,
            "entry_low":      entry_low,
            "entry_high":     entry_high,
            "stop_loss":      stop_loss,
            "score":          round(score, 1),
            "trend_pct":      round(trend_pct, 2),
            "higher_lows":    higher_lows,
            "resistance_10d": round(float(resistance_10d), 2),
        }
    except Exception:
        return None

# ─── Prob estimator (from cached 2y df) ──────────────────────────────────────

def estimate_win_prob_from_df(df2y: pd.DataFrame, conditions: dict) -> dict | None:
    """
    Mirror screener_prob.estimate_win_prob() but use pre-downloaded df.
    NOTE: uses full 2y history — minor lookahead bias, documented limitation.
    """
    try:
        if df2y is None or len(df2y) < 30:
            return None

        rsi_series = ta.rsi(df2y["Close"], length=14)
        sma10      = df2y["Close"].rolling(10).mean()
        vol20      = df2y["Volume"].rolling(20).mean()
        high20     = df2y["High"].rolling(20).max()

        momentum_series  = (df2y["Close"] - sma10) / sma10 * 100
        vol_ratio_series = df2y["Volume"] / vol20
        near_bo_series   = df2y["Close"] > high20.shift(1) * 0.98

        rsi_q = conditions["rsi"]
        mom_q = conditions["momentum"]
        vr_q  = conditions["vol_ratio"]
        nbo_q = conditions["near_breakout"]

        wins   = []
        losses = []

        for i in range(len(df2y) - 1):
            if (pd.isna(rsi_series.iloc[i]) or
                pd.isna(momentum_series.iloc[i]) or
                pd.isna(vol_ratio_series.iloc[i])):
                continue

            rsi_i = float(rsi_series.iloc[i])
            mom_i = float(momentum_series.iloc[i])
            vr_i  = float(vol_ratio_series.iloc[i])
            nbo_i = bool(near_bo_series.iloc[i])

            if abs(rsi_i - rsi_q) > 10:
                continue
            if abs(mom_i - mom_q) > 2:
                continue
            if abs(vr_i - vr_q) > 0.3:
                continue
            if nbo_i != nbo_q:
                continue

            next_open = float(df2y["Open"].iloc[i + 1])
            next_high = float(df2y["High"].iloc[i + 1])
            next_low  = float(df2y["Low"].iloc[i + 1])

            if next_open == 0:
                continue

            gain     = (next_high - next_open) / next_open
            drawdown = (next_low  - next_open) / next_open

            if gain >= 0.02 and drawdown >= -0.07:
                wins.append(gain * 100)
            else:
                losses.append(drawdown * 100)

        n = len(wins) + len(losses)
        if n < 5:
            return None

        win_rate = len(wins) / n
        avg_gain  = float(np.mean(wins))  if wins  else 0.0
        avg_loss  = float(np.mean(losses)) if losses else 0.0
        ev_pct    = win_rate * avg_gain + (1 - win_rate) * avg_loss

        return {
            "win_rate":  round(win_rate * 100, 1),
            "n_samples": n,
            "ev_pct":    round(ev_pct, 2),
        }
    except Exception:
        return None

# ─── Outcome evaluation ───────────────────────────────────────────────────────

def evaluate_outcome(trade_day_df: pd.DataFrame, entry_high: float) -> dict:
    """
    Given the actual trading day OHLC, compute outcome relative to entry_high.
    Returns outcome string and pnl_pct.
    """
    if trade_day_df is None or len(trade_day_df) == 0:
        return {"outcome": "NO_DATA", "pnl_pct": 0.0}

    day_high  = float(trade_day_df["High"].iloc[0])
    day_low   = float(trade_day_df["Low"].iloc[0])
    day_close = float(trade_day_df["Close"].iloc[0])

    triggered = day_high >= entry_high

    if not triggered:
        return {"outcome": "NO_TRIGGER", "pnl_pct": 0.0}

    # Both win and stop can occur in same candle — assume worst-case order (stop first)
    win_thresh  = entry_high * 1.02
    stop_thresh = entry_high * 0.93

    if day_low <= stop_thresh:
        # Stop was hit
        return {"outcome": "STOP", "pnl_pct": STOP_PCT}
    elif day_high >= win_thresh:
        return {"outcome": "WIN",  "pnl_pct": WIN_PCT}
    else:
        # SCRATCH: closed between entry and target
        pnl = (day_close - entry_high) / entry_high * 100
        return {"outcome": "SCRATCH", "pnl_pct": round(pnl, 2)}

# ─── Stats aggregation ────────────────────────────────────────────────────────

def compute_stats(all_picks_outcomes: list[dict]) -> dict:
    """
    all_picks_outcomes: list of {"outcome": str, "pnl_pct": float}
    """
    if not all_picks_outcomes:
        return {
            "n_picks": 0, "trigger_rate": 0, "win_rate_t": 0,
            "stop_rate_t": 0, "avg_pnl": 0, "total_pnl": 0,
        }

    n_picks     = len(all_picks_outcomes)
    triggered   = [p for p in all_picks_outcomes if p["outcome"] != "NO_TRIGGER" and p["outcome"] != "NO_DATA"]
    n_triggered = len(triggered)
    wins        = [p for p in triggered if p["outcome"] == "WIN"]
    stops       = [p for p in triggered if p["outcome"] == "STOP"]

    trigger_rate = n_triggered / n_picks * 100 if n_picks else 0
    win_rate_t   = len(wins)  / n_triggered * 100 if n_triggered else 0
    stop_rate_t  = len(stops) / n_triggered * 100 if n_triggered else 0
    avg_pnl      = np.mean([p["pnl_pct"] for p in all_picks_outcomes])
    total_pnl    = sum([p["pnl_pct"] for p in all_picks_outcomes])  # equal-sized positions

    return {
        "n_picks":      n_picks,
        "trigger_rate": round(trigger_rate, 1),
        "win_rate_t":   round(win_rate_t, 1),
        "stop_rate_t":  round(stop_rate_t, 1),
        "avg_pnl":      round(avg_pnl, 2),
        "total_pnl":    round(total_pnl, 2),
    }

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"TASI 1-MONTH BACKTEST: {START_DATE} to {END_DATE}")
    print(f"{'='*60}\n")

    # Load universe
    universe = load_universe()
    print(f"Universe: {len(universe)} Sharia-compliant tickers")

    # Generate trading days
    trading_days = get_trading_days(START_DATE, END_DATE)
    print(f"Trading days generated: {len(trading_days)}")
    print(f"Days: {[str(d) for d in trading_days]}\n")

    # ── Step 1: Pre-download 60d data for ALL tickers ────────────────────────
    print(f"Downloading 60d daily data for {len(universe)} tickers (20 workers)...")
    cache_60d: dict[str, pd.DataFrame] = {}
    failed_60d = 0

    def _dl60(tkr):
        return tkr, download_60d(tkr)

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(_dl60, t): t for t in universe}
        done = 0
        for fut in as_completed(futures):
            tkr, df = fut.result()
            if df is not None:
                cache_60d[tkr] = df
            else:
                failed_60d += 1
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(universe)} downloaded ({len(cache_60d)} ok, {failed_60d} failed)...")

    print(f"60d cache ready: {len(cache_60d)} tickers ({failed_60d} failed/skipped)\n")

    # ── Step 2: Run backtest per trading day ─────────────────────────────────
    day_records = []     # one record per trading day
    prob_cache_2y: dict[str, pd.DataFrame | None] = {}  # 2y data cache for prob picks

    for day_idx, trade_day in enumerate(trading_days):
        # "eve" = last market day before trade_day
        # For pre-market view, slice data UP TO (but not including) trade_day
        eve_cutoff = pd.Timestamp(trade_day)  # data before this date

        # Score all tickers using data up to eve of trade_day
        results = []
        for tkr, df_full in cache_60d.items():
            # Slice: only rows strictly before trade_day
            df_pre = df_full[df_full.index < eve_cutoff]
            if len(df_pre) < MIN_DAYS:
                continue
            r = score_stock_from_df(tkr, df_pre)
            if r is not None:
                results.append(r)

        results.sort(key=lambda x: x["score"], reverse=True)
        top3 = results[:TOP_N_RULE]

        if not top3:
            day_records.append({
                "date": trade_day,
                "rule_picks": [],
                "prob_picks": [],
                "combined_picks": [],
            })
            if (day_idx + 1) % 5 == 0:
                print(f"  Progress: {day_idx+1}/{len(trading_days)} days processed")
            continue

        # ── Prob estimation for top3 ─────────────────────────────────────
        # Download 2y data for tickers not yet cached
        new_tickers_for_2y = [r["ticker"] for r in top3 if r["ticker"] not in prob_cache_2y]
        if new_tickers_for_2y:
            def _dl2y(tkr):
                return tkr, download_2y(tkr)
            with ThreadPoolExecutor(max_workers=5) as ex:
                for fut in as_completed({ex.submit(_dl2y, t): t for t in new_tickers_for_2y}):
                    tkr, df2y = fut.result()
                    prob_cache_2y[tkr] = df2y  # may be None

        for r in top3:
            df2y = prob_cache_2y.get(r["ticker"])
            conditions = {
                "rsi":          r["rsi"],
                "momentum":     r["momentum"],
                "vol_ratio":    r["vol_ratio"],
                "near_breakout": r["near_breakout"],
            }
            prob = estimate_win_prob_from_df(df2y, conditions) if df2y is not None else None
            if prob:
                r["win_rate"]  = prob["win_rate"]
                r["n_samples"] = prob["n_samples"]
                r["ev_pct"]    = prob["ev_pct"]
            else:
                r["win_rate"]  = None
                r["n_samples"] = None
                r["ev_pct"]    = None

        # ── Determine picks per strategy ──────────────────────────────────
        # Rule-based: top 2 by score
        rule_picks = top3[:TOP_N_PICKS]

        # Prob-based: top 2 by win_rate (among those with prob data)
        prob_ranked = sorted(
            [r for r in top3 if r.get("win_rate") is not None],
            key=lambda x: x["win_rate"],
            reverse=True,
        )
        prob_picks = prob_ranked[:TOP_N_PICKS]

        # Combined: stocks where both agree (in both rule top2 AND prob top2)
        rule_tickers = {r["ticker"] for r in rule_picks}
        prob_tickers = {r["ticker"] for r in prob_picks}
        combined_tickers = rule_tickers & prob_tickers
        combined_picks = [r for r in top3 if r["ticker"] in combined_tickers]

        # ── Evaluate actual outcomes for that trading day ─────────────────
        for r in top3:
            tkr = r["ticker"]
            entry_high = r["entry_high"]

            # Slice the actual trading day row from 60d cache
            df_full = cache_60d.get(tkr)
            trade_ts = pd.Timestamp(trade_day)
            if df_full is not None:
                day_row = df_full[df_full.index == trade_ts]
            else:
                day_row = pd.DataFrame()

            outcome_info = evaluate_outcome(day_row, entry_high)
            r["outcome"]  = outcome_info["outcome"]
            r["pnl_pct"]  = outcome_info["pnl_pct"]

        day_records.append({
            "date":           trade_day,
            "rule_picks":     rule_picks,
            "prob_picks":     prob_picks,
            "combined_picks": combined_picks,
        })

        if (day_idx + 1) % 5 == 0 or day_idx == len(trading_days) - 1:
            print(f"  Progress: {day_idx+1}/{len(trading_days)} days processed (last: {trade_day})")

    print()

    # ── Step 3: Aggregate stats ──────────────────────────────────────────────

    rule_all_outcomes     = []
    prob_all_outcomes     = []
    combined_all_outcomes = []

    for rec in day_records:
        for r in rec["rule_picks"]:
            rule_all_outcomes.append({"outcome": r["outcome"], "pnl_pct": r["pnl_pct"]})
        for r in rec["prob_picks"]:
            prob_all_outcomes.append({"outcome": r["outcome"], "pnl_pct": r["pnl_pct"]})
        for r in rec["combined_picks"]:
            combined_all_outcomes.append({"outcome": r["outcome"], "pnl_pct": r["pnl_pct"]})

    rule_stats     = compute_stats(rule_all_outcomes)
    prob_stats     = compute_stats(prob_all_outcomes)
    combined_stats = compute_stats(combined_all_outcomes)

    # Best/worst days for rule-based
    def day_pnl(rec, strategy="rule"):
        picks = rec[f"{strategy}_picks"]
        return sum(r["pnl_pct"] for r in picks)

    day_pnls_rule = [(rec["date"], day_pnl(rec, "rule"), rec["rule_picks"]) for rec in day_records if rec["rule_picks"]]
    best_day_rule  = max(day_pnls_rule, key=lambda x: x[1]) if day_pnls_rule else None
    worst_day_rule = min(day_pnls_rule, key=lambda x: x[1]) if day_pnls_rule else None

    # ── Step 4: Build output ─────────────────────────────────────────────────

    lines = []

    def L(s=""):
        lines.append(s)

    L(f"{'='*65}")
    L(f"  1-MONTH BACKTEST: {START_DATE} to {END_DATE}")
    L(f"{'='*65}")
    L(f"Trading days analysed: {len(trading_days)}")
    L(f"Universe size: {len(universe)} tickers | Cache loaded: {len(cache_60d)}")
    L()
    L("NOTE: Prob model uses full 2y history — minor lookahead bias in win probability")
    L("      estimates. Outcome evaluation uses strict day-of OHLC (no lookahead).")
    L()

    def fmt_stats(name, st):
        L(f"{name}:")
        if st["n_picks"] == 0:
            L("  No picks available.")
            return
        L(f"  Total picks:              {st['n_picks']} ({st['n_picks']//len(trading_days) if trading_days else 0:.0f}/day avg)")
        L(f"  Trigger rate:             {st['trigger_rate']}% of picks actually broke out")
        L(f"  Win rate (triggered):     {st['win_rate_t']}% of triggered trades hit +2%")
        L(f"  Stop rate (triggered):    {st['stop_rate_t']}% of triggered trades hit -7%")
        L(f"  Avg P&L per pick:         {st['avg_pnl']:+.2f}% (includes no-triggers as 0%)")
        L(f"  Total P&L (equal-sized):  {st['total_pnl']:+.2f}%")
        L()

    fmt_stats("RULE-BASED (top 2 by score per day)", rule_stats)
    fmt_stats("PROB-BASED (top 2 by P(win) per day, where available)", prob_stats)
    fmt_stats("COMBINED (both agree = trade, else skip)", combined_stats)

    # Best / worst day
    if best_day_rule:
        d, pnl, picks = best_day_rule
        pick_str = ", ".join(f"{r['ticker']} ({r['outcome']} {r['pnl_pct']:+.1f}%)" for r in picks)
        L(f"Best day (rule):   {d} — {pick_str} → total {pnl:+.1f}%")
    if worst_day_rule:
        d, pnl, picks = worst_day_rule
        pick_str = ", ".join(f"{r['ticker']} ({r['outcome']} {r['pnl_pct']:+.1f}%)" for r in picks)
        L(f"Worst day (rule):  {d} — {pick_str} → total {pnl:+.1f}%")

    L()
    L(f"{'='*65}")
    L("FULL PER-DAY DETAIL")
    L(f"{'='*65}")

    header = f"{'DATE':<12} {'STRATEGY':<8} {'PICKS':<55} {'OUTCOMES'}"
    L(header)
    L("-" * 110)

    for rec in day_records:
        d = str(rec["date"])

        def picks_str(picks):
            if not picks:
                return "(no picks)"
            return " | ".join(
                f"{r['ticker']} score={r['score']} entry={r['entry_high']:.2f} → {r.get('outcome','?')} {r.get('pnl_pct',0):+.1f}%"
                for r in picks
            )

        L(f"{d:<12} RULE     {picks_str(rec['rule_picks'])}")
        if rec["prob_picks"]:
            L(f"{'':12} PROB     {picks_str(rec['prob_picks'])}")
        if rec["combined_picks"]:
            L(f"{'':12} COMBINED {picks_str(rec['combined_picks'])}")
        L()

    full_output = "\n".join(lines)

    # Save to file
    with open(OUTPUT_FILE, "w") as f:
        f.write(full_output)
    print(f"Full detail saved to: {OUTPUT_FILE}\n")

    # ── Print summary to stdout ──────────────────────────────────────────────
    print("\n" + "="*65)
    print(f"  1-MONTH BACKTEST: {START_DATE} to {END_DATE}")
    print("="*65)
    print(f"Trading days analysed: {len(trading_days)}")
    print()

    def print_stats(name, st):
        print(f"{name}:")
        if st["n_picks"] == 0:
            print("  No picks available.")
            return
        print(f"  Trigger rate:          {st['trigger_rate']}%")
        print(f"  Win rate (triggered):  {st['win_rate_t']}%")
        print(f"  Stop rate (triggered): {st['stop_rate_t']}%")
        print(f"  Avg P&L per pick:      {st['avg_pnl']:+.2f}%")
        print(f"  Total P&L:             {st['total_pnl']:+.2f}%")
        print()

    print_stats("RULE-BASED (top 2 picks/day)", rule_stats)
    print_stats("PROB-BASED (top 2 by P(win))", prob_stats)
    print_stats("COMBINED (agreement only)", combined_stats)

    if best_day_rule:
        d, pnl, picks = best_day_rule
        pick_str = ", ".join(f"{r['ticker']}→{r['outcome']}" for r in picks)
        print(f"Best day:  {d} | {pick_str} | {pnl:+.1f}%")
    if worst_day_rule:
        d, pnl, picks = worst_day_rule
        pick_str = ", ".join(f"{r['ticker']}→{r['outcome']}" for r in picks)
        print(f"Worst day: {d} | {pick_str} | {pnl:+.1f}%")

    print()
    print("─── Last 10 trading days (rule-based) ───")
    print(f"{'DATE':<12} {'PICK 1':<30} {'PICK 2':<30} {'DAY P&L':>8}")
    print("-" * 85)
    for rec in day_records[-10:]:
        d = str(rec["date"])
        picks = rec["rule_picks"]
        p1 = f"{picks[0]['ticker']}→{picks[0]['outcome']} {picks[0]['pnl_pct']:+.1f}%" if len(picks) > 0 else "(none)"
        p2 = f"{picks[1]['ticker']}→{picks[1]['outcome']} {picks[1]['pnl_pct']:+.1f}%" if len(picks) > 1 else "(none)"
        day_total = sum(r["pnl_pct"] for r in picks)
        print(f"{d:<12} {p1:<30} {p2:<30} {day_total:>+7.1f}%")

    print()
    print(f"Full detail: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

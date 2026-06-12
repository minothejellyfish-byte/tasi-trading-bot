#!/usr/bin/env python3
"""
TASI Capital Growth Backtest: 2026-04-17 to 2026-05-15
Simulates 1,000 SAR growth across 3 strategies.

Strategy A — Rule-based only (top 2 by score)
Strategy B — Prob as veto (top 3 rule → reject P(win)<20% → top 2 remaining)
Strategy C — Tuned prob model (6mo lookback, no near_breakout condition, top 2 by P(win))
"""

import json
import os
import sys
import numpy as np
import pandas as pd
import yfinance as yf
import pandas_ta as ta
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Config ───────────────────────────────────────────────────────────────────

SHARIA_FILE  = "/home/mino/tasi-exec/sharia_list.json"
OUTPUT_FILE  = "/home/mino/tasi-exec/backtest_capital_output.txt"
START_DATE   = date(2026, 4, 17)
END_DATE     = date(2026, 5, 15)

MIN_AVG_VOLUME = 500_000
MIN_PRICE      = 10.0
MAX_PRICE      = 500.0
TOP_N_RULE     = 5    # score top 5 to feed prob models
TOP_N_PICKS    = 2    # final picks per strategy per day
MIN_DAYS       = 15

WIN_PCT   =  2.0
STOP_PCT  = -7.0

# Capital simulation
START_CAPITAL   = 1_000.0
POSITION_PCT    = 0.40   # 40% per pick (2 picks = 80% deployed)

HOLIDAY_SKIP: set[date] = set()

# ─── Load universe ─────────────────────────────────────────────────────────────

def load_universe() -> list[str]:
    with open(SHARIA_FILE) as f:
        data = json.load(f)
    return data["main_market_yahoo_tickers"]

# ─── Trading days ──────────────────────────────────────────────────────────────

def get_trading_days(start: date, end: date) -> list[date]:
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() in (6, 0, 1, 2, 3) and cur not in HOLIDAY_SKIP:
            days.append(cur)
        cur += timedelta(days=1)
    return days

# ─── Data download ─────────────────────────────────────────────────────────────

def download_60d(ticker: str) -> pd.DataFrame | None:
    try:
        df = yf.download(
            ticker,
            start="2026-02-01",
            end="2026-05-16",
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

# ─── Score stock ───────────────────────────────────────────────────────────────

def score_stock_from_df(ticker: str, df: pd.DataFrame) -> dict | None:
    try:
        if df is None or len(df) < 10:
            return None

        close = df["Close"].iloc[-1]
        vol20 = df["Volume"].rolling(20).mean().iloc[-1]
        vol1  = df["Volume"].iloc[-1]

        if pd.isna(vol20) or vol20 < MIN_AVG_VOLUME:
            return None
        if close < MIN_PRICE or close > MAX_PRICE:
            return None

        rsi = ta.rsi(df["Close"], length=14)
        if rsi is None or pd.isna(rsi.iloc[-1]) or rsi.iloc[-1] > 70:
            return None
        rsi_val = float(rsi.iloc[-1])

        sma10 = df["Close"].rolling(10).mean().iloc[-1]
        if pd.isna(sma10) or sma10 == 0:
            return None
        momentum = float((close - sma10) / sma10 * 100)

        vol_ratio = float(vol1 / vol20) if vol20 > 0 else 0.0

        if len(df) < 2:
            return None
        prev_high = df["High"].iloc[-2]
        prev_low  = df["Low"].iloc[-2]

        if len(df) < 11:
            return None
        resistance_10d = df["High"].iloc[-11:-1].max()
        dist_to_high = float((resistance_10d - close) / close * 100)

        high20 = df["High"].rolling(20).max().iloc[-2]
        if pd.isna(high20):
            return None
        near_breakout = bool(close > high20 * 0.98)

        closes_10 = df["Close"].iloc[-10:].values.astype(float)
        if len(closes_10) < 10:
            return None
        x = np.arange(len(closes_10))
        slope = np.polyfit(x, closes_10, 1)[0]
        trend_pct = float(slope / closes_10[0] * 100) if closes_10[0] != 0 else 0.0

        lows_3 = df["Low"].iloc[-3:].values.astype(float)
        higher_lows = bool(len(lows_3) >= 3 and lows_3[1] > lows_3[0] and lows_3[2] > lows_3[1])

        score = 0.0
        score += min(momentum, 5) * 10
        score += min(vol_ratio, 3) * 15
        score += (5 - min(dist_to_high, 5)) * 5
        score += 20 if near_breakout else 0
        score -= max(rsi_val - 60, 0) * 2
        score += min(max(trend_pct * 5, -20), 15)
        score += 10 if higher_lows else 0

        entry_high = round(float(prev_high) * 1.001, 2)
        entry_low  = round(float(prev_low)  * 1.001, 2)
        stop_loss  = round(float(close) * 0.93, 2)

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

# ─── Prob estimators ───────────────────────────────────────────────────────────

def estimate_win_prob_2y(df2y: pd.DataFrame, conditions: dict) -> dict | None:
    """
    Strategy B: standard 2y lookback WITH near_breakout condition.
    Mirrors screener_prob.estimate_win_prob logic exactly.
    """
    try:
        if df2y is None or len(df2y) < 30:
            return None

        rsi_series      = ta.rsi(df2y["Close"], length=14)
        sma10           = df2y["Close"].rolling(10).mean()
        vol20           = df2y["Volume"].rolling(20).mean()
        high20          = df2y["High"].rolling(20).max()
        momentum_series = (df2y["Close"] - sma10) / sma10 * 100
        vol_ratio_series = df2y["Volume"] / vol20
        near_bo_series  = df2y["Close"] > high20.shift(1) * 0.98

        rsi_q = conditions["rsi"]
        mom_q = conditions["momentum"]
        vr_q  = conditions["vol_ratio"]
        nbo_q = conditions["near_breakout"]

        wins, losses = [], []

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
        avg_gain = float(np.mean(wins))  if wins  else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        ev_pct   = win_rate * avg_gain + (1 - win_rate) * avg_loss

        return {
            "win_rate":  round(win_rate * 100, 1),
            "n_samples": n,
            "ev_pct":    round(ev_pct, 2),
        }
    except Exception:
        return None


def estimate_win_prob_tuned(df2y: pd.DataFrame, conditions: dict) -> dict | None:
    """
    Strategy C: 6-month lookback, NO near_breakout condition.
    Uses only RSI ±10, momentum ±2%, vol_ratio ±30%.
    Slice df2y to last 6 months inline.
    """
    try:
        if df2y is None or len(df2y) < 30:
            return None

        # Slice to 6-month lookback
        cutoff_6mo = df2y.index[-1] - pd.DateOffset(months=6)
        df6mo = df2y[df2y.index >= cutoff_6mo].copy()

        if len(df6mo) < 30:
            return None

        rsi_series       = ta.rsi(df6mo["Close"], length=14)
        sma10            = df6mo["Close"].rolling(10).mean()
        vol20            = df6mo["Volume"].rolling(20).mean()
        momentum_series  = (df6mo["Close"] - sma10) / sma10 * 100
        vol_ratio_series = df6mo["Volume"] / vol20

        rsi_q = conditions["rsi"]
        mom_q = conditions["momentum"]
        vr_q  = conditions["vol_ratio"]
        # near_breakout intentionally NOT used in matching

        wins, losses = [], []

        for i in range(len(df6mo) - 1):
            if (pd.isna(rsi_series.iloc[i]) or
                pd.isna(momentum_series.iloc[i]) or
                pd.isna(vol_ratio_series.iloc[i])):
                continue

            rsi_i = float(rsi_series.iloc[i])
            mom_i = float(momentum_series.iloc[i])
            vr_i  = float(vol_ratio_series.iloc[i])

            if abs(rsi_i - rsi_q) > 10:
                continue
            if abs(mom_i - mom_q) > 2:
                continue
            if abs(vr_i - vr_q) > 0.3:
                continue

            next_open = float(df6mo["Open"].iloc[i + 1])
            next_high = float(df6mo["High"].iloc[i + 1])
            next_low  = float(df6mo["Low"].iloc[i + 1])

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
        avg_gain = float(np.mean(wins))  if wins  else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        ev_pct   = win_rate * avg_gain + (1 - win_rate) * avg_loss

        return {
            "win_rate":  round(win_rate * 100, 1),
            "n_samples": n,
            "ev_pct":    round(ev_pct, 2),
        }
    except Exception:
        return None

# ─── Outcome evaluation ────────────────────────────────────────────────────────

def evaluate_outcome(trade_day_df: pd.DataFrame, entry_high: float) -> dict:
    if trade_day_df is None or len(trade_day_df) == 0:
        return {"outcome": "NO_DATA", "pnl_pct": 0.0}

    day_high  = float(trade_day_df["High"].iloc[0])
    day_low   = float(trade_day_df["Low"].iloc[0])
    day_close = float(trade_day_df["Close"].iloc[0])

    triggered = day_high >= entry_high

    if not triggered:
        return {"outcome": "NO_TRIGGER", "pnl_pct": 0.0}

    win_thresh  = entry_high * 1.02
    stop_thresh = entry_high * 0.93

    # Worst-case: assume stop hits before win if both possible in same candle
    if day_low <= stop_thresh:
        return {"outcome": "STOP", "pnl_pct": STOP_PCT}
    elif day_high >= win_thresh:
        return {"outcome": "WIN", "pnl_pct": WIN_PCT}
    else:
        pnl = (day_close - entry_high) / entry_high * 100
        # If day closed below entry_high after triggering, use actual negative
        return {"outcome": "SCRATCH", "pnl_pct": round(pnl, 2)}

# ─── Capital simulation ────────────────────────────────────────────────────────

def simulate_capital(picks_per_day: list[list[dict]], outcomes_per_day: list[list[dict]]) -> tuple[list[float], dict]:
    """
    Given list of picks (each pick has outcome + pnl_pct) per day,
    simulate capital growth from START_CAPITAL.

    Returns (daily_capital_after: list[float], summary: dict)
    """
    capital = START_CAPITAL
    daily_caps = []

    total_wins      = 0
    total_stops     = 0
    total_scratches = 0
    total_no_trigger = 0

    for day_picks in picks_per_day:
        for pick in day_picks:
            outcome = pick.get("outcome", "NO_DATA")
            pnl_pct = pick.get("pnl_pct", 0.0)
            position = capital * POSITION_PCT

            if outcome == "WIN":
                capital += position * WIN_PCT / 100
                total_wins += 1
            elif outcome == "STOP":
                capital += position * STOP_PCT / 100
                total_stops += 1
            elif outcome == "SCRATCH":
                capital += position * pnl_pct / 100
                total_scratches += 1
            elif outcome in ("NO_TRIGGER", "NO_DATA"):
                # No change
                total_no_trigger += 1

        capital = round(capital, 2)
        daily_caps.append(capital)

    return daily_caps, {
        "final_capital": capital,
        "wins":          total_wins,
        "stops":         total_stops,
        "scratches":     total_scratches,
        "no_trigger":    total_no_trigger,
    }

# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"TASI CAPITAL SIMULATION: {START_DATE} to {END_DATE}")
    print(f"{'='*60}\n")

    universe     = load_universe()
    trading_days = get_trading_days(START_DATE, END_DATE)
    print(f"Universe: {len(universe)} tickers | Trading days: {len(trading_days)}")
    print(f"Days: {[str(d) for d in trading_days]}\n")

    # ── Step 1: Pre-download 60d data for ALL tickers ─────────────────────────
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

    print(f"60d cache: {len(cache_60d)} tickers loaded ({failed_60d} failed)\n")

    # ── Step 2: Run per-day logic ──────────────────────────────────────────────
    prob_cache_2y: dict[str, pd.DataFrame | None] = {}

    # Per-strategy daily picks (list of lists, one per trading day)
    picks_A: list[list[dict]] = []
    picks_B: list[list[dict]] = []
    picks_C: list[list[dict]] = []

    for day_idx, trade_day in enumerate(trading_days):
        eve_cutoff = pd.Timestamp(trade_day)

        # Score all tickers using pre-trade data
        results = []
        for tkr, df_full in cache_60d.items():
            df_pre = df_full[df_full.index < eve_cutoff]
            if len(df_pre) < MIN_DAYS:
                continue
            r = score_stock_from_df(tkr, df_pre)
            if r is not None:
                results.append(r)

        results.sort(key=lambda x: x["score"], reverse=True)
        top5 = results[:TOP_N_RULE]

        if not top5:
            picks_A.append([])
            picks_B.append([])
            picks_C.append([])
            print(f"  Day {day_idx+1}/{len(trading_days)} ({trade_day}): no picks")
            continue

        # ── Download 2y data for top5 not yet cached ──────────────────────────
        new_tickers = [r["ticker"] for r in top5 if r["ticker"] not in prob_cache_2y]
        if new_tickers:
            def _dl2y(tkr):
                return tkr, download_2y(tkr)
            with ThreadPoolExecutor(max_workers=5) as ex:
                for fut in as_completed({ex.submit(_dl2y, t): t for t in new_tickers}):
                    tkr, df2y = fut.result()
                    prob_cache_2y[tkr] = df2y

        # ── Attach prob data to each top5 pick ────────────────────────────────
        for r in top5:
            df2y = prob_cache_2y.get(r["ticker"])
            conditions = {
                "rsi":           r["rsi"],
                "momentum":      r["momentum"],
                "vol_ratio":     r["vol_ratio"],
                "near_breakout": r["near_breakout"],
            }
            # Strategy B prob (2y + near_breakout condition)
            prob_b = estimate_win_prob_2y(df2y, conditions) if df2y is not None else None
            r["win_rate_b"]  = prob_b["win_rate"]  if prob_b else None
            r["n_samples_b"] = prob_b["n_samples"] if prob_b else None

            # Strategy C prob (6mo, no near_breakout)
            prob_c = estimate_win_prob_tuned(df2y, conditions) if df2y is not None else None
            r["win_rate_c"]  = prob_c["win_rate"]  if prob_c else None
            r["n_samples_c"] = prob_c["n_samples"] if prob_c else None

        # ── Evaluate actual outcomes for trade_day ────────────────────────────
        trade_ts = pd.Timestamp(trade_day)
        for r in top5:
            tkr = r["ticker"]
            df_full = cache_60d.get(tkr)
            if df_full is not None:
                day_row = df_full[df_full.index == trade_ts]
            else:
                day_row = pd.DataFrame()
            outcome_info = evaluate_outcome(day_row, r["entry_high"])
            r["outcome"] = outcome_info["outcome"]
            r["pnl_pct"] = outcome_info["pnl_pct"]

        # ── Strategy A: top 2 by score ────────────────────────────────────────
        day_A = [dict(r) for r in top5[:TOP_N_PICKS]]

        # ── Strategy B: prob veto (P(win) < 20% rejected) ────────────────────
        top3_b = top5[:3]  # top 3 by score
        remaining_b = [
            r for r in top3_b
            if not (r["win_rate_b"] is not None and r["win_rate_b"] < 20.0)
        ]
        day_B_raw = remaining_b[:TOP_N_PICKS]
        # If fewer than 2, fill with next rule-based picks (no veto on fallbacks)
        if len(day_B_raw) < TOP_N_PICKS:
            used_tickers = {r["ticker"] for r in day_B_raw}
            fallbacks = [r for r in top5 if r["ticker"] not in used_tickers]
            day_B_raw = day_B_raw + fallbacks[:TOP_N_PICKS - len(day_B_raw)]
        day_B = [dict(r) for r in day_B_raw]

        # ── Strategy C: top 2 by win_rate_c ──────────────────────────────────
        has_prob_c = [r for r in top5 if r["win_rate_c"] is not None]
        has_prob_c.sort(key=lambda x: x["win_rate_c"], reverse=True)
        day_C_raw = has_prob_c[:TOP_N_PICKS]
        # Fill from rule-based if fewer than 2 have prob data
        if len(day_C_raw) < TOP_N_PICKS:
            used_tickers = {r["ticker"] for r in day_C_raw}
            fallbacks = [r for r in top5 if r["ticker"] not in used_tickers]
            day_C_raw = day_C_raw + fallbacks[:TOP_N_PICKS - len(day_C_raw)]
        day_C = [dict(r) for r in day_C_raw]

        picks_A.append(day_A)
        picks_B.append(day_B)
        picks_C.append(day_C)

        if (day_idx + 1) % 3 == 0 or day_idx == len(trading_days) - 1:
            print(f"  Day {day_idx+1}/{len(trading_days)} ({trade_day}) done")

    print()

    # ── Step 3: Capital simulation ─────────────────────────────────────────────
    caps_A, summary_A = simulate_capital(picks_A, None)
    caps_B, summary_B = simulate_capital(picks_B, None)
    caps_C, summary_C = simulate_capital(picks_C, None)

    # ── Step 4: Build output ───────────────────────────────────────────────────
    lines = []

    def L(s=""):
        lines.append(s)

    L(f"=== CAPITAL SIMULATION: 1,000 SAR | {START_DATE} to {END_DATE} ===")
    L()
    L(f"{'Date':<12}| {'A: Rule-Based':>13} | {'B: Prob Veto':>12} | {'C: Tuned Prob':>13}")
    L(f"{'-'*11}|{'-'*15}|{'-'*14}|{'-'*15}")

    for i, d in enumerate(trading_days):
        ca = caps_A[i]
        cb = caps_B[i]
        cc = caps_C[i]
        L(f"{str(d):<12}| {ca:>10,.0f} SAR | {cb:>9,.0f} SAR | {cc:>10,.0f} SAR")

    L()
    L("FINAL RESULTS:")
    pct_A = (summary_A["final_capital"] - START_CAPITAL) / START_CAPITAL * 100
    pct_B = (summary_B["final_capital"] - START_CAPITAL) / START_CAPITAL * 100
    pct_C = (summary_C["final_capital"] - START_CAPITAL) / START_CAPITAL * 100

    L(f"Strategy A (Rule-Based):   {summary_A['final_capital']:>7,.0f} SAR  ({pct_A:+.1f}%)  "
      f"— {summary_A['wins']} wins, {summary_A['stops']} stops, "
      f"{summary_A['scratches']} scratch, {summary_A['no_trigger']} no-trigger")
    L(f"Strategy B (Prob Veto):    {summary_B['final_capital']:>7,.0f} SAR  ({pct_B:+.1f}%)  "
      f"— {summary_B['wins']} wins, {summary_B['stops']} stops, "
      f"{summary_B['scratches']} scratch, {summary_B['no_trigger']} no-trigger")
    L(f"Strategy C (Tuned Prob):   {summary_C['final_capital']:>7,.0f} SAR  ({pct_C:+.1f}%)  "
      f"— {summary_C['wins']} wins, {summary_C['stops']} stops, "
      f"{summary_C['scratches']} scratch, {summary_C['no_trigger']} no-trigger")

    L()

    # Best/worst strategy by final capital
    strats = [("A", summary_A["final_capital"]), ("B", summary_B["final_capital"]), ("C", summary_C["final_capital"])]
    best_strat  = max(strats, key=lambda x: x[1])
    worst_strat = min(strats, key=lambda x: x[1])
    L(f"Best strategy:  {best_strat[0]}")

    # Best/worst day per strategy
    def day_gain(caps, idx):
        prev = caps[idx - 1] if idx > 0 else START_CAPITAL
        return caps[idx] - prev

    for name, caps in [("A", caps_A), ("B", caps_B), ("C", caps_C)]:
        gains = [day_gain(caps, i) for i in range(len(trading_days))]
        best_i  = int(np.argmax(gains))
        worst_i = int(np.argmin(gains))
        L(f"Strategy {name} — Best day:  {trading_days[best_i]}  ({gains[best_i]:+.2f} SAR)  "
          f"| Worst day: {trading_days[worst_i]}  ({gains[worst_i]:+.2f} SAR)")

    L()

    # ── Detailed per-day log ──────────────────────────────────────────────────
    L("="*70)
    L("DETAILED PER-DAY PICKS")
    L("="*70)

    for i, d in enumerate(trading_days):
        L(f"\n{d}")
        for strat_name, day_picks in [("A", picks_A[i]), ("B", picks_B[i]), ("C", picks_C[i])]:
            if day_picks:
                picks_str = "  |  ".join(
                    f"{r['ticker']} score={r['score']} entry={r['entry_high']:.2f} "
                    f"wb={r.get('win_rate_b','?')} wc={r.get('win_rate_c','?')} "
                    f"→ {r.get('outcome','?')} {r.get('pnl_pct',0):+.1f}%"
                    for r in day_picks
                )
            else:
                picks_str = "(no picks)"
            L(f"  [{strat_name}] {picks_str}")

    full_output = "\n".join(lines)

    with open(OUTPUT_FILE, "w") as f:
        f.write(full_output)
    print(f"Full output saved to: {OUTPUT_FILE}\n")

    # ── Print summary table to stdout ─────────────────────────────────────────
    summary_start = next(i for i, line in enumerate(lines) if line.startswith("==="))
    for line in lines[summary_start:]:
        if line.startswith("="*70):
            break
        print(line)

    print(f"\nFull log: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

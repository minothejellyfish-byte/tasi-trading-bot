#!/usr/bin/env python3
"""
TASI Backtest — Thursday 2026-05-15
Simulates pre-market screening as of Wed 2026-05-14 close,
then scores picks against actual Thursday data.
"""

import json
import os
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf
import pandas_ta as ta

warnings.filterwarnings("ignore")

# ─── Config ──────────────────────────────────────────────────────────────────

SHARIA_FILE   = "/home/mino/tasi-exec/sharia_list.json"
MIN_AVG_VOLUME = 500_000
MIN_PRICE      = 10.0
MAX_PRICE      = 500.0
TOP_N          = 5

# Data boundary: simulate pre-market view as of Wed close
BACKTEST_END   = "2026-05-15"   # yf end is exclusive → data through 2026-05-14
THURSDAY_START = "2026-05-15"
THURSDAY_END   = "2026-05-16"


# ─── Load Universe ────────────────────────────────────────────────────────────

def load_sharia_universe() -> list:
    with open(SHARIA_FILE) as f:
        data = json.load(f)
    return data.get("main_market_yahoo_tickers", [])


# ─── Score (patched to use data up to 2026-05-14) ────────────────────────────

def score_stock_backtest(ticker: str) -> dict | None:
    try:
        df = yf.download(
            ticker,
            end=BACKTEST_END,
            period="35d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if df is None or len(df) < 10:
            return None

        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna()

        close  = df["Close"].iloc[-1]
        vol20  = df["Volume"].rolling(20).mean().iloc[-1]
        vol1   = df["Volume"].iloc[-1]

        if vol20 < MIN_AVG_VOLUME or close < MIN_PRICE or close > MAX_PRICE:
            return None

        rsi = ta.rsi(df["Close"], length=14)
        if rsi is None or rsi.iloc[-1] > 70:
            return None
        rsi_val = rsi.iloc[-1]

        sma10       = df["Close"].rolling(10).mean().iloc[-1]
        momentum    = (close - sma10) / sma10 * 100
        vol_ratio   = vol1 / vol20 if vol20 > 0 else 0

        prev_high   = df["High"].iloc[-2]
        prev_low    = df["Low"].iloc[-2]

        resistance_10d = df["High"].iloc[-11:-1].max()
        dist_to_high   = (resistance_10d - close) / close * 100
        dist_to_low    = (close - prev_low) / close * 100

        high20       = df["High"].rolling(20).max().iloc[-2]
        near_breakout = close > high20 * 0.98

        closes_10 = df["Close"].iloc[-10:].values
        x         = np.arange(len(closes_10))
        slope     = np.polyfit(x, closes_10, 1)[0]
        trend_pct = slope / closes_10[0] * 100

        lows_3      = df["Low"].iloc[-3:].values
        higher_lows = bool(lows_3[1] > lows_3[0] and lows_3[2] > lows_3[1])

        score = 0
        score += min(momentum, 5) * 10
        score += min(vol_ratio, 3) * 15
        score += (5 - min(dist_to_high, 5)) * 5
        score += 20 if near_breakout else 0
        score -= max(rsi_val - 60, 0) * 2
        score += min(max(trend_pct * 5, -20), 15)
        score += 10 if higher_lows else 0

        entry_low  = round(prev_low * 1.001, 2)
        entry_high = round(prev_high * 1.001, 2)
        stop_loss  = round(close * 0.93, 2)

        if close > entry_high * 1.01:
            return None

        return {
            "ticker":        ticker,
            "close":         round(float(close), 2),
            "rsi":           round(float(rsi_val), 1),
            "momentum":      round(float(momentum), 2),
            "vol_ratio":     round(float(vol_ratio), 2),
            "near_breakout": near_breakout,
            "entry_low":     entry_low,
            "entry_high":    entry_high,
            "stop_loss":     stop_loss,
            "score":         round(float(score), 1),
            "trend_pct":     round(float(trend_pct), 2),
            "higher_lows":   higher_lows,
            "resistance_10d": round(float(resistance_10d), 2),
            "prev_high":     round(float(prev_high), 2),
            "prev_low":      round(float(prev_low), 2),
        }
    except Exception:
        return None


# ─── Prob estimator (from screener_prob) ─────────────────────────────────────

def estimate_win_prob(ticker: str, conditions: dict) -> dict | None:
    try:
        df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
        if df is None or len(df) < 30:
            return None

        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna()

        rsi_series       = ta.rsi(df["Close"], length=14)
        sma10            = df["Close"].rolling(10).mean()
        vol20            = df["Volume"].rolling(20).mean()
        high20           = df["High"].rolling(20).max()
        momentum_series  = (df["Close"] - sma10) / sma10 * 100
        vol_ratio_series = df["Volume"] / vol20
        near_bo_series   = df["Close"] > high20.shift(1) * 0.98

        rsi_q = conditions["rsi"]
        mom_q = conditions["momentum"]
        vr_q  = conditions["vol_ratio"]
        nbo_q = conditions["near_breakout"]

        wins, losses = [], []
        for i in range(len(df) - 1):
            if pd.isna(rsi_series.iloc[i]) or pd.isna(momentum_series.iloc[i]) or pd.isna(vol_ratio_series.iloc[i]):
                continue
            rsi_i = rsi_series.iloc[i]
            mom_i = momentum_series.iloc[i]
            vr_i  = vol_ratio_series.iloc[i]
            nbo_i = near_bo_series.iloc[i]

            if abs(rsi_i - rsi_q) > 10: continue
            if abs(mom_i - mom_q) > 2:  continue
            if abs(vr_i - vr_q) > 0.3:  continue
            if nbo_i != nbo_q:           continue

            next_open = df["Open"].iloc[i + 1]
            next_high = df["High"].iloc[i + 1]
            next_low  = df["Low"].iloc[i + 1]

            if next_open == 0:
                continue

            gain     = (next_high - next_open) / next_open
            drawdown = (next_low - next_open) / next_open

            if gain >= 0.02 and drawdown >= -0.07:
                wins.append(gain * 100)
            else:
                losses.append(drawdown * 100)

        n = len(wins) + len(losses)
        if n < 5:
            return None

        win_rate = len(wins) / n
        avg_gain = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        ev_pct   = win_rate * avg_gain + (1 - win_rate) * avg_loss

        return {
            "win_rate":  round(win_rate * 100, 1),
            "n_samples": n,
            "ev_pct":    round(ev_pct, 2),
        }
    except Exception:
        return None


# ─── Thursday actual outcome ──────────────────────────────────────────────────

def get_actual_outcome(ticker: str, entry_price: float, stop_pct: float = 0.93, target_pct: float = 1.02) -> dict | None:
    try:
        df = yf.download(
            ticker,
            start=THURSDAY_START,
            end=THURSDAY_END,
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if df is None or len(df) == 0:
            return None

        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna()

        thu_open  = float(df["Open"].iloc[0])
        thu_high  = float(df["High"].iloc[0])
        thu_low   = float(df["Low"].iloc[0])
        thu_close = float(df["Close"].iloc[0])

        target_price = round(entry_price * target_pct, 2)
        stop_price   = round(entry_price * stop_pct, 2)

        # Did price reach entry (breakout trigger)?
        triggered = thu_high >= entry_price

        if not triggered:
            change_from_entry = (thu_close - entry_price) / entry_price * 100
            label = f"FLAT (no trigger) [{change_from_entry:+.1f}% vs entry]"
            pnl   = None
        elif thu_high >= target_price:
            # Win — assume stop and target are both potentially reachable;
            # if low also breaches stop, conservative label it WIN (target hit)
            pnl   = (target_price - entry_price) / entry_price * 100
            label = f"WIN (+{pnl:.1f}%)"
        elif thu_low <= stop_price:
            pnl   = (stop_price - entry_price) / entry_price * 100
            label = f"STOP ({pnl:.1f}%)"
        else:
            pnl   = (thu_close - entry_price) / entry_price * 100
            label = f"SCRATCH ({pnl:+.1f}%)"

        return {
            "thu_open":  round(thu_open, 2),
            "thu_high":  round(thu_high, 2),
            "thu_low":   round(thu_low, 2),
            "thu_close": round(thu_close, 2),
            "triggered": triggered,
            "label":     label,
            "pnl":       pnl,
        }
    except Exception:
        return None


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    universe = load_sharia_universe()
    print(f"Loaded {len(universe)} Sharia tickers", file=sys.stderr)

    # ── Step 1: score all tickers with data up to 2026-05-14 ──────────────────
    results = []
    done    = 0

    def score_worker(ticker):
        return score_stock_backtest(ticker)

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(score_worker, t): t for t in universe}
        for fut in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  Scored {done}/{len(universe)} tickers...", file=sys.stderr)
            r = fut.result()
            if r:
                results.append(r)

    print(f"Qualified tickers (passed filters): {len(results)}", file=sys.stderr)

    if not results:
        print("No tickers passed the screener filters. Exiting.")
        return

    results.sort(key=lambda x: x["score"], reverse=True)
    top5 = results[:TOP_N]

    # ── Step 2: prob estimates ────────────────────────────────────────────────
    print("Running probabilistic estimates for top 5...", file=sys.stderr)
    for r in top5:
        conditions = {
            "rsi":          r["rsi"],
            "momentum":     r["momentum"],
            "vol_ratio":    r["vol_ratio"],
            "near_breakout": r["near_breakout"],
        }
        prob = estimate_win_prob(r["ticker"], conditions)
        if prob:
            r["win_rate"]  = prob["win_rate"]
            r["n_samples"] = prob["n_samples"]
            r["ev_pct"]    = prob["ev_pct"]
        else:
            r["win_rate"]  = None
            r["n_samples"] = None
            r["ev_pct"]    = None

    # ── Step 3: actual Thursday outcome ──────────────────────────────────────
    print("Fetching Thursday actual data...", file=sys.stderr)
    for r in top5:
        outcome = get_actual_outcome(r["ticker"], r["entry_high"])
        if outcome:
            r["outcome"] = outcome
        else:
            r["outcome"] = {"label": "NO DATA", "triggered": False, "pnl": None,
                            "thu_open": None, "thu_high": None, "thu_low": None, "thu_close": None}

    # ── Step 4: Print results table ───────────────────────────────────────────
    print()
    print("=" * 90)
    print("=== BACKTEST: Thursday 2026-05-15 ===")
    print("=" * 90)

    # Header
    hdr = f"{'Rank':>4} | {'Ticker':>8} | {'Score':>6} | {'Trend':>6} | {'H.Lows':>6} | {'P(win)':>6} | {'EV%':>5} | {'Entry':>7} | {'Thu O/H/L/C':>18} | Actual Outcome"
    print(hdr)
    print("-" * 4 + "-+-" + "-" * 8 + "-+-" + "-" * 6 + "-+-" + "-" * 6 + "-+-" + "-" * 6 + "-+-" + "-" * 6 + "-+-" + "-" * 5 + "-+-" + "-" * 7 + "-+-" + "-" * 18 + "-+-" + "-" * 30)

    for i, r in enumerate(top5, 1):
        hl_icon   = "YES" if r["higher_lows"] else " NO"
        trend_str = f"{r['trend_pct']:+.2f}%"
        prob_str  = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "  N/A"
        ev_str    = f"{r['ev_pct']:+.1f}" if r["ev_pct"] is not None else "  N/A"

        o = r["outcome"]
        if o["thu_open"] is not None:
            ohlc_str = f"{o['thu_open']}/{o['thu_high']}/{o['thu_low']}/{o['thu_close']}"
        else:
            ohlc_str = "N/A"

        print(
            f"{i:>4} | {r['ticker']:>8} | {r['score']:>6.1f} | {trend_str:>6} | {hl_icon:>6} | "
            f"{prob_str:>6} | {ev_str:>5} | {r['entry_high']:>7.2f} | {ohlc_str:>18} | {o['label']}"
        )

    # ── Step 5: Summary ───────────────────────────────────────────────────────
    print()
    print("=" * 90)
    print("SUMMARY")
    print("=" * 90)

    # Rule-based: top 2 by score (already sorted)
    rule_picks  = top5[:2]
    # Prob-based: top 2 by win_rate (among those with data)
    prob_sorted = sorted([r for r in top5 if r["win_rate"] is not None], key=lambda x: x["win_rate"], reverse=True)
    prob_picks  = prob_sorted[:2]

    def outcome_summary(picks):
        wins = stops = flats = scratches = no_data = 0
        for r in picks:
            lbl = r["outcome"]["label"].upper()
            if "WIN" in lbl:      wins += 1
            elif "STOP" in lbl:   stops += 1
            elif "FLAT" in lbl or "NO TRIGGER" in lbl: flats += 1
            elif "SCRATCH" in lbl: scratches += 1
            else: no_data += 1
        return wins, stops, flats, scratches, no_data

    rw, rs, rf, rsc, rn = outcome_summary(rule_picks)
    pw, ps, pf, psc, pn = outcome_summary(prob_picks)

    print(f"\nRule-based top 2 (by Score): {[r['ticker'] for r in rule_picks]}")
    for r in rule_picks:
        print(f"  {r['ticker']:>8}  score={r['score']:.1f}  entry={r['entry_high']:.2f}  → {r['outcome']['label']}")
    print(f"  Result: {rw} WIN(s), {rs} STOP(s), {rf} FLAT(s), {rsc} SCRATCH(es)")

    print(f"\nProb-based top 2 (by P(win)): {[r['ticker'] for r in prob_picks] if prob_picks else 'N/A'}")
    for r in prob_picks:
        print(f"  {r['ticker']:>8}  P(win)={r['win_rate']}%  EV={r['ev_pct']:+.1f}%  → {r['outcome']['label']}")
    if prob_picks:
        print(f"  Result: {pw} WIN(s), {ps} STOP(s), {pf} FLAT(s), {psc} SCRATCH(es)")

    rule_tickers = {r["ticker"] for r in rule_picks}
    prob_tickers = {r["ticker"] for r in prob_picks}
    overlap      = rule_tickers & prob_tickers
    print(f"\nAgreement between rule-based and prob-based top 2: {len(overlap)}/2 same tickers")
    if overlap:
        print(f"  Overlapping picks: {sorted(overlap)}")
    else:
        print("  No overlap — the two methods diverge on which stocks to pick.")

    # Count how many of all top5 triggered
    triggered_count = sum(1 for r in top5 if r["outcome"].get("triggered"))
    print(f"\nOf the top 5 picks, {triggered_count} actually triggered (breakout happened) on Thursday.")

    print()
    print("=" * 90)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Probabilistic win-rate estimator for TASI screener picks.
"""

import numpy as np
import pandas as pd
import yfinance as yf
import pandas_ta as ta


def estimate_win_prob(ticker: str, conditions: dict) -> dict | None:
    df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
    if df is None or len(df) < 30:
        return None

    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.dropna()

    rsi_series = ta.rsi(df["Close"], length=14)
    sma10 = df["Close"].rolling(10).mean()
    vol20 = df["Volume"].rolling(20).mean()
    high20 = df["High"].rolling(20).max()

    momentum_series = (df["Close"] - sma10) / sma10 * 100
    vol_ratio_series = df["Volume"] / vol20
    near_bo_series = df["Close"] > high20.shift(1) * 0.98

    rsi_q = conditions["rsi"]
    mom_q = conditions["momentum"]
    vr_q = conditions["vol_ratio"]
    nbo_q = conditions["near_breakout"]

    wins = []
    losses = []

    for i in range(len(df) - 1):
        if pd.isna(rsi_series.iloc[i]) or pd.isna(momentum_series.iloc[i]) or pd.isna(vol_ratio_series.iloc[i]):
            continue

        rsi_i = rsi_series.iloc[i]
        mom_i = momentum_series.iloc[i]
        vr_i = vol_ratio_series.iloc[i]
        nbo_i = near_bo_series.iloc[i]

        if abs(rsi_i - rsi_q) > 10:
            continue
        if abs(mom_i - mom_q) > 2:
            continue
        if abs(vr_i - vr_q) > 0.3:
            continue
        if nbo_i != nbo_q:
            continue

        next_open = df["Open"].iloc[i + 1]
        next_high = df["High"].iloc[i + 1]
        next_low = df["Low"].iloc[i + 1]

        if next_open == 0:
            continue

        gain = (next_high - next_open) / next_open
        drawdown = (next_low - next_open) / next_open

        if gain >= 0.02 and drawdown >= -0.07:
            wins.append(gain * 100)
        else:
            loss_pct = drawdown * 100
            losses.append(loss_pct)

    n = len(wins) + len(losses)
    if n < 5:
        return None

    win_rate = len(wins) / n
    avg_gain = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    ev_pct = win_rate * avg_gain + (1 - win_rate) * avg_loss

    return {
        "win_rate": round(win_rate * 100, 1),
        "n_samples": n,
        "ev_pct": round(ev_pct, 2),
    }


def run_prob_scan(picks: list[dict]) -> list[dict]:
    for pick in picks:
        if not isinstance(pick, dict) or not pick.get("ticker"):
            continue
        conditions = {
            "rsi": pick.get("rsi"),
            "momentum": pick.get("momentum"),
            "vol_ratio": pick.get("vol_ratio"),
            "near_breakout": pick.get("near_breakout"),
        }
        # Skip if any required condition is None (can't match historicals)
        if None in conditions.values():
            pick["win_rate"] = None
            pick["n_samples"] = None
            pick["ev_pct"] = None
            continue
        try:
            prob = estimate_win_prob(pick.get("ticker"), conditions)
        except Exception:
            prob = None

        if prob:
            pick["win_rate"] = prob["win_rate"]
            pick["n_samples"] = prob["n_samples"]
            pick["ev_pct"] = prob["ev_pct"]
        else:
            pick["win_rate"] = None
            pick["n_samples"] = None
            pick["ev_pct"] = None

    return picks

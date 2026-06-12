#!/usr/bin/env python3
"""
Backtest: compare old exit rules vs new exit rules on 2026-05-14 session.
Old rules: hard stop -7%, trailing stop, hard close 14:45
New rules: + time stop (down >-1% after 30 min), VWAP re-break exit
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
import pytz

RIYADH = pytz.timezone("Asia/Riyadh")

HARD_STOP_PCT  = 0.07
TRAIL_TRIGGER  = 0.02
TRAIL_STOP_PCT = 0.03
TIME_STOP_PCT  = 0.01
TIME_STOP_MINS = 30
HARD_CLOSE     = time(14, 45)
CAPITAL        = 200  # SAR per position

def calc_vwap(df):
    df = df.copy()
    df["tp"] = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_vol = df["Volume"].cumsum()
    if cum_vol.iloc[-1] == 0:
        return None
    return float((df["tp"] * df["Volume"]).cumsum().iloc[-1] / cum_vol.iloc[-1])

def fetch_intraday(symbol, date_str="2026-05-14"):
    tk = yf.Ticker(symbol)
    df = tk.history(start=date_str, end="2026-05-15", interval="5m")
    if df.empty:
        return None
    df.index = df.index.tz_convert(RIYADH)
    # Trading hours only
    df = df.between_time("10:00", "15:00")
    return df

def simulate(symbol, entry_time_str, entry_price, signal, df):
    """
    Run both old and new exit rule simulations.
    Returns dict with exit info for each ruleset.
    """
    entry_dt = RIYADH.localize(datetime.strptime(f"2026-05-14 {entry_time_str}", "%Y-%m-%d %H:%M"))
    session  = df[df.index >= entry_dt].copy()

    results = {}
    for mode in ("old", "new"):
        peak       = entry_price
        exit_price = None
        exit_time  = None
        exit_reason= None

        for ts, row in session.iterrows():
            price = float(row["Close"])
            mins  = (ts - entry_dt).total_seconds() / 60

            if price > peak:
                peak = price

            gain_pct       = (price - entry_price) / entry_price
            peak_pct       = (peak - entry_price)  / entry_price
            drop_from_peak = (peak - price) / peak if peak else 0
            t              = ts.time()

            # Hard stop
            if gain_pct <= -HARD_STOP_PCT:
                exit_price  = price
                exit_time   = ts
                exit_reason = f"HARD STOP ({gain_pct*100:.1f}%)"
                break

            # Trailing stop
            if peak_pct >= TRAIL_TRIGGER and drop_from_peak >= TRAIL_STOP_PCT:
                exit_price  = price
                exit_time   = ts
                exit_reason = f"TRAIL STOP (peak +{peak_pct*100:.1f}%, now -{drop_from_peak*100:.1f}% from peak)"
                break

            # Hard close
            if t >= HARD_CLOSE:
                exit_price  = price
                exit_time   = ts
                exit_reason = "HARD CLOSE 14:45"
                break

            # ── New rules only ────────────────────────────────────────────────
            if mode == "new":
                # Time stop: down >-1% after 30 min
                if mins >= TIME_STOP_MINS and gain_pct <= -TIME_STOP_PCT:
                    exit_price  = price
                    exit_time   = ts
                    exit_reason = f"TIME STOP (held {int(mins)}min, {gain_pct*100:.1f}%)"
                    break

                # VWAP re-break: price back below VWAP and negative
                if signal == "vwap_reclaim" and gain_pct < 0:
                    vwap = calc_vwap(df[df.index <= ts])
                    if vwap and price < vwap:
                        exit_price  = price
                        exit_time   = ts
                        exit_reason = f"VWAP RE-BREAK (price {price:.2f} < VWAP {vwap:.2f})"
                        break

        if exit_price is None:
            exit_price  = float(session["Close"].iloc[-1])
            exit_time   = session.index[-1]
            exit_reason = "SESSION END"

        qty     = int(CAPITAL / entry_price)
        pnl_pct = (exit_price - entry_price) / entry_price * 100
        pnl_sar = (exit_price - entry_price) * qty

        results[mode] = {
            "exit_price":  exit_price,
            "exit_time":   exit_time.strftime("%H:%M"),
            "exit_reason": exit_reason,
            "qty":         qty,
            "pnl_pct":     pnl_pct,
            "pnl_sar":     pnl_sar,
        }

    return results


def main():
    trades = [
        {"symbol": "2190.SR", "entry_time": "10:00", "entry_price": 36.46, "signal": "breakout"},
        {"symbol": "2130.SR", "entry_time": "10:25", "entry_price": 12.80, "signal": "vwap_reclaim"},
    ]

    print("\n" + "="*70)
    print("  BACKTEST — 2026-05-14 — Old vs New Exit Rules")
    print("="*70)

    total_old = 0.0
    total_new = 0.0

    for t in trades:
        sym = t["symbol"]
        print(f"\n{'─'*70}")
        print(f"  {sym}  |  Entry {t['entry_time']} @ {t['entry_price']:.2f}  |  Signal: {t['signal']}")
        print(f"{'─'*70}")

        df = fetch_intraday(sym)
        if df is None or df.empty:
            print(f"  No data for {sym}")
            continue

        res = simulate(sym, t["entry_time"], t["entry_price"], t["signal"], df)

        for mode in ("old", "new"):
            r = res[mode]
            sign = "+" if r["pnl_sar"] >= 0 else ""
            print(f"  [{mode.upper()}]  Exit {r['exit_time']} @ {r['exit_price']:.2f}"
                  f"  →  {sign}{r['pnl_pct']:.2f}%  /  {sign}{r['pnl_sar']:.2f} SAR"
                  f"  ({r['exit_reason']})")

        total_old += res["old"]["pnl_sar"]
        total_new += res["new"]["pnl_sar"]

    print(f"\n{'='*70}")
    print(f"  DAY TOTAL (400 SAR deployed)")
    sign_o = "+" if total_old >= 0 else ""
    sign_n = "+" if total_new >= 0 else ""
    print(f"  [OLD]  {sign_o}{total_old:.2f} SAR  ({sign_o}{total_old/400*100:.2f}%)")
    print(f"  [NEW]  {sign_n}{total_new:.2f} SAR  ({sign_n}{total_new/400*100:.2f}%)")
    diff = total_new - total_old
    print(f"  Improvement: {'+' if diff >= 0 else ''}{diff:.2f} SAR")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()

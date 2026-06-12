#!/usr/bin/env python3
"""
Backtest: Live feed vs 15-min delayed feed — impact on signal timing and P&L.

Mechanics:
  LIVE    — signal checked at time T using data up to T; execute at T price
  DELAYED — signal checked at time T using data up to T-15min; execute at T price
            (the decision is late, but the actual order hits current market)

This isolates the pure cost of data lag: late entries, late exits.
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, time, timedelta
import pytz

RIYADH = pytz.timezone("Asia/Riyadh")

HARD_STOP_PCT  = 0.07
TRAIL_TRIGGER  = 0.02
TRAIL_STOP_PCT = 0.03
TIME_STOP_PCT  = 0.01
TIME_STOP_MINS = 30
HARD_CLOSE     = time(14, 45)
ENTRY_CUTOFF   = time(13, 30)
DELAY_BARS     = 3   # 3 × 5min = 15 min delay
PER_POSITION   = 200
TOTAL_CAPITAL  = 400

PICKS = [
    {"symbol": "2190.SR", "entry_high": 33.35, "signal_type": "breakout"},
    {"symbol": "2130.SR", "entry_high": 13.32, "signal_type": "vwap_reclaim"},
    {"symbol": "3060.SR", "entry_high": 16.03, "signal_type": "vwap_reclaim"},
]


def fetch(symbol):
    df = yf.Ticker(symbol).history(start="2026-05-14", end="2026-05-15", interval="5m")
    if df.empty:
        return None
    df.index = df.index.tz_convert(RIYADH)
    df = df.between_time("10:00", "15:00")
    return df


def calc_vwap(df):
    df = df.copy()
    df["tp"] = (df["High"] + df["Low"] + df["Close"]) / 3
    cum = df["Volume"].cumsum()
    return float((df["tp"] * df["Volume"]).cumsum().iloc[-1] / cum.iloc[-1]) if cum.iloc[-1] else None


def check_vwap_reclaim(df):
    if len(df) < 2:
        return False
    vwap = calc_vwap(df)
    prev = float(df["Close"].iloc[-2])
    curr = float(df["Close"].iloc[-1])
    avol = float(df["Volume"].mean())
    cvol = float(df["Volume"].iloc[-1])
    return vwap and prev < vwap < curr and cvol > avol * 0.8


def check_breakout(df):
    if len(df) < 6:
        return False
    prior_high = float(df["High"].iloc[:-1].max())
    curr       = float(df["Close"].iloc[-1])
    avol       = float(df["Volume"].mean())
    cvol       = float(df["Volume"].iloc[-1])
    return curr > prior_high and cvol > avol * 1.5


def run_session(dfs, mode="live"):
    """
    Simulate a full session.
    mode: "live" or "delayed"
    Returns list of completed trades.
    """
    delay = DELAY_BARS if mode == "delayed" else 0
    trades    = []
    positions = {}   # sym → state
    traded    = set()
    free_cap  = TOTAL_CAPITAL

    all_times = sorted(set().union(*[set(df.index) for df in dfs.values() if df is not None]))

    for i, ts in enumerate(all_times):
        t = ts.time()

        # ── Update open positions ─────────────────────────────────────────────
        for sym in list(positions.keys()):
            pos = positions[sym]
            if pos.get("closed"):
                continue
            df = dfs.get(sym)
            if df is None or ts not in df.index:
                continue

            # Actual live execution price (always current — you place order at market)
            live_price = float(df.loc[ts, "Close"])

            # Observed price (what the feed shows you — delayed or live)
            obs_idx = max(0, i - delay)
            obs_ts  = all_times[obs_idx]
            obs_price = float(df.loc[obs_ts, "Close"]) if obs_ts in df.index else live_price

            ep     = pos["entry_price"]
            peak   = pos.get("peak", ep)
            et     = pos["entry_dt"]
            signal = pos["signal"]
            mins   = (ts - et).total_seconds() / 60

            # Update peak using observed price
            if obs_price > peak:
                pos["peak"] = obs_price
                peak = obs_price

            gain_obs   = (obs_price - ep) / ep
            peak_gain  = (peak - ep) / ep
            drop_peak  = (peak - obs_price) / peak if peak else 0

            exit_live = None; exit_r = None

            # All exits checked on OBSERVED price, executed at LIVE price
            if gain_obs <= -HARD_STOP_PCT:
                exit_live, exit_r = live_price, f"HARD STOP (obs {obs_price:.2f})"
            elif peak_gain >= TRAIL_TRIGGER and drop_peak >= TRAIL_STOP_PCT:
                exit_live, exit_r = live_price, f"TRAIL STOP"
            elif t >= HARD_CLOSE:
                exit_live, exit_r = live_price, "HARD CLOSE"
            elif mins >= TIME_STOP_MINS and gain_obs <= -TIME_STOP_PCT:
                exit_live, exit_r = live_price, f"TIME STOP ({int(mins)}min)"
            elif signal == "vwap_reclaim" and gain_obs < 0:
                df_obs = dfs[sym][dfs[sym].index <= obs_ts]
                vwap   = calc_vwap(df_obs)
                if vwap and obs_price < vwap:
                    exit_live, exit_r = live_price, f"VWAP RE-BREAK (obs {obs_price:.2f} < VWAP {vwap:.2f})"

            if exit_live is not None:
                qty = pos["qty"]
                pnl = (exit_live - ep) * qty
                free_cap += pos["capital"]
                trades.append({
                    "sym":          sym,
                    "entry_time":   et.strftime("%H:%M"),
                    "entry_price":  ep,
                    "signal":       signal,
                    "exit_time":    ts.strftime("%H:%M"),
                    "exit_price":   exit_live,
                    "exit_reason":  exit_r,
                    "pnl_pct":      (exit_live - ep) / ep * 100,
                    "pnl_sar":      pnl,
                    "qty":          qty,
                    "redeployment": pos.get("redeployment", False),
                })
                pos["closed"] = True

        # ── Check entry signals ───────────────────────────────────────────────
        if free_cap < PER_POSITION or len([p for p in positions.values() if not p.get("closed")]) >= 2:
            continue
        if t >= ENTRY_CUTOFF:
            continue

        # Use observed (possibly delayed) data for signal check
        obs_idx = max(0, i - delay)
        obs_ts  = all_times[obs_idx]

        for pick in PICKS:
            sym = pick["symbol"]
            if sym in traded:
                continue
            df = dfs.get(sym)
            if df is None or ts not in df.index:
                continue

            # Signal is based on observed data
            df_obs = df[df.index <= obs_ts]
            if len(df_obs) < 2:
                continue

            # Execution (entry) price is always the current live price
            live_entry = float(df.loc[ts, "Close"])
            obs_entry  = float(df_obs["Close"].iloc[-1])

            # Gap-up guard on observed price
            if obs_entry > pick["entry_high"] * 1.01:
                continue

            signal = None
            if pick["signal_type"] == "vwap_reclaim" and check_vwap_reclaim(df_obs):
                signal = "vwap_reclaim"
            elif pick["signal_type"] == "breakout" and check_breakout(df_obs):
                signal = "breakout"

            if signal:
                qty = int(PER_POSITION / live_entry)
                positions[sym] = {
                    "entry_price": live_entry,
                    "entry_dt":    ts,
                    "signal":      signal,
                    "capital":     PER_POSITION,
                    "qty":         qty,
                    "peak":        live_entry,
                    "redeployment": len(traded) >= 2,
                }
                traded.add(sym)
                free_cap -= PER_POSITION
                break

    # Force-close remaining open positions
    for sym, pos in positions.items():
        if pos.get("closed"):
            continue
        df = dfs.get(sym)
        if df is None:
            continue
        exit_p = float(df["Close"].iloc[-1])
        ep     = pos["entry_price"]
        qty    = pos["qty"]
        pnl    = (exit_p - ep) * qty
        trades.append({
            "sym":          sym,
            "entry_time":   pos["entry_dt"].strftime("%H:%M"),
            "entry_price":  ep,
            "signal":       pos["signal"],
            "exit_time":    df.index[-1].strftime("%H:%M"),
            "exit_price":   exit_p,
            "exit_reason":  "SESSION END",
            "pnl_pct":      (exit_p - ep) / ep * 100,
            "pnl_sar":      pnl,
            "qty":          qty,
            "redeployment": pos.get("redeployment", False),
        })

    return trades


def print_trades(trades, mode):
    total = sum(t["pnl_sar"] for t in trades)
    print(f"\n  {'─'*64}")
    print(f"  {mode.upper()} FEED")
    print(f"  {'─'*64}")
    for t in trades:
        redep = " [REDEPLOY]" if t["redeployment"] else ""
        s = "+" if t["pnl_sar"] >= 0 else ""
        print(f"  {t['sym']}{redep}  ({t['signal']})")
        print(f"    Entry {t['entry_time']} @ {t['entry_price']:.2f}  →  Exit {t['exit_time']} @ {t['exit_price']:.2f}")
        print(f"    {s}{t['pnl_pct']:.2f}%  /  {s}{t['pnl_sar']:.2f} SAR  |  {t['exit_reason']}")
    s = "+" if total >= 0 else ""
    print(f"  {'─'*64}")
    print(f"  TOTAL: {s}{total:.2f} SAR  ({s}{total/TOTAL_CAPITAL*100:.2f}%)")


def main():
    print("\nFetching data...")
    syms = list({p["symbol"] for p in PICKS})
    dfs  = {}
    for sym in syms:
        df = fetch(sym)
        dfs[sym] = df
        print(f"  {sym}: {len(df) if df is not None else 'NO DATA'} candles")

    print("\n" + "="*68)
    print("  LIVE vs 15-MIN DELAYED — 2026-05-14")
    print("="*68)

    live_trades    = run_session(dfs, mode="live")
    delayed_trades = run_session(dfs, mode="delayed")

    print_trades(live_trades,    "LIVE")
    print_trades(delayed_trades, "15-MIN DELAY")

    live_pnl    = sum(t["pnl_sar"] for t in live_trades)
    delayed_pnl = sum(t["pnl_sar"] for t in delayed_trades)
    diff        = live_pnl - delayed_pnl

    print(f"\n  {'='*64}")
    print(f"  DELAY COST:  {'+' if diff >= 0 else ''}{diff:.2f} SAR  ({'+' if diff >= 0 else ''}{diff/TOTAL_CAPITAL*100:.2f}%)")
    print(f"  {'='*64}\n")

    # Entry timing comparison
    print("  ENTRY TIMING COMPARISON")
    print(f"  {'─'*64}")
    for lt in live_trades:
        dt = next((t for t in delayed_trades if t["sym"] == lt["sym"]), None)
        if dt:
            print(f"  {lt['sym']}  Live entry {lt['entry_time']} @ {lt['entry_price']:.2f}"
                  f"  vs  Delayed entry {dt['entry_time']} @ {dt['entry_price']:.2f}"
                  f"  (diff: {dt['entry_price'] - lt['entry_price']:+.2f} SAR/share)")
            print(f"         Live exit  {lt['exit_time']} @ {lt['exit_price']:.2f}"
                  f"  vs  Delayed exit  {dt['exit_time']} @ {dt['exit_price']:.2f}"
                  f"  (diff: {dt['exit_price'] - lt['exit_price']:+.2f} SAR/share)")
    print()


if __name__ == "__main__":
    main()

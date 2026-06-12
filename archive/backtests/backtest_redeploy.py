#!/usr/bin/env python3
"""
Backtest with capital redeployment.
When a position exits early, freed capital is redeployed into the next
qualifying pick (VWAP reclaim or breakout signal) until 13:30 cutoff.

Compares:
  OLD — hold to 14:45 hard close, no redeployment
  NEW — early exits + redeploy freed capital into next signal
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, time
import pytz

RIYADH = pytz.timezone("Asia/Riyadh")

HARD_STOP_PCT  = 0.07
TRAIL_TRIGGER  = 0.02
TRAIL_STOP_PCT = 0.03
TIME_STOP_PCT  = 0.01
TIME_STOP_MINS = 30
ENTRY_CUTOFF   = time(13, 30)
HARD_CLOSE     = time(14, 45)
TOTAL_CAPITAL  = 400    # SAR total to deploy (2 positions × 200)
PER_POSITION   = 200    # SAR per position
MAX_POSITIONS  = 2

# Thursday screener top picks (in rank order — use in priority)
PICKS = [
    {"symbol": "2190.SR", "entry_low": 32.93, "entry_high": 33.35, "stop_loss": 33.48},
    {"symbol": "2130.SR", "entry_low": 12.80, "entry_high": 13.32, "stop_loss": 12.00},
    {"symbol": "2222.SR", "entry_low": 27.63, "entry_high": 27.97, "stop_loss": 25.97},
    {"symbol": "2010.SR", "entry_low": 60.26, "entry_high": 61.46, "stop_loss": 57.24},
    {"symbol": "3060.SR", "entry_low": 15.75, "entry_high": 16.03, "stop_loss": 14.77},
]

# Thursday confirmed entries (first two were auto-entered at open/early signal)
FORCED_ENTRIES = {
    "2190.SR": {"time": "10:00", "price": 36.46, "signal": "breakout"},
    "2130.SR": {"time": "10:25", "price": 12.80, "signal": "vwap_reclaim"},
}


def fetch(symbol):
    df = yf.Ticker(symbol).history(start="2026-05-14", end="2026-05-15", interval="5m")
    if df.empty:
        return None
    df.index = df.index.tz_convert(RIYADH)
    df = df.between_time("10:00", "15:00")
    return df


def calc_vwap(df_so_far):
    df = df_so_far.copy()
    df["tp"] = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_vol = df["Volume"].cumsum()
    return float((df["tp"] * df["Volume"]).cumsum().iloc[-1] / cum_vol.iloc[-1]) if cum_vol.iloc[-1] else None


def check_vwap_reclaim(df_so_far):
    if len(df_so_far) < 2:
        return False
    vwap  = calc_vwap(df_so_far)
    prev  = float(df_so_far["Close"].iloc[-2])
    curr  = float(df_so_far["Close"].iloc[-1])
    avol  = float(df_so_far["Volume"].mean())
    cvol  = float(df_so_far["Volume"].iloc[-1])
    return vwap and prev < vwap < curr and cvol > avol * 0.8


def check_breakout(df_so_far):
    if len(df_so_far) < 6:
        return False
    prior_high = float(df_so_far["High"].iloc[:-1].max())
    curr       = float(df_so_far["Close"].iloc[-1])
    avol       = float(df_so_far["Volume"].mean())
    cvol       = float(df_so_far["Volume"].iloc[-1])
    return curr > prior_high and cvol > avol * 1.5


def simulate_old(dfs):
    """Old rules: two fixed positions held to hard close."""
    log = []
    total_pnl = 0.0

    for sym, entry in FORCED_ENTRIES.items():
        df   = dfs.get(sym)
        if df is None:
            continue
        ep   = entry["price"]
        qty  = int(PER_POSITION / ep)
        et_s = f"2026-05-14 {entry['time']}:00"
        entry_dt = RIYADH.localize(datetime.strptime(et_s, "%Y-%m-%d %H:%M:%S"))
        session  = df[df.index >= entry_dt]

        peak = ep
        exit_p = None; exit_t = None; exit_r = None
        for ts, row in session.iterrows():
            p = float(row["Close"])
            if p > peak: peak = p
            gain       = (p - ep) / ep
            peak_gain  = (peak - ep) / ep
            drop_peak  = (peak - p) / peak if peak else 0
            if gain <= -HARD_STOP_PCT:
                exit_p, exit_t, exit_r = p, ts, "HARD STOP"
                break
            if peak_gain >= TRAIL_TRIGGER and drop_peak >= TRAIL_STOP_PCT:
                exit_p, exit_t, exit_r = p, ts, "TRAIL STOP"
                break
            if ts.time() >= HARD_CLOSE:
                exit_p, exit_t, exit_r = p, ts, "HARD CLOSE"
                break
        if exit_p is None:
            exit_p = float(session["Close"].iloc[-1])
            exit_t = session.index[-1]
            exit_r = "SESSION END"

        pnl = (exit_p - ep) * qty
        total_pnl += pnl
        log.append({
            "sym": sym, "entry_time": entry["time"], "entry_price": ep,
            "qty": qty, "signal": entry["signal"],
            "exit_time": exit_t.strftime("%H:%M"), "exit_price": exit_p,
            "exit_reason": exit_r,
            "pnl_pct": (exit_p - ep) / ep * 100, "pnl_sar": pnl,
            "redeployment": False,
        })
    return log, total_pnl


def simulate_new(dfs):
    """New rules: early exits + redeploy freed capital into next signal."""
    log         = []
    total_pnl   = 0.0
    free_cap    = 0.0
    open_count  = 0
    traded_syms = set()

    # Positions queue: {sym: state}
    positions = {}

    # Align all dataframes to a common timeline
    all_times = sorted(set().union(*[set(df.index) for df in dfs.values() if df is not None]))

    for ts in all_times:
        t = ts.time()

        # ── Update / exit open positions ──────────────────────────────────────
        for sym in list(positions.keys()):
            pos = positions[sym]
            if pos.get("closed"):
                continue
            df = dfs.get(sym)
            if df is None or ts not in df.index:
                continue

            p       = float(df.loc[ts, "Close"])
            ep      = pos["entry_price"]
            peak    = pos.get("peak", ep)
            et      = pos["entry_dt"]
            mins    = (ts - et).total_seconds() / 60
            signal  = pos["signal"]

            if p > peak:
                pos["peak"] = p
                peak = p

            gain       = (p - ep) / ep
            peak_gain  = (peak - ep) / ep
            drop_peak  = (peak - p) / peak if peak else 0

            exit_p = None; exit_r = None

            if gain <= -HARD_STOP_PCT:
                exit_p, exit_r = p, "HARD STOP"
            elif peak_gain >= TRAIL_TRIGGER and drop_peak >= TRAIL_STOP_PCT:
                exit_p, exit_r = p, "TRAIL STOP"
            elif t >= HARD_CLOSE:
                exit_p, exit_r = p, "HARD CLOSE"
            elif mins >= TIME_STOP_MINS and gain <= -TIME_STOP_PCT:
                exit_p, exit_r = p, f"TIME STOP ({int(mins)}min)"
            elif signal == "vwap_reclaim" and gain < 0:
                df_so_far = dfs[sym][dfs[sym].index <= ts]
                vwap = calc_vwap(df_so_far)
                if vwap and p < vwap:
                    exit_p, exit_r = p, f"VWAP RE-BREAK"

            if exit_p is not None:
                qty = pos["qty"]
                pnl = (exit_p - ep) * qty
                total_pnl += pnl
                free_cap  += pos["capital"]
                open_count -= 1
                log.append({
                    "sym": sym,
                    "entry_time":  et.strftime("%H:%M"),
                    "entry_price": ep,
                    "qty": qty,
                    "signal": signal,
                    "exit_time":   ts.strftime("%H:%M"),
                    "exit_price":  exit_p,
                    "exit_reason": exit_r,
                    "pnl_pct": gain * 100,
                    "pnl_sar": pnl,
                    "redeployment": pos.get("redeployment", False),
                })
                pos["closed"] = True

        # ── Check entries for forced first trades ─────────────────────────────
        for sym, entry in FORCED_ENTRIES.items():
            if sym in traded_syms:
                continue
            entry_dt_s = f"2026-05-14 {entry['time']}:00"
            entry_dt   = RIYADH.localize(datetime.strptime(entry_dt_s, "%Y-%m-%d %H:%M:%S"))
            if ts == entry_dt or (ts > entry_dt and sym not in positions):
                # Check we have a slot and capital
                if open_count < MAX_POSITIONS and free_cap >= PER_POSITION or (sym not in positions and open_count < 2):
                    df = dfs.get(sym)
                    if df is None or ts not in df.index:
                        continue
                    ep  = entry["price"]
                    qty = int(PER_POSITION / ep)
                    positions[sym] = {
                        "entry_price": ep, "entry_dt": ts,
                        "signal": entry["signal"], "capital": PER_POSITION,
                        "qty": qty, "peak": ep, "redeployment": False,
                    }
                    traded_syms.add(sym)
                    open_count += 1
                    if sym not in FORCED_ENTRIES or ts == entry_dt:
                        pass  # capital is pre-allocated

        # ── Redeploy freed capital into next pick ─────────────────────────────
        if free_cap >= PER_POSITION and open_count < MAX_POSITIONS and t < ENTRY_CUTOFF:
            for pick in PICKS:
                sym = pick["symbol"]
                if sym in traded_syms:
                    continue
                df = dfs.get(sym)
                if df is None or ts not in df.index:
                    continue

                df_so_far = df[df.index <= ts]
                if len(df_so_far) < 2:
                    continue

                price      = float(df.loc[ts, "Close"])
                entry_high = pick["entry_high"]

                # Gap-up guard
                if price > entry_high * 1.01:
                    continue

                signal = None
                if check_vwap_reclaim(df_so_far):
                    signal = "vwap_reclaim"
                elif check_breakout(df_so_far):
                    signal = "breakout"

                if signal:
                    qty = int(PER_POSITION / price)
                    positions[sym] = {
                        "entry_price": price, "entry_dt": ts,
                        "signal": signal, "capital": PER_POSITION,
                        "qty": qty, "peak": price, "redeployment": True,
                    }
                    traded_syms.add(sym)
                    open_count += 1
                    free_cap   -= PER_POSITION
                    print(f"  REDEPLOY → {sym} @ {price:.2f} ({signal}) at {ts.strftime('%H:%M')}")
                    break

    # Force-close any still-open positions at session end
    for sym, pos in positions.items():
        if pos.get("closed"):
            continue
        df = dfs.get(sym)
        if df is None:
            continue
        ep  = pos["entry_price"]
        qty = pos["qty"]
        exit_p = float(df["Close"].iloc[-1])
        pnl    = (exit_p - ep) * qty
        total_pnl += pnl
        log.append({
            "sym": sym,
            "entry_time":  pos["entry_dt"].strftime("%H:%M"),
            "entry_price": ep,
            "qty": qty,
            "signal": pos["signal"],
            "exit_time":   df.index[-1].strftime("%H:%M"),
            "exit_price":  exit_p,
            "exit_reason": "SESSION END",
            "pnl_pct": (exit_p - ep) / ep * 100,
            "pnl_sar": pnl,
            "redeployment": pos.get("redeployment", False),
        })

    return log, total_pnl


def print_log(log, label):
    print(f"\n{'─'*68}")
    print(f"  {label}")
    print(f"{'─'*68}")
    for t in log:
        redep = " [REDEPLOY]" if t["redeployment"] else ""
        sign  = "+" if t["pnl_sar"] >= 0 else ""
        print(f"  {t['sym']}{redep}")
        print(f"    Entry {t['entry_time']} @ {t['entry_price']:.2f}  ({t['signal']})")
        print(f"    Exit  {t['exit_time']} @ {t['exit_price']:.2f}  → {sign}{t['pnl_pct']:.2f}% / {sign}{t['pnl_sar']:.2f} SAR")
        print(f"    Reason: {t['exit_reason']}")


def main():
    print("\nFetching intraday data...")
    all_syms = list({p["symbol"] for p in PICKS} | set(FORCED_ENTRIES.keys()))
    dfs = {}
    for sym in all_syms:
        df = fetch(sym)
        dfs[sym] = df
        status = f"{len(df)} candles" if df is not None and not df.empty else "NO DATA"
        print(f"  {sym}: {status}")

    print("\n" + "="*68)
    print("  BACKTEST — 2026-05-14 — With Capital Redeployment")
    print("="*68)

    log_old, pnl_old = simulate_old(dfs)
    print_log(log_old, "OLD — hold to hard close, no redeployment")

    log_new, pnl_new = simulate_new(dfs)
    print_log(log_new, "NEW — early exits + redeploy capital")

    print(f"\n{'='*68}")
    print(f"  SUMMARY")
    print(f"{'─'*68}")
    so = "+" if pnl_old >= 0 else ""
    sn = "+" if pnl_new >= 0 else ""
    print(f"  OLD  {so}{pnl_old:.2f} SAR  ({so}{pnl_old/TOTAL_CAPITAL*100:.2f}%)")
    print(f"  NEW  {sn}{pnl_new:.2f} SAR  ({sn}{pnl_new/TOTAL_CAPITAL*100:.2f}%)")
    diff = pnl_new - pnl_old
    print(f"  Improvement: {'+' if diff >= 0 else ''}{diff:.2f} SAR")
    print("="*68 + "\n")


if __name__ == "__main__":
    main()

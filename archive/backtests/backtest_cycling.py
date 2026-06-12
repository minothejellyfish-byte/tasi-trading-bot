#!/usr/bin/env python3
"""
TASI Cycling Backtest: 2026-04-19 to 2026-05-15
Simulates multiple round trips per pick per day using 5-minute intraday data.

Cycling rules:
- Entry when 5m candle Close >= entry_high
- WIN  (+2%): any candle High >= entry_price * 1.02 → take profit, reset
- STOP (-7%): any candle Low  <= entry_price * 0.93 → stop out, NO re-entry today
- After WIN: wait 1 candle, re-enter at next candle Open if time < 14:30 Riyadh
- Hard close: 14:45 Riyadh — exit at candle Close
- Max 4 cycles per stock per day

Capital: 40% per pick, 2 picks/day, cycles compound within the day.

Strategy A — Rule-based, top 2 by score
Strategy B — Prob veto: top 3 rule → drop P(win) < 20% → top 2 remaining
Strategy C — Tuned prob: top 2 by P(win), 6mo lookback, no near_breakout filter
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
from zoneinfo import ZoneInfo

# ─── Config ───────────────────────────────────────────────────────────────────

SHARIA_FILE  = "/home/mino/tasi-exec/sharia_list.json"
OUTPUT_FILE  = "/home/mino/tasi-exec/backtest_cycling_output.txt"
START_DATE   = date(2026, 4, 19)
END_DATE     = date(2026, 5, 15)

MIN_AVG_VOLUME = 500_000
MIN_PRICE      = 10.0
MAX_PRICE      = 500.0
TOP_N_RULE     = 5    # score top 5 to feed prob models
TOP_N_PICKS    = 2    # final picks per strategy per day
MIN_DAYS       = 15

WIN_PCT    =  2.0   # % per cycle win
STOP_PCT   = -7.0   # % stop loss

START_CAPITAL  = 1_000.0
POSITION_PCT   = 0.40   # 40% per pick

RIYADH_TZ = ZoneInfo("Asia/Riyadh")
# Market hours in Riyadh time
MARKET_OPEN_H    = 10
MARKET_OPEN_M    = 0
MARKET_CLOSE_H   = 14
MARKET_CLOSE_M   = 45   # hard close at 14:45 Riyadh
REENTRY_CUTOFF_H = 14
REENTRY_CUTOFF_M = 30   # no new re-entry after 14:30 Riyadh
MAX_CYCLES       = 4    # prevent runaway

# No-cycling baseline from backtest_capital.py
NOCYCLE_A_PCT = 5.4
NOCYCLE_B_PCT = 8.0
NOCYCLE_C_PCT = 3.4

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


def download_5m_bulk(ticker: str) -> pd.DataFrame | None:
    """Download ~60 days of 5-minute intraday data for a ticker.
    Returns dataframe with Riyadh-tz timestamps, or None on failure."""
    try:
        df = yf.download(
            ticker,
            period="60d",
            interval="5m",
            progress=False,
            auto_adjust=True,
        )
        if df is None or len(df) == 0:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna(how="all")
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(RIYADH_TZ)
        return df
    except Exception:
        return None


def get_5m_for_day(df5m_bulk: pd.DataFrame | None, trade_date: date) -> pd.DataFrame | None:
    """Slice bulk 5m data to a specific trading day."""
    if df5m_bulk is None or len(df5m_bulk) == 0:
        return None
    date_str = str(trade_date)
    mask = df5m_bulk.index.date == trade_date
    day_df = df5m_bulk[mask]
    if len(day_df) == 0:
        return None
    return day_df

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
    """Strategy B: standard 2y lookback WITH near_breakout condition."""
    try:
        if df2y is None or len(df2y) < 30:
            return None

        rsi_series       = ta.rsi(df2y["Close"], length=14)
        sma10            = df2y["Close"].rolling(10).mean()
        vol20            = df2y["Volume"].rolling(20).mean()
        high20           = df2y["High"].rolling(20).max()
        momentum_series  = (df2y["Close"] - sma10) / sma10 * 100
        vol_ratio_series = df2y["Volume"] / vol20
        near_bo_series   = df2y["Close"] > high20.shift(1) * 0.98

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
            if abs(rsi_i - rsi_q) > 10: continue
            if abs(mom_i - mom_q) > 2:  continue
            if abs(vr_i  - vr_q)  > 0.3: continue
            if nbo_i != nbo_q:          continue
            next_open = float(df2y["Open"].iloc[i + 1])
            next_high = float(df2y["High"].iloc[i + 1])
            next_low  = float(df2y["Low"].iloc[i + 1])
            if next_open == 0: continue
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
    """Strategy C: 6-month lookback, NO near_breakout condition."""
    try:
        if df2y is None or len(df2y) < 30:
            return None

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

        wins, losses = [], []
        for i in range(len(df6mo) - 1):
            if (pd.isna(rsi_series.iloc[i]) or
                pd.isna(momentum_series.iloc[i]) or
                pd.isna(vol_ratio_series.iloc[i])):
                continue
            rsi_i = float(rsi_series.iloc[i])
            mom_i = float(momentum_series.iloc[i])
            vr_i  = float(vol_ratio_series.iloc[i])
            if abs(rsi_i - rsi_q) > 10: continue
            if abs(mom_i - mom_q) > 2:  continue
            if abs(vr_i  - vr_q)  > 0.3: continue
            next_open = float(df6mo["Open"].iloc[i + 1])
            next_high = float(df6mo["High"].iloc[i + 1])
            next_low  = float(df6mo["Low"].iloc[i + 1])
            if next_open == 0: continue
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

# ─── Cycling simulation ────────────────────────────────────────────────────────

def simulate_cycling(df5m_day: pd.DataFrame | None, entry_high: float,
                     daily_high: float, daily_low: float, daily_close: float) -> dict:
    """
    Walk 5-minute candles for one trading day.
    State machine per stock per day:

      WAITING  — watching for candle where Close >= entry_high to enter
      IN_TRADE — track win/stop/EOD on subsequent candles

    Rules:
      Entry: candle Close >= entry_high → enter at entry_high price
      WIN  (+2%): any candle High >= pos_entry * 1.02 → exit at target
                  if cycles < MAX_CYCLES and time < 14:30 Riyadh:
                    wait 1 candle, re-enter at next candle Open
      STOP (-7%): any candle Low <= pos_entry * 0.93 → exit stop, NO re-entry today
      EOD (14:45): if still in trade at hard-close candle → exit at Close
      When both win+stop in same candle → stop takes priority (worst-case)

    Capital per cycle compounds: actual position dollar value passed in from caller.
    This function returns P&L as a list of per-cycle pct returns so caller can compound.
    """
    market_open  = MARKET_OPEN_H  * 60 + MARKET_OPEN_M   # 600 mins from midnight
    market_close = MARKET_CLOSE_H * 60 + MARKET_CLOSE_M  # 885 mins
    reentry_cut  = REENTRY_CUTOFF_H * 60 + REENTRY_CUTOFF_M  # 870 mins (14:30)

    if df5m_day is None or len(df5m_day) == 0:
        return _fallback_daily(entry_high, daily_high, daily_low, daily_close)

    # Build ordered candle list within market hours
    candles = []
    for ts, row in df5m_day.iterrows():
        mins = ts.hour * 60 + ts.minute
        if mins < market_open or mins > market_close:
            continue
        vol = float(row.get("Volume", 1))
        if vol == 0:
            continue
        candles.append({
            "ts":    ts,
            "open":  float(row["Open"]),
            "high":  float(row["High"]),
            "low":   float(row["Low"]),
            "close": float(row["Close"]),
            "mins":  mins,
        })

    if not candles:
        return _fallback_daily(entry_high, daily_high, daily_low, daily_close)

    cycle_pnls: list[float] = []   # per-cycle % returns (can compound)
    n_wins  = 0
    n_stops = 0
    n_eod   = 0

    entry_trigger = entry_high   # price level needed for entry
    state         = "WAITING"
    pos_entry     = 0.0
    i             = 0

    while i < len(candles):
        if len(cycle_pnls) >= MAX_CYCLES:
            break

        c    = candles[i]
        mins = c["mins"]

        if state == "WAITING":
            # Entry condition: candle Close crosses entry_trigger
            if c["close"] >= entry_trigger:
                pos_entry = entry_trigger
                state = "IN_TRADE"
                # Don't advance i — evaluate this same candle as first in-trade candle
                # (entry is at close of this candle, so evaluate next candle for exit)
                i += 1
                continue
            else:
                i += 1
                continue

        elif state == "IN_TRADE":
            win_thresh  = pos_entry * 1.02
            stop_thresh = pos_entry * 0.93

            hit_stop = c["low"]  <= stop_thresh
            hit_win  = c["high"] >= win_thresh

            # Hard close: at or past 14:45
            if mins >= market_close:
                pnl = (c["close"] - pos_entry) / pos_entry * 100
                cycle_pnls.append(pnl)
                n_eod += 1
                state  = "WAITING"
                break

            if hit_stop and hit_win:
                # Worst-case: stop hits first
                cycle_pnls.append(STOP_PCT)
                n_stops += 1
                break  # no re-entry after stop

            elif hit_stop:
                cycle_pnls.append(STOP_PCT)
                n_stops += 1
                break  # no re-entry after stop

            elif hit_win:
                cycle_pnls.append(WIN_PCT)
                n_wins += 1
                # Re-entry: only if under cycle cap and time permits
                if len(cycle_pnls) < MAX_CYCLES and mins <= reentry_cut:
                    # Wait 1 candle, then re-enter at next candle Open
                    skip_idx = i + 1  # skip this candle (1 candle wait)
                    reentry_idx = skip_idx + 1
                    if reentry_idx < len(candles):
                        next_c = candles[reentry_idx]
                        if next_c["mins"] <= reentry_cut:
                            entry_trigger = next_c["open"]
                            pos_entry     = next_c["open"]
                            state         = "IN_TRADE"
                            i = reentry_idx + 1
                            continue
                # Cannot re-enter
                break

            else:
                # In-trade, no win/stop yet
                # Check if this is the last candle
                if i == len(candles) - 1:
                    pnl = (c["close"] - pos_entry) / pos_entry * 100
                    cycle_pnls.append(pnl)
                    n_eod += 1
                    break
                i += 1
                continue

        i += 1

    if not cycle_pnls:
        return {
            "cycle_pnls":    [],
            "total_pnl_pct": 0.0,
            "n_cycles":      0,
            "n_wins":        0,
            "n_stops":       0,
            "n_eod":         0,
            "outcome":       "NO_TRIGGER",
            "fallback":      False,
        }

    total_pnl = sum(cycle_pnls)
    n_cycles  = len(cycle_pnls)
    n_scratch = n_cycles - n_wins - n_stops - n_eod

    if n_wins > 0 and n_stops == 0 and n_eod == 0:
        outcome = f"WIN_x{n_wins}" if n_wins > 1 else "WIN"
    elif n_stops > 0 and n_wins == 0 and n_eod == 0:
        outcome = "STOP"
    elif n_eod > 0 and n_wins == 0 and n_stops == 0:
        outcome = "EOD"
    else:
        outcome = f"MIX_{n_wins}W_{n_stops}S_{n_eod}E"

    return {
        "cycle_pnls":    cycle_pnls,
        "total_pnl_pct": round(total_pnl, 3),
        "n_cycles":      n_cycles,
        "n_wins":        n_wins,
        "n_stops":       n_stops,
        "n_eod":         n_eod,
        "outcome":       outcome,
        "fallback":      False,
    }


def _fallback_daily(entry_high: float, daily_high: float, daily_low: float, daily_close: float) -> dict:
    """Single-candle fallback using daily OHLC when 5m data unavailable."""
    triggered = daily_high >= entry_high
    if not triggered:
        return {
            "cycle_pnls":    [],
            "total_pnl_pct": 0.0,
            "n_cycles":      0,
            "n_wins":        0,
            "n_stops":       0,
            "n_eod":         0,
            "outcome":       "NO_TRIGGER",
            "fallback":      True,
        }

    win_thresh  = entry_high * 1.02
    stop_thresh = entry_high * 0.93

    if daily_low <= stop_thresh:
        pnl     = STOP_PCT
        outcome = "STOP_FB"
        n_wins  = 0; n_stops = 1; n_eod = 0
    elif daily_high >= win_thresh:
        pnl     = WIN_PCT
        outcome = "WIN_FB"
        n_wins  = 1; n_stops = 0; n_eod = 0
    else:
        pnl     = (daily_close - entry_high) / entry_high * 100
        outcome = "SCRATCH_FB"
        n_wins  = 0; n_stops = 0; n_eod = 1

    return {
        "cycle_pnls":    [pnl],
        "total_pnl_pct": round(pnl, 3),
        "n_cycles":      1,
        "n_wins":        n_wins,
        "n_stops":       n_stops,
        "n_eod":         n_eod,
        "outcome":       outcome,
        "fallback":      True,
    }

# ─── Capital simulation (cycling with intra-day compounding) ──────────────────

def simulate_capital_cycling(picks_per_day: list[list[dict]]) -> tuple[list[float], dict]:
    """
    Capital simulation with intra-day compounding per cycling pick.

    For each pick on each day:
      - Allocate 40% of current capital as position size
      - Each cycle's P&L adjusts the sub-capital: sub_cap += sub_cap * pnl_pct/100
      - After all cycles for this pick: capital is updated by the net pick P&L
    """
    capital  = START_CAPITAL
    daily_caps = []

    total_wins   = 0
    total_stops  = 0
    total_scratch = 0
    total_cycles = 0

    for day_picks in picks_per_day:
        for pick in day_picks:
            cycle_pnls = pick.get("cycle_pnls", [])
            n_wins     = pick.get("n_wins",  0)
            n_stops    = pick.get("n_stops", 0)
            n_eod      = pick.get("n_eod",   0)

            total_wins   += n_wins
            total_stops  += n_stops
            total_scratch += max(0, len(cycle_pnls) - n_wins - n_stops)
            total_cycles += len(cycle_pnls)

            if not cycle_pnls:
                continue

            # Intra-day compounding: each cycle reinvests gains/absorbs losses
            sub_cap = capital * POSITION_PCT
            for pct in cycle_pnls:
                sub_cap *= (1 + pct / 100)

            # Net change to capital
            net_gain = sub_cap - (capital * POSITION_PCT)
            capital += net_gain

        capital = round(capital, 2)
        daily_caps.append(capital)

    n_picks = sum(len(d) for d in picks_per_day)
    avg_cycles = total_cycles / max(n_picks, 1)

    return daily_caps, {
        "final_capital": capital,
        "wins":          total_wins,
        "stops":         total_stops,
        "scratches":     total_scratch,
        "total_cycles":  total_cycles,
        "avg_cycles":    round(avg_cycles, 2),
    }

# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*70}")
    print(f"TASI CYCLING BACKTEST: {START_CAPITAL:,.0f} SAR | {START_DATE} to {END_DATE}")
    print(f"{'='*70}\n")

    universe     = load_universe()
    trading_days = get_trading_days(START_DATE, END_DATE)
    print(f"Universe: {len(universe)} tickers | Trading days: {len(trading_days)}")
    print(f"Days: {[str(d) for d in trading_days]}\n")

    # ── Step 1: Bulk download 60d daily data for all tickers ──────────────────
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

    # ── Step 2: Bulk download 5m intraday data for ALL tickers ────────────────
    print(f"Downloading 5m intraday data for {len(universe)} tickers (15 workers)...")
    cache_5m_bulk: dict[str, pd.DataFrame | None] = {}
    failed_5m = 0

    def _dl5m(tkr):
        return tkr, download_5m_bulk(tkr)

    with ThreadPoolExecutor(max_workers=15) as ex:
        futures5m = {ex.submit(_dl5m, t): t for t in universe}
        done5m = 0
        for fut in as_completed(futures5m):
            tkr, df = fut.result()
            cache_5m_bulk[tkr] = df
            if df is None:
                failed_5m += 1
            done5m += 1
            if done5m % 50 == 0:
                print(f"  5m: {done5m}/{len(universe)} ({len(universe)-failed_5m} ok, {failed_5m} failed)...")

    ok_5m = sum(1 for v in cache_5m_bulk.values() if v is not None)
    print(f"5m cache: {ok_5m} tickers loaded ({failed_5m} failed)\n")

    # ── Step 3: Per-day processing ─────────────────────────────────────────────
    prob_cache_2y: dict[str, pd.DataFrame | None] = {}

    picks_A: list[list[dict]] = []
    picks_B: list[list[dict]] = []
    picks_C: list[list[dict]] = []

    daily_avg_cycles: list[float] = []

    for day_idx, trade_day in enumerate(trading_days):
        eve_cutoff = pd.Timestamp(trade_day)
        trade_ts   = pd.Timestamp(trade_day)

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
            daily_avg_cycles.append(0.0)
            print(f"  Day {day_idx+1}/{len(trading_days)} ({trade_day}): no picks")
            continue

        # Download 2y data for prob models (lazy)
        new_tickers = [r["ticker"] for r in top5 if r["ticker"] not in prob_cache_2y]
        if new_tickers:
            def _dl2y(tkr):
                return tkr, download_2y(tkr)
            with ThreadPoolExecutor(max_workers=5) as ex:
                for fut in as_completed({ex.submit(_dl2y, t): t for t in new_tickers}):
                    tkr, df2y = fut.result()
                    prob_cache_2y[tkr] = df2y

        # Attach prob estimates to each pick
        for r in top5:
            df2y = prob_cache_2y.get(r["ticker"])
            conditions = {
                "rsi":           r["rsi"],
                "momentum":      r["momentum"],
                "vol_ratio":     r["vol_ratio"],
                "near_breakout": r["near_breakout"],
            }
            prob_b = estimate_win_prob_2y(df2y, conditions) if df2y is not None else None
            r["win_rate_b"]  = prob_b["win_rate"]  if prob_b else None
            r["n_samples_b"] = prob_b["n_samples"] if prob_b else None

            prob_c = estimate_win_prob_tuned(df2y, conditions) if df2y is not None else None
            r["win_rate_c"]  = prob_c["win_rate"]  if prob_c else None
            r["n_samples_c"] = prob_c["n_samples"] if prob_c else None

        # Strategy A: top 2 by score
        day_A_raw = [dict(r) for r in top5[:TOP_N_PICKS]]

        # Strategy B: prob veto on top 3
        top3_b = top5[:3]
        remaining_b = [
            r for r in top3_b
            if not (r["win_rate_b"] is not None and r["win_rate_b"] < 20.0)
        ]
        day_B_raw = [dict(r) for r in remaining_b[:TOP_N_PICKS]]
        if len(day_B_raw) < TOP_N_PICKS:
            used = {r["ticker"] for r in day_B_raw}
            fallbacks = [dict(r) for r in top5 if r["ticker"] not in used]
            day_B_raw = day_B_raw + fallbacks[:TOP_N_PICKS - len(day_B_raw)]

        # Strategy C: top 2 by win_rate_c (6mo tuned prob)
        has_prob_c = [r for r in top5 if r.get("win_rate_c") is not None]
        has_prob_c.sort(key=lambda x: x["win_rate_c"], reverse=True)
        day_C_raw = [dict(r) for r in has_prob_c[:TOP_N_PICKS]]
        if len(day_C_raw) < TOP_N_PICKS:
            used = {r["ticker"] for r in day_C_raw}
            fallbacks = [dict(r) for r in top5 if r["ticker"] not in used]
            day_C_raw = day_C_raw + fallbacks[:TOP_N_PICKS - len(day_C_raw)]

        # Collect unique tickers across all strategies (top 3 per strategy)
        # Only run intraday sim on top 3 picks per strategy per day
        unique_picks_today: dict[str, dict] = {}
        for r in (top5[:3] + day_A_raw + day_B_raw + day_C_raw):
            unique_picks_today[r["ticker"]] = r

        # Run cycling simulation for each unique pick
        cycling_results: dict[str, dict] = {}
        for tkr, r in unique_picks_today.items():
            df5m_day = get_5m_for_day(cache_5m_bulk.get(tkr), trade_day)

            # Get daily OHLC from 60d cache for fallback
            df_full = cache_60d.get(tkr)
            if df_full is not None:
                day_row = df_full[df_full.index == trade_ts]
                if len(day_row) > 0:
                    daily_high  = float(day_row["High"].iloc[0])
                    daily_low   = float(day_row["Low"].iloc[0])
                    daily_close = float(day_row["Close"].iloc[0])
                else:
                    daily_high = daily_low = daily_close = r["entry_high"]
            else:
                daily_high = daily_low = daily_close = r["entry_high"]

            cycling_results[tkr] = simulate_cycling(
                df5m_day, r["entry_high"], daily_high, daily_low, daily_close
            )

        # Attach cycling results to picks
        for pick in day_A_raw + day_B_raw + day_C_raw:
            tkr = pick["ticker"]
            cyc = cycling_results.get(tkr, {
                "cycle_pnls": [], "total_pnl_pct": 0.0,
                "n_cycles": 0, "n_wins": 0, "n_stops": 0, "n_eod": 0,
                "outcome": "NO_DATA", "fallback": True
            })
            pick.update(cyc)

        picks_A.append(day_A_raw)
        picks_B.append(day_B_raw)
        picks_C.append(day_C_raw)

        # Daily avg cycles across all active picks (A+B+C combined)
        all_today = day_A_raw + day_B_raw + day_C_raw
        all_cyc = [p["n_cycles"] for p in all_today if p.get("n_cycles", 0) > 0]
        avg_cyc = sum(all_cyc) / len(all_cyc) if all_cyc else 0.0
        daily_avg_cycles.append(avg_cyc)

        if (day_idx + 1) % 5 == 0 or day_idx == len(trading_days) - 1:
            print(f"  Day {day_idx+1}/{len(trading_days)} ({trade_day}) done — "
                  f"avg {avg_cyc:.1f} cycles/pick today")

    print()

    # ── Step 4: Capital simulation ─────────────────────────────────────────────
    caps_A, summary_A = simulate_capital_cycling(picks_A)
    caps_B, summary_B = simulate_capital_cycling(picks_B)
    caps_C, summary_C = simulate_capital_cycling(picks_C)

    # ── Step 5: Build output ───────────────────────────────────────────────────
    lines = []

    def L(s=""):
        lines.append(s)

    L(f"=== CYCLING BACKTEST: {START_CAPITAL:,.0f} SAR | {START_DATE} to {END_DATE} ===")
    L()
    L(f"{'Date':<12}| {'A: Rule-Based':>13} | {'B: Prob Veto':>12} | {'C: Tuned Prob':>13} | {'Avg cycles/pick':>15}")
    L(f"{'-'*11}|{'-'*15}|{'-'*14}|{'-'*15}|{'-'*17}")

    for i, d in enumerate(trading_days):
        ca  = caps_A[i]
        cb  = caps_B[i]
        cc  = caps_C[i]
        cyc = daily_avg_cycles[i]
        L(f"{str(d):<12}| {ca:>10,.0f} SAR | {cb:>9,.0f} SAR | {cc:>10,.0f} SAR | {cyc:>14.1f}")

    L()
    pct_A = (summary_A["final_capital"] - START_CAPITAL) / START_CAPITAL * 100
    pct_B = (summary_B["final_capital"] - START_CAPITAL) / START_CAPITAL * 100
    pct_C = (summary_C["final_capital"] - START_CAPITAL) / START_CAPITAL * 100

    L("FINAL RESULTS (Cycling):")
    L(f"Strategy A: {summary_A['final_capital']:,.0f} SAR ({pct_A:+.1f}%) "
      f"— {summary_A['wins']} wins, {summary_A['stops']} stops, "
      f"{summary_A['scratches']} scratch | avg {summary_A['avg_cycles']:.1f} cycles/pick/day")
    L(f"Strategy B: {summary_B['final_capital']:,.0f} SAR ({pct_B:+.1f}%) "
      f"— {summary_B['wins']} wins, {summary_B['stops']} stops, "
      f"{summary_B['scratches']} scratch | avg {summary_B['avg_cycles']:.1f} cycles/pick/day")
    L(f"Strategy C: {summary_C['final_capital']:,.0f} SAR ({pct_C:+.1f}%) "
      f"— {summary_C['wins']} wins, {summary_C['stops']} stops, "
      f"{summary_C['scratches']} scratch | avg {summary_C['avg_cycles']:.1f} cycles/pick/day")

    L()
    L("vs NO-CYCLING baseline:")
    L(f"Strategy A: {START_CAPITAL * (1 + NOCYCLE_A_PCT/100):,.0f} SAR (+{NOCYCLE_A_PCT:.1f}%)")
    L(f"Strategy B: {START_CAPITAL * (1 + NOCYCLE_B_PCT/100):,.0f} SAR (+{NOCYCLE_B_PCT:.1f}%)")
    L(f"Strategy C: {START_CAPITAL * (1 + NOCYCLE_C_PCT/100):,.0f} SAR (+{NOCYCLE_C_PCT:.1f}%)")

    L()
    L("Uplift from cycling:")
    delta_A = pct_A - NOCYCLE_A_PCT
    delta_B = pct_B - NOCYCLE_B_PCT
    delta_C = pct_C - NOCYCLE_C_PCT
    L(f"Strategy A: {delta_A:+.1f}% additional")
    L(f"Strategy B: {delta_B:+.1f}% additional")
    L(f"Strategy C: {delta_C:+.1f}% additional")

    L()
    L("="*70)
    L("DETAILED PER-DAY PICKS (CYCLING)")
    L("="*70)

    for i, d in enumerate(trading_days):
        L(f"\n{d}")
        for strat_name, day_picks in [("A", picks_A[i]), ("B", picks_B[i]), ("C", picks_C[i])]:
            if day_picks:
                picks_str = "  |  ".join(
                    f"{r['ticker']} entry={r['entry_high']:.2f} "
                    f"→ {r.get('outcome','?')} pnl={r.get('total_pnl_pct',0):+.2f}% "
                    f"(cyc={r.get('n_cycles',0)}, W={r.get('n_wins',0)}, "
                    f"S={r.get('n_stops',0)}, fb={r.get('fallback',True)})"
                    for r in day_picks
                )
            else:
                picks_str = "(no picks)"
            L(f"  [{strat_name}] {picks_str}")

    full_output = "\n".join(lines)

    with open(OUTPUT_FILE, "w") as f:
        f.write(full_output)
    print(f"Full output saved to: {OUTPUT_FILE}\n")

    # Print summary to stdout
    summary_start = next((i for i, line in enumerate(lines) if line.startswith("===")), 0)
    for line in lines[summary_start:]:
        if line.startswith("="*70):
            break
        print(line)

    print(f"\nFull log: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

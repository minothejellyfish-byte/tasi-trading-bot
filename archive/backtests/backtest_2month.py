#!/usr/bin/env python3
"""2-month capital simulation: 2026-03-02 to 2026-05-15, 1000 SAR start"""
import json, sys, warnings
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
import yfinance as yf
import pandas_ta as ta
warnings.filterwarnings('ignore')

START_CAPITAL = 1000.0
SHARIA_FILE   = "/home/mino/tasi-exec/sharia_list.json"
OUTPUT_FILE   = "/home/mino/tasi-exec/backtest_2month_output.txt"
PHASE2_START  = date(2026, 3, 17)   # 5m data available from here
SIM_START     = date(2026, 3, 2)
SIM_END       = date(2026, 5, 15)
WIN_PCT       = 2.0
STOP_PCT      = -7.0

REGIME_PARAMS = {
    "TRENDING":  {"position_pct": 0.45, "max_cycles": 8},
    "NEUTRAL":   {"position_pct": 0.40, "max_cycles": 2},
    "DEFENSIVE": {"position_pct": 0.20, "max_cycles": 1},
}

lines = []
def L(s=""):
    print(s)
    lines.append(s)

def trading_days(start, end):
    days, d = [], start
    while d <= end:
        if d.weekday() in (6,0,1,2,3):
            days.append(d)
        d += timedelta(days=1)
    return days

def load_tickers():
    with open(SHARIA_FILE) as f:
        return json.load(f)["main_market_yahoo_tickers"]

def download_ticker(ticker, period="3mo", interval="1d"):
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 5:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df.dropna()
    except:
        return None

def score_stock_on_day(df_full, sim_date):
    df = df_full[df_full.index.date < sim_date]
    if len(df) < 15:
        return None
    try:
        close   = df["Close"].iloc[-1]
        vol20   = df["Volume"].rolling(20).mean().iloc[-1]
        vol1    = df["Volume"].iloc[-1]
        if vol20 < 500_000 or close < 10 or close > 500:
            return None
        rsi = ta.rsi(df["Close"], length=14)
        if rsi is None or rsi.iloc[-1] > 70:
            return None
        rsi_val    = rsi.iloc[-1]
        sma10      = df["Close"].rolling(10).mean().iloc[-1]
        momentum   = (close - sma10) / sma10 * 100
        vol_ratio  = vol1 / vol20 if vol20 > 0 else 0
        prev_high  = df["High"].iloc[-2]
        prev_low   = df["Low"].iloc[-2]
        high20     = df["High"].rolling(20).max().iloc[-2]
        near_bo    = close > high20 * 0.98
        resist10d  = df["High"].iloc[-11:-1].max()
        dist_high  = (resist10d - close) / close * 100
        closes10   = df["Close"].iloc[-11:-1].values
        lows3      = df["Low"].iloc[-3:].values
        slope      = np.polyfit(np.arange(len(closes10)), closes10, 1)[0]
        trend_pct  = slope / closes10[0] * 100 if closes10[0] > 0 else 0
        higher_lows = bool(lows3[1] > lows3[0] and lows3[2] > lows3[1])
        entry_high = round(prev_high * 1.001, 2)
        entry_low  = round(prev_low  * 1.001, 2)
        if close > entry_high * 1.01:
            return None
        score = 0
        score += min(momentum, 5) * 10
        score += min(vol_ratio, 3) * 15
        score += (5 - min(dist_high, 5)) * 5
        score += 20 if near_bo else 0
        score -= max(rsi_val - 60, 0) * 2
        score += min(max(trend_pct * 5, -20), 15)
        score += 10 if higher_lows else 0
        return {
            "ticker": None, "close": close, "rsi": round(rsi_val,1),
            "momentum": round(momentum,2), "vol_ratio": round(vol_ratio,2),
            "near_breakout": near_bo, "entry_high": entry_high,
            "entry_low": entry_low, "score": round(score,1),
            "stop_loss": round(close * 0.93, 2),
        }
    except:
        return None

_regime_counts_debug = {"TRENDING": 0, "NEUTRAL": 0, "DEFENSIVE": 0}
_regime_days_classified = 0

def classify_regime_on_day(tasi_df, oil_df, sim_date):
    global _regime_days_classified
    try:
        if tasi_df is None or oil_df is None:
            return "NEUTRAL"
        td = tasi_df[tasi_df.index.date < sim_date]
        od = oil_df[oil_df.index.date < sim_date]
        if len(td) < 11 or len(od) < 3:
            return "NEUTRAL"
        sma5       = td["Close"].rolling(5).mean().iloc[-1]
        momentum   = (td["Close"].iloc[-1] - sma5) / sma5 * 100
        # Use Close-to-Close return (avoids unreliable Open prices from yfinance)
        yesterday  = (td["Close"].iloc[-1] - td["Close"].iloc[-2]) / td["Close"].iloc[-2] * 100
        # 5-day return for bonus/penalty
        tasi_5d = (td["Close"].iloc[-1] - td["Close"].iloc[-6]) / td["Close"].iloc[-6] * 100 if len(td) >= 6 else 0.0
        oil_2d     = (od["Close"].iloc[-1] - od["Close"].iloc[-2]) / od["Close"].iloc[-2] * 100
        sma10      = td["Close"].rolling(10).mean().iloc[-1]
        above_sma10 = td["Close"].iloc[-1] > sma10
        score = 0
        score += 2 if momentum > 0 else -1
        score += (2 if yesterday > 0.3 else (-2 if yesterday < -0.3 else 0))
        score += (1 if oil_2d > 1.0 else (-1 if oil_2d < -1.0 else 0))
        score += 1 if above_sma10 else -1
        score += (1 if tasi_5d > 1.5 else (-1 if tasi_5d < -1.5 else 0))
        if score >= 4:   regime = "TRENDING"
        elif score >= 1: regime = "NEUTRAL"
        else:            regime = "DEFENSIVE"
        _regime_counts_debug[regime] = _regime_counts_debug.get(regime, 0) + 1
        _regime_days_classified += 1
        if _regime_days_classified % 10 == 0:
            print(f"  [regime debug] {_regime_days_classified} days: TRENDING={_regime_counts_debug['TRENDING']} NEUTRAL={_regime_counts_debug['NEUTRAL']} DEFENSIVE={_regime_counts_debug['DEFENSIVE']}", flush=True)
        return regime
    except:
        return "NEUTRAL"

def simulate_daily(day_data, entry_high, regime):
    if day_data is None or len(day_data) == 0:
        return {"outcome": "NO_DATA", "pnl_pct": 0.0, "cycles": 0}
    row = day_data.iloc[0]
    if row["High"] < entry_high:
        return {"outcome": "NO_TRIGGER", "pnl_pct": 0.0, "cycles": 0}
    if row["Low"] <= entry_high * (1 + STOP_PCT/100):
        return {"outcome": "STOP", "pnl_pct": STOP_PCT, "cycles": 1}
    if row["High"] >= entry_high * (1 + WIN_PCT/100):
        return {"outcome": "WIN", "pnl_pct": WIN_PCT, "cycles": 1}
    pnl = (row["Close"] - entry_high) / entry_high * 100
    return {"outcome": "SCRATCH", "pnl_pct": round(pnl,2), "cycles": 1}

def simulate_cycling(intra_df, entry_high, regime):
    if intra_df is None or len(intra_df) == 0:
        return {"outcome": "NO_DATA", "pnl_pct": 0.0, "cycles": 0, "wins": 0, "stops": 0}
    params     = REGIME_PARAMS[regime]
    max_cycles = params["max_cycles"]
    cutoff_utc = "11:30"   # 14:30 Riyadh = 11:30 UTC
    df = intra_df.copy()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    close_utc = "11:45"
    total_pnl = 0.0
    cycles = 0
    wins = 0
    stops = 0
    consec_scratch = 0
    in_position = False
    ep = entry_high
    triggered = False
    for i, (ts, row) in enumerate(df.iterrows()):
        t = ts.strftime("%H:%M")
        if not in_position:
            if t >= cutoff_utc:
                break
            if not triggered and row["High"] < ep:
                continue
            triggered = True
            in_position = True
            ep = ep
        if in_position:
            if row["Low"] <= ep * (1 + STOP_PCT/100):
                total_pnl += STOP_PCT
                stops += 1
                cycles += 1
                break
            if row["High"] >= ep * (1 + WIN_PCT/100):
                total_pnl += WIN_PCT
                wins += 1
                cycles += 1
                consec_scratch = 0
                in_position = False
                if cycles >= max_cycles or t >= cutoff_utc:
                    break
                continue
            if t >= close_utc:
                pnl = (row["Close"] - ep) / ep * 100
                total_pnl += pnl
                cycles += 1
                if pnl < 0:
                    consec_scratch += 1
                else:
                    consec_scratch = 0
                in_position = False
                break
        if not in_position and cycles > 0:
            if consec_scratch >= 2:
                break
            if cycles >= max_cycles:
                break
            if t >= cutoff_utc:
                break
            ep = entry_high
    if in_position:
        last = df.iloc[-1]
        pnl = (last["Close"] - ep) / ep * 100
        total_pnl += pnl
        cycles += 1
        if pnl < 0:
            consec_scratch += 1
    label = "NO_TRIGGER" if not triggered else ("MIX" if cycles > 1 else ("WIN" if wins else ("STOP" if stops else "SCRATCH")))
    return {"outcome": label, "pnl_pct": round(total_pnl,2), "cycles": cycles, "wins": wins, "stops": stops}

def pick_strategy(scored, prob_cache, regime, strategy):
    if strategy == "A":
        return scored[:2]
    if strategy == "B":
        vetoed = [r for r in scored if prob_cache.get(r["ticker"], {}).get("win_rate", 100) >= 20]
        result = vetoed[:2]
        if len(result) < 2:
            result += [r for r in scored if r not in result][:2-len(result)]
        return result[:2]
    if strategy == "C":
        with_prob = [(r, prob_cache.get(r["ticker"], {})) for r in scored]
        c_picks = sorted([r for r, p in with_prob if p.get("win_rate") is not None],
                         key=lambda r: prob_cache[r["ticker"]].get("win_rate", 0), reverse=True)
        result = c_picks[:2]
        if len(result) < 2:
            result += [r for r in scored if r not in result][:2-len(result)]
        return result[:2]
    return scored[:2]

def main():
    tickers = load_tickers()
    days    = trading_days(SIM_START, SIM_END)
    L(f"Loading data for {len(tickers)} tickers over {len(days)} trading days…")

    daily_cache = {}
    intra_cache = {}
    prob_cache  = {}

    def fetch_daily(t):
        return t, download_ticker(t, period="3mo", interval="1d")
    def fetch_intra(t):
        return t, download_ticker(t, period="60d", interval="5m")

    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(fetch_daily, t): t for t in tickers}
        for i, f in enumerate(as_completed(futs)):
            t, df = f.result()
            if df is not None:
                daily_cache[t] = df
            if (i+1) % 50 == 0:
                print(f"  Daily: {i+1}/{len(tickers)}", flush=True)

    L(f"Daily data: {len(daily_cache)} tickers loaded")

    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(fetch_intra, t): t for t in tickers}
        for i, f in enumerate(as_completed(futs)):
            t, df = f.result()
            if df is not None:
                intra_cache[t] = df
            if (i+1) % 50 == 0:
                print(f"  Intra: {i+1}/{len(tickers)}", flush=True)

    L(f"Intraday data: {len(intra_cache)} tickers loaded")

    tasi_df = download_ticker("EWS", period="3mo", interval="1d")
    oil_df  = download_ticker("BZ=F",  period="3mo", interval="1d")
    tasi_df = tasi_df if tasi_df is not None and len(tasi_df) > 5 else None
    oil_df  = oil_df  if oil_df  is not None and len(oil_df)  > 2 else None
    print(f"EWS rows: {len(tasi_df) if tasi_df is not None else 0} | BZ=F rows: {len(oil_df) if oil_df is not None else 0}")

    try:
        from screener_prob import estimate_win_prob
        prob_enabled = True
    except:
        prob_enabled = False
        L("Warning: screener_prob unavailable — prob disabled")

    caps_A = [START_CAPITAL]
    caps_B = [START_CAPITAL]
    caps_C = [START_CAPITAL]
    caps_D = [START_CAPITAL]
    caps_A_fb = [START_CAPITAL]
    caps_B_fb = [START_CAPITAL]
    cycles_A = []
    cycles_B = []
    cycles_C = []
    cycles_D = []
    cycles_A_fb = []
    cycles_B_fb = []
    regime_counts = {"TRENDING": 0, "NEUTRAL": 0, "DEFENSIVE": 0}
    all_rows = []

    L()
    L("=== 2-MONTH CAPITAL SIMULATION: 1,000 SAR | 2026-03-02 to 2026-05-15 ===")
    L("(Phase 1: Mar 2-16 = daily only | Phase 2: Mar 17-May 15 = 5m cycling)")
    L()
    L(f"{'Date':<12} {'Regime':<11} {'A:Rule':>9} {'B:Veto':>9} {'C:Prob':>9} {'D:Hybrd':>9} {'A:FB':>9} {'B:FB':>9}")
    L("-"*80)

    phase2_printed = False

    for day in days:
        regime = classify_regime_on_day(tasi_df, oil_df, day)
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
        params = REGIME_PARAMS[regime]
        pos_pct = params["position_pct"]
        is_phase2 = day >= PHASE2_START

        if is_phase2 and not phase2_printed:
            L("--- Phase 2 starts (cycling enabled) ---")
            phase2_printed = True

        scored = []
        for t, df in daily_cache.items():
            r = score_stock_on_day(df, day)
            if r:
                r["ticker"] = t
                scored.append(r)
        scored.sort(key=lambda x: x["score"], reverse=True)
        top5 = scored[:5]

        if prob_enabled:
            for r in top5:
                t = r["ticker"]
                if t not in prob_cache:
                    try:
                        cond = {"rsi": r["rsi"], "momentum": r["momentum"],
                                "vol_ratio": r["vol_ratio"], "near_breakout": r["near_breakout"]}
                        prob_cache[t] = estimate_win_prob(t, cond) or {}
                    except:
                        prob_cache[t] = {}

        picks_A = pick_strategy(scored, prob_cache, regime, "A")
        picks_B = pick_strategy(scored, prob_cache, regime, "B")
        picks_C = pick_strategy(scored, prob_cache, regime, "C")
        # Strategy D: B on NEUTRAL/DEFENSIVE, C on TRENDING
        picks_D = pick_strategy(scored, prob_cache, regime, "C" if regime == "TRENDING" else "B")

        def get_day_intra(t, d):
            df = intra_cache.get(t)
            if df is None:
                return None
            df2 = df.copy()
            df2.index = pd.to_datetime(df2.index)
            if df2.index.tz is not None:
                df2.index = df2.index.tz_convert("UTC").tz_localize(None)
            mask = df2.index.date == d
            return df2[mask] if mask.any() else None

        def get_day_daily(t, d):
            df = daily_cache.get(t)
            if df is None:
                return None
            mask = df.index.date == d
            return df[mask] if mask.any() else None

        def sim_pick(pick, day, is_phase2, regime):
            eh = pick["entry_high"]
            if is_phase2:
                idf = get_day_intra(pick["ticker"], day)
                return simulate_cycling(idf, eh, regime)
            else:
                ddf = get_day_daily(pick["ticker"], day)
                return simulate_daily(ddf, eh, regime)

        def apply_day(picks, capital, regime):
            pnl_total = 0.0
            day_cycles = 0
            for pick in picks:
                res = sim_pick(pick, day, is_phase2, regime)
                pos = capital * pos_pct
                pnl_total += pos * res["pnl_pct"] / 100
                day_cycles += res.get("cycles", 0)
            return round(capital + pnl_total, 2), day_cycles

        # ── Fallback logic: top 5 picks, activate fallback if primary idle ──
        FALLBACK_TIMEOUT = "10:30"   # activate fallback at 10:30 (07:30 UTC)
        def apply_day_fallback(picks_all, capital, regime, is_phase2, day):
            """Apply fallback: try #1-2 first, if no trigger by 10:30 try #3-5"""
            pnl_total = 0.0
            day_cycles = 0
            any_triggered = False
            primary = picks_all[:2]
            fallback = picks_all[2:5]

            for pick in primary:
                res = sim_pick(pick, day, is_phase2, regime)
                if res["outcome"] != "NO_TRIGGER":
                    any_triggered = True
                pos = capital * pos_pct
                pnl_total += pos * res["pnl_pct"] / 100
                day_cycles += res.get("cycles", 0)

            if not any_triggered and fallback:
                # Check if any primary had no trigger at all
                for pick in fallback:
                    res = sim_pick(pick, day, is_phase2, regime)
                    pos = capital * pos_pct
                    pnl_total += pos * res["pnl_pct"] / 100
                    day_cycles += res.get("cycles", 0)

            return round(capital + pnl_total, 2), day_cycles

        cap_a, dc_a = apply_day(picks_A, caps_A[-1], regime)
        cap_b, dc_b = apply_day(picks_B, caps_B[-1], regime)
        cap_c, dc_c = apply_day(picks_C, caps_C[-1], regime)
        cap_d, dc_d = apply_day(picks_D, caps_D[-1], regime)
        cap_a_fb, dc_a_fb = apply_day_fallback(picks_A, caps_A_fb[-1], regime, is_phase2, day)
        cap_b_fb, dc_b_fb = apply_day_fallback(picks_B, caps_B_fb[-1], regime, is_phase2, day)

        caps_A.append(cap_a)
        caps_B.append(cap_b)
        caps_C.append(cap_c)
        caps_D.append(cap_d)
        caps_A_fb.append(cap_a_fb)
        caps_B_fb.append(cap_b_fb)
        cycles_A.append(dc_a)
        cycles_B.append(dc_b)
        cycles_C.append(dc_c)
        cycles_D.append(dc_d)
        cycles_A_fb.append(dc_a_fb)
        cycles_B_fb.append(dc_b_fb)

        L(f"{str(day):<12} {regime:<11} {cap_a:>9,.0f} {cap_b:>9,.0f} {cap_c:>9,.0f} {cap_d:>9,.0f} {cap_a_fb:>9,.0f} {cap_b_fb:>9,.0f}")
        all_rows.append((day, regime, cap_a, cap_b, cap_c, cap_d, cap_a_fb, cap_b_fb))

    L()
    L("=== FINAL RESULTS ===")
    fa, fb, fc, fd = caps_A[-1], caps_B[-1], caps_C[-1], caps_D[-1]
    fa_fb, fb_fb = caps_A_fb[-1], caps_B_fb[-1]
    ra = (fa - START_CAPITAL) / START_CAPITAL * 100
    rb = (fb - START_CAPITAL) / START_CAPITAL * 100
    rc = (fc - START_CAPITAL) / START_CAPITAL * 100
    rd = (fd - START_CAPITAL) / START_CAPITAL * 100
    ra_fb = (fa_fb - START_CAPITAL) / START_CAPITAL * 100
    rb_fb = (fb_fb - START_CAPITAL) / START_CAPITAL * 100
    L(f"Strategy A (Rule-Based):    {fa:>8,.0f} SAR  ({ra:+.1f}%)")
    L(f"Strategy B (Prob Veto):     {fb:>8,.0f} SAR  ({rb:+.1f}%)")
    L(f"Strategy C (Tuned Prob):    {fc:>8,.0f} SAR  ({rc:+.1f}%)")
    L(f"Strategy D (B+C Hybrid):    {fd:>8,.0f} SAR  ({rd:+.1f}%)")
    L(f"  → D uses C on TRENDING days, B on NEUTRAL/DEFENSIVE")
    L()
    L("=== FALLBACK COMPARISON (Top 5 with fallback) ===")
    L(f"Strategy A+Fallback:        {fa_fb:>8,.0f} SAR  ({ra_fb:+.1f}%)")
    L(f"Strategy B+Fallback:        {fb_fb:>8,.0f} SAR  ({rb_fb:+.1f}%)")
    L(f"  → Fallback tries #3-5 if #1-2 don't trigger")
    L()
    if ra_fb > ra:
        L(f"  A+FB beats A by: +{ra_fb - ra:.1f}pp")
    else:
        L(f"  A+FB underperforms A by: {ra_fb - ra:.1f}pp")
    if rb_fb > rb:
        L(f"  B+FB beats B by: +{rb_fb - rb:.1f}pp")
    else:
        L(f"  B+FB underperforms B by: {rb_fb - rb:.1f}pp")
    L()
    L(f"Regime distribution: TRENDING={regime_counts.get('TRENDING',0)} | NEUTRAL={regime_counts.get('NEUTRAL',0)} | DEFENSIVE={regime_counts.get('DEFENSIVE',0)} days")
    if cycles_A:
        L(f"Avg cycles/pick (Phase 2): A={sum(cycles_A)/max(len(cycles_A),1):.1f} B={sum(cycles_B)/max(len(cycles_B),1):.1f} C={sum(cycles_C)/max(len(cycles_C),1):.1f} D={sum(cycles_D)/max(len(cycles_D),1):.1f}")
        L(f"Avg cycles (Fallback):     A+FB={sum(cycles_A_fb)/max(len(cycles_A_fb),1):.1f} B+FB={sum(cycles_B_fb)/max(len(cycles_B_fb),1):.1f}")

    if len(all_rows) > 1:
        daily_deltas_a = [all_rows[i][2]-all_rows[i-1][2] for i in range(1,len(all_rows))]
        best_i = daily_deltas_a.index(max(daily_deltas_a))
        worst_i = daily_deltas_a.index(min(daily_deltas_a))
        L(f"Best day  (A): {all_rows[best_i+1][0]} ({all_rows[best_i+1][1]}) +{max(daily_deltas_a):.1f} SAR")
        L(f"Worst day (A): {all_rows[worst_i+1][0]} ({all_rows[worst_i+1][1]}) {min(daily_deltas_a):.1f} SAR")

    with open(OUTPUT_FILE, "w") as f:
        f.write("\n".join(lines))
    L(f"\nFull log saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    import os
    os.chdir("/home/mino/tasi-exec")
    main()

#!/usr/bin/env python3
"""
Market Regime Classifier for TASI Trading System.
Classifies each trading day as TRENDING / NEUTRAL / DEFENSIVE based on
TASI index momentum and oil price signals.

Pre-market: classify_premarket() — called from screener.py at ~09:50
Intraday:   classify_intraday()  — called from poller.py every 30 min
"""

import json
import logging
import os
from datetime import datetime

import requests
import yfinance as yf

# ─── Config ──────────────────────────────────────────────────────────────────

REGIME_FILE = "/home/mino/tasi-exec/regime.json"
BOT_TOKEN   = "8989533040:AAFWzP_lYL3g_w4eXGxrvwdo-tBNdPxVYQU"
CHAT_ID     = 5529987063

REGIME_PARAMS = {
    "TRENDING":  {
        "strategy":         "C",
        "max_positions":    3,
        "position_pct":     0.35,
        "alt_position_pct": 0.25,
        "target_pct":       0.020,   # +2.0% target (was 2.5%)
        "trail_trigger":    0.020,   # trail after +2.0% (matches target)
        "trail_stop":       0.03,    # -3% from peak
        "hard_stop":        0.07,    # -7% hard stop
        "time_stop_mins":   30,      # 30 min time stop
        "time_stop_pct":    0.01,    # -1% after 30min
    },
    "NEUTRAL":   {
        "strategy":         "B",
        "max_positions":    3,
        "position_pct":     0.30,
        "alt_position_pct": 0.30,
        "target_pct":       0.012,   # +1.2% target (was 2.0%)
        "trail_trigger":    0.012,   # trail after +1.2% (matches target)
        "trail_stop":       0.03,    # -3% from peak
        "hard_stop":        0.05,    # -5% hard stop (tighter)
        "time_stop_mins":   30,
        "time_stop_pct":    0.01,
    },
    "DEFENSIVE": {
        "strategy":         "B",
        "max_positions":    4,
        "position_pct":     0.20,
        "alt_position_pct": 0.20,
        "target_pct":       0.008,   # +0.8% target (was 1.5%)
        "trail_trigger":    0.008,   # trail after +0.8% (matches target)
        "trail_stop":       0.02,    # -2% from peak (tight)
        "hard_stop":        0.04,    # -4% hard stop (tight)
        "time_stop_mins":   20,      # 20 min time stop (faster)
        "time_stop_pct":    0.005,   # -0.5% after 20min
    },
}

_NEUTRAL_DEFAULT = {
    "regime": "NEUTRAL",
    "params": REGIME_PARAMS["NEUTRAL"],
    "reason": "Default — no data available.",
    "classified_at": None,
}

log = logging.getLogger(__name__)

# ─── Telegram ────────────────────────────────────────────────────────────────

def _tg_send(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        log.error(f"market_regime tg_send failed: {e}")

# ─── Regime file helpers ──────────────────────────────────────────────────────

def _read_regime_file() -> dict:
    """Read regime.json; return NEUTRAL defaults if missing or corrupt."""
    try:
        if os.path.exists(REGIME_FILE):
            with open(REGIME_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return dict(_NEUTRAL_DEFAULT)


def _write_regime_file(data: dict):
    try:
        with open(REGIME_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        log.error(f"Failed to write regime.json: {e}")

# ─── Notification ─────────────────────────────────────────────────────────────

def notify_regime_change(old_regime: str, new_regime: str, reason: str):
    """Send Telegram alert when regime shifts during the session."""
    action_map = {
        "TRENDING":  "Increase position size to 45% (2 picks = 90% deployed), up to 8 cycles — time-based limit.",
        "NEUTRAL":   "Standard 40% position size, max 2 cycles.",
        "DEFENSIVE": "Reduce to 20% position size, max 1 cycle — preserve capital.",
    }
    action = action_map.get(new_regime, "Review parameters.")
    msg = (
        f"⚠️ <b>Regime changed: {old_regime} → {new_regime}</b>\n"
        f"{reason}\n"
        f"Action: {action}"
    )
    _tg_send(msg)

# ─── Pre-market classification ────────────────────────────────────────────────

def classify_premarket() -> dict:
    """
    Download daily TASI + Brent data and classify today's regime before open.
    Writes regime.json and returns the regime dict.
    """
    try:
        tasi_df = yf.download("EWS", period="15d", interval="1d",
                               progress=False, auto_adjust=True)
        oil_df  = yf.download("BZ=F",  period="5d",  interval="1d",
                               progress=False, auto_adjust=True)

        # Flatten multi-level columns if present
        if tasi_df is not None and not tasi_df.empty:
            tasi_df.columns = [c[0] if isinstance(c, tuple) else c for c in tasi_df.columns]
            tasi_df = tasi_df.dropna(subset=["Close"])
        if oil_df is not None and not oil_df.empty:
            oil_df.columns  = [c[0] if isinstance(c, tuple) else c for c in oil_df.columns]
            oil_df = oil_df.dropna(subset=["Close"])

        if tasi_df is None or len(tasi_df) < 5:
            log.warning("classify_premarket: insufficient TASI data — defaulting to NEUTRAL")
            result = dict(_NEUTRAL_DEFAULT)
            result["classified_at"] = datetime.now().isoformat()
            _write_regime_file(result)
            return result

    except Exception as e:
        log.warning(f"classify_premarket download failed: {e} — defaulting to NEUTRAL")
        result = dict(_NEUTRAL_DEFAULT)
        result["classified_at"] = datetime.now().isoformat()
        _write_regime_file(result)
        return result

    # ── Debug: show what yfinance returned ───────────────────────────────────
    print(f"[DEBUG] ^TASI rows: {len(tasi_df)} | last 3 dates: {list(tasi_df.index[-3:])}")
    print(f"[DEBUG] ^TASI last close: {tasi_df['Close'].iloc[-1]:.2f} | prev close: {tasi_df['Close'].iloc[-2]:.2f}")

    # ── Compute signals ───────────────────────────────────────────────────────

    tasi_close = tasi_df["Close"]

    last_close  = float(tasi_close.iloc[-1])
    prev_close  = float(tasi_close.iloc[-2])
    sma5        = float(tasi_close.rolling(5).mean().iloc[-1])
    sma10       = float(tasi_close.rolling(10).mean().iloc[-1]) if len(tasi_close) >= 10 else sma5

    tasi_momentum    = (last_close - sma5) / sma5 * 100
    # Use Close-to-Close return (avoids unreliable Open prices from yfinance)
    tasi_yesterday   = (last_close - prev_close) / prev_close * 100 if prev_close else 0
    tasi_above_sma10 = last_close > sma10

    # 5-day return: close[-1] vs close[-6]
    if len(tasi_close) >= 6:
        tasi_5d_return = float((tasi_close.iloc[-1] - tasi_close.iloc[-6]) / tasi_close.iloc[-6] * 100)
    else:
        tasi_5d_return = 0.0

    oil_2d = 0.0
    if oil_df is not None and len(oil_df) >= 2:
        oil_close = oil_df["Close"]
        oil_2d = float((oil_close.iloc[-1] - oil_close.iloc[-2]) / oil_close.iloc[-2] * 100)

    # ── Score ─────────────────────────────────────────────────────────────────

    score  = 0
    signals = []

    if tasi_momentum > 0:
        score += 2
        signals.append(f"TASI momentum +{tasi_momentum:.2f}% above SMA5 (+2)")
    else:
        score -= 1
        signals.append(f"TASI momentum {tasi_momentum:.2f}% below SMA5 (-1)")

    if tasi_yesterday > 0.3:
        score += 2
        signals.append(f"Yesterday +{tasi_yesterday:.2f}% C-to-C (bullish day, +2)")
    elif tasi_yesterday < -0.3:
        score -= 2
        signals.append(f"Yesterday {tasi_yesterday:.2f}% C-to-C (bearish day, -2)")
    else:
        signals.append(f"Yesterday {tasi_yesterday:.2f}% C-to-C (flat, 0)")

    if tasi_5d_return > 1.5:
        score += 1
        signals.append(f"5d return +{tasi_5d_return:.2f}% (strong trend bonus, +1)")
    elif tasi_5d_return < -1.5:
        score -= 1
        signals.append(f"5d return {tasi_5d_return:.2f}% (weak trend penalty, -1)")
    else:
        signals.append(f"5d return {tasi_5d_return:.2f}% (neutral, 0)")

    if oil_2d > 1.0:
        score += 1
        signals.append(f"Oil +{oil_2d:.2f}% 2-day (+1)")
    elif oil_2d < -1.0:
        score -= 1
        signals.append(f"Oil {oil_2d:.2f}% 2-day (-1)")
    else:
        signals.append(f"Oil {oil_2d:.2f}% 2-day (0)")

    if tasi_above_sma10:
        score += 1
        signals.append(f"TASI above SMA10 ({last_close:.1f} > {sma10:.1f}, +1)")
    else:
        score -= 1
        signals.append(f"TASI below SMA10 ({last_close:.1f} < {sma10:.1f}, -1)")

    # ── Classify ──────────────────────────────────────────────────────────────

    if score >= 4:
        regime = "TRENDING"
    elif score >= 1:
        regime = "NEUTRAL"
    else:
        regime = "DEFENSIVE"

    reason = f"Score {score}: " + " | ".join(signals)

    result = {
        "regime":          regime,
        "params":          REGIME_PARAMS[regime],
        "score":           score,
        "reason":          reason,
        "tasi_momentum":   round(tasi_momentum, 3),
        "tasi_yesterday":  round(tasi_yesterday, 3),
        "tasi_5d_return":  round(tasi_5d_return, 3),
        "oil_2d":          round(oil_2d, 3),
        "tasi_above_sma10": tasi_above_sma10,
        "classified_at":   datetime.now().isoformat(),
        "source":          "premarket",
    }

    _write_regime_file(result)
    log.info(f"Pre-market regime: {regime} (score={score})")
    return result

# ─── Intraday classification ──────────────────────────────────────────────────

def classify_intraday() -> dict:
    """
    Re-evaluate regime using 5-minute intraday TASI data.
    Overrides if session return crosses key thresholds.
    Notifies on regime change. Writes regime.json.
    """
    current = _read_regime_file()
    old_regime = current.get("regime", "NEUTRAL")

    try:
        df = yf.download("EWS", interval="5m", period="1d",
                          progress=False, auto_adjust=True)
        if df is None or df.empty:
            log.warning("classify_intraday: no intraday data — keeping current regime")
            return current

        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna(subset=["Close"])

        if len(df) < 2:
            return current

    except Exception as e:
        log.warning(f"classify_intraday download failed: {e} — keeping current regime")
        return current

    # ── Compute intraday signals ──────────────────────────────────────────────

    first_open    = float(df["Open"].iloc[0]) if "Open" in df.columns else float(df["Close"].iloc[0])
    current_close = float(df["Close"].iloc[-1])

    session_return = (current_close - first_open) / first_open * 100 if first_open else 0

    # VWAP
    df = df.copy()
    df["tp"] = (df["High"] + df["Low"] + df["Close"]) / 3
    total_vol = float(df["Volume"].sum())
    vwap = float((df["tp"] * df["Volume"]).sum() / total_vol) if total_vol > 0 else current_close
    above_vwap = current_close > vwap
    big_move   = abs(session_return) > 1.5

    # ── Override logic ────────────────────────────────────────────────────────

    if session_return < -1.0:
        new_regime = "DEFENSIVE"
        reason = (
            f"Session return {session_return:.2f}% (below -1%) → forced DEFENSIVE. "
            f"VWAP: {vwap:.1f} | Current: {current_close:.1f}"
        )
    elif session_return > 1.0 and above_vwap:
        new_regime = "TRENDING"
        reason = (
            f"Session return +{session_return:.2f}% and above VWAP ({current_close:.1f} > {vwap:.1f}) "
            f"→ upgraded to TRENDING."
        )
    else:
        new_regime = old_regime
        reason = (
            f"Session return {session_return:.2f}%, above_vwap={above_vwap} — no override, "
            f"keeping {old_regime}."
        )

    # Notify if changed
    if new_regime != old_regime:
        notify_regime_change(old_regime, new_regime, reason)

    result = {
        "regime":         new_regime,
        "params":         REGIME_PARAMS[new_regime],
        "score":          current.get("score"),
        "reason":         reason,
        "session_return": round(session_return, 3),
        "vwap":           round(vwap, 3),
        "above_vwap":     above_vwap,
        "big_move":       big_move,
        "classified_at":  datetime.now().isoformat(),
        "source":         "intraday",
    }

    _write_regime_file(result)
    log.info(f"Intraday regime: {new_regime} (session={session_return:.2f}%, vwap_above={above_vwap})")
    return result

# ─── Public accessor ──────────────────────────────────────────────────────────

def get_current_regime() -> dict:
    """
    Read regime.json and return it.
    Falls back to NEUTRAL defaults if the file is missing or unreadable.
    """
    data = _read_regime_file()
    # Ensure params key is always populated
    regime_name = data.get("regime", "NEUTRAL")
    if regime_name not in REGIME_PARAMS:
        regime_name = "NEUTRAL"
    if "params" not in data or not data["params"]:
        data["params"] = REGIME_PARAMS[regime_name]
    data["regime"] = regime_name
    return data


if __name__ == "__main__":
    import json as _json
    logging.basicConfig(level=logging.INFO)
    r = classify_premarket()
    print(_json.dumps(r, indent=2, default=str))

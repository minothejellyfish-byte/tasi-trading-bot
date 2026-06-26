#!/usr/bin/env python3
"""
Market Regime Classifier for TASI Trading System.
Classifies each trading day as TRENDING / NEUTRAL / DEFENSIVE based on
TASI index momentum and oil price signals.

Pre-market: classify_premarket() — called from screener.py at ~09:50
Intraday:   classify_intraday()  — called from poller.py every 30 min
"""

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import requests
import yfinance as yf

# ─── Saudi Exchange TASI data helpers ────────────────────────────────────────

def get_tasi_data_saudi(csv_path='/tmp/tasi_latest.csv'):
    """Fetch TASI data from Saudi Exchange CSV (same-day data)."""
    if not Path(csv_path).exists():
        return None
    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return None
        return {
            'dates': [datetime.strptime(r['date'], '%Y/%m/%d') for r in rows],
            'open': [float(r['open']) for r in rows],
            'high': [float(r['high']) for r in rows],
            'low': [float(r['low']) for r in rows],
            'close': [float(r['close']) for r in rows],
            'volume': [int(r['volume']) for r in rows],
        }
    except Exception as e:
        log.warning(f"Failed to read Saudi Exchange TASI data: {e}")
        return None


def get_tasi_data_with_fallback():
    """Try Saudi Exchange first, fall back to WS tracked tasi_daily.json."""
    saudi_data = get_tasi_data_saudi()
    if saudi_data:
        log.info(f"Using Saudi Exchange TASI data ({len(saudi_data['close'])} days)")
        return saudi_data
    
    # Fallback to WS tracked data (tasi_daily.json)
    log.info("Saudi Exchange data unavailable, falling back to WS tracked tasi_daily.json")
    try:
        from pathlib import Path
        from datetime import datetime, timedelta
        
        tasi_file = Path("/home/mino/tasi-exec/tasi_daily.json")
        if tasi_file.exists():
            with open(tasi_file) as f:
                data = json.load(f)
            
            # Need at least a few days of data
            if len(data) >= 5:
                # Sort dates descending (newest first)
                sorted_dates = sorted(data.keys(), reverse=True)
                
                dates = []
                opens = []
                highs = []
                lows = []
                closes = []
                volumes = []
                
                for date_str in sorted_dates:
                    day_data = data[date_str]
                    if all(k in day_data for k in ["open", "high", "low", "close"]):
                        dates.append(datetime.strptime(date_str, "%Y-%m-%d"))
                        opens.append(float(day_data["open"]))
                        highs.append(float(day_data["high"]))
                        lows.append(float(day_data["low"]))
                        closes.append(float(day_data["close"]))
                        volumes.append(float(day_data.get("volume", 0)))
                
                if len(closes) >= 5:
                    log.info(f"Using WS tracked TASI data ({len(closes)} days)")
                    return {
                        'dates': dates,
                        'open': opens,
                        'high': highs,
                        'low': lows,
                        'close': closes,
                        'volume': volumes,
                    }
        
        log.warning("tasi_daily.json insufficient or not found")
    except Exception as e:
        log.warning(f"WS tracked fallback failed: {e}")
    return None

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
        # v4.7: Liquidity direction parameters
        "enable_liquidity":    True,   # Phase 3: enabled — liquidity direction now active
        "liquidity_entry_min": 1.1,    # Lower bar — momentum already present
        "liquidity_exit_confirm": 0.5, # Below = confirmed breakdown
        "liquidity_hold_min":  1.5,    # Above = hold despite breakdown
        # v4.7b: Spread filter parameters
        "enable_spread_filter": True,   # Phase 2: enabled — spread filter now active
        "max_spread_pct":      1.5,    # Lenient — momentum stocks often wider
        # v4.8: Position weight scaling parameters
        "enable_weight_scaling": True,
        "large_weight_threshold": 0.30,
        "small_weight_threshold": 0.15,
        "large_scale_factor": 0.75,
        "small_scale_factor": 1.25,
        # v4.9: Position upgrade redesign
        "enable_upgrade_redesign": True,
        # v4.10: Dynamic ratio allocation
        "enable_dynamic_allocation": True,
        "deployment_target": 0.95,  # Target 95% capital deployed
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
        # v4.7: Liquidity direction parameters
        "enable_liquidity":    True,   # Phase 3: enabled — liquidity direction now active
        "liquidity_entry_min": 1.2,    # Standard confirmation
        "liquidity_exit_confirm": 0.5,
        "liquidity_hold_min":  1.5,
        # v4.7b: Spread filter parameters
        "enable_spread_filter": True,   # Phase 2: enabled — spread filter now active
        "max_spread_pct":      1.0,    # Standard — normal market conditions
        # v4.8: Position weight scaling parameters
        "enable_weight_scaling": True,
        "large_weight_threshold": 0.30,
        "small_weight_threshold": 0.15,
        "large_scale_factor": 0.75,
        "small_scale_factor": 1.25,
        # v4.9: Position upgrade redesign
        "enable_upgrade_redesign": True,
        # v4.10: Dynamic ratio allocation
        "enable_dynamic_allocation": True,
        "deployment_target": 0.90,  # Target 90% capital deployed
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
        # v4.7: Liquidity direction parameters (strictest)
        "enable_liquidity":    True,   # Phase 3: enabled — strictest regime
        "liquidity_entry_min": 1.3,    # Higher bar — only enter with clear pressure
        "liquidity_exit_confirm": 0.6,  # Tighter — faster exit
        "liquidity_hold_min":  1.6,    # Need stronger confirmation to hold
        # v4.7b: Spread filter parameters
        "enable_spread_filter": True,   # Phase 2: enabled — spread filter now active
        "max_spread_pct":      0.5,    # Strict — only enter in liquid markets
        # v4.8: Position weight scaling parameters
        "enable_weight_scaling": True,
        "large_weight_threshold": 0.30,
        "small_weight_threshold": 0.15,
        "large_scale_factor": 0.70,  # Stricter in DEFENSIVE
        "small_scale_factor": 1.25,
        # v4.9: Position upgrade redesign
        "enable_upgrade_redesign": True,
        # v4.10: Dynamic ratio allocation
        "enable_dynamic_allocation": True,
        "deployment_target": 0.80,  # Target 80% capital deployed
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
    Uses Saudi Exchange data first, falls back to yfinance.
    Writes regime.json and returns the regime dict.
    """
    
    # ── Try Saudi Exchange first ───────────────────────────────────────────────
    tasi_data = get_tasi_data_with_fallback()
    
    if tasi_data is None or len(tasi_data['close']) < 5:
        log.warning("classify_premarket: insufficient TASI data — defaulting to NEUTRAL")
        result = dict(_NEUTRAL_DEFAULT)
        result["classified_at"] = datetime.now().isoformat()
        _write_regime_file(result)
        return result

    # ── Debug: show data source ────────────────────────────────────────────────
    is_saudi = get_tasi_data_saudi() is not None
    source = "Saudi Exchange" if is_saudi else "yfinance"
    print(f"[DEBUG] TASI source: {source} | rows: {len(tasi_data['close'])} | last 3 dates: {tasi_data['dates'][:3]}")
    print(f"[DEBUG] TASI last close: {tasi_data['close'][0]:.2f} | prev close: {tasi_data['close'][1]:.2f}")

    # ── Compute signals ───────────────────────────────────────────────────────
    closes = tasi_data['close']
    
    last_close  = float(closes[0])
    prev_close  = float(closes[1])
    sma5        = float(sum(closes[:5]) / 5) if len(closes) >= 5 else last_close
    sma10       = float(sum(closes[:10]) / 10) if len(closes) >= 10 else sma5

    tasi_momentum    = (last_close - sma5) / sma5 * 100
    tasi_yesterday   = (last_close - prev_close) / prev_close * 100 if prev_close else 0
    tasi_above_sma10 = last_close > sma10

    # 5-day return: close[0] vs close[5]
    if len(closes) >= 6:
        tasi_5d_return = float((closes[0] - closes[5]) / closes[5] * 100)
    else:
        tasi_5d_return = 0.0

    # Oil data (still from yfinance)
    oil_2d = 0.0
    try:
        oil_df = yf.download("BZ=F", period="5d", interval="1d",
                              progress=False, auto_adjust=True)
        if oil_df is not None and len(oil_df) >= 2:
            oil_df.columns = [c[0] if isinstance(c, tuple) else c for c in oil_df.columns]
            oil_close = oil_df["Close"]
            oil_2d = float((oil_close.iloc[-1] - oil_close.iloc[-2]) / oil_close.iloc[-2] * 100)
    except Exception as e:
        log.warning(f"Oil data fetch failed: {e}")

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

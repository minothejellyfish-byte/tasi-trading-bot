#!/usr/bin/env python3
"""
Pick Evaluator — v4.12
Runs every 30 min (10:10, 10:40, 11:10, 11:40, 12:10, 12:40, 13:10, 13:40, 14:10)
Re-evaluates existing picks and adds evaluator_score, evaluator_action, evaluator_note.
Overwrites picks.json with evaluation fields.

v4.12 Changes:
- Two-gate system: Gate 1 (Validation) + Gate 2 (Evaluate)
- WS data as primary price source
- Regime-aware thresholds
- Reads all pick files (premarket + mid-screens)
- Symmetric penalties
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np
import yfinance as yf
import pandas as pd

# ─── Config ──────────────────────────────────────────────────────────────────

BASE_DIR     = Path("/home/mino/tasi-exec")
PICKS_FILE   = BASE_DIR / "picks.json"
PICKS_1030   = BASE_DIR / "picks_1030.json"
PICKS_1200   = BASE_DIR / "picks_1200.json"
PICKS_1330   = BASE_DIR / "picks_1330.json"
LOG_FILE     = BASE_DIR / "evaluator.log"

RIYADH = datetime.now().astimezone().tzinfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ─── Regime Config ───────────────────────────────────────────────────────────

REGIME_THRESHOLDS = {
    "TRENDING": {
        "momentum": 0.05,
        "volume": 0.20,
        "boost": 30,
        "penalty": -30
    },
    "NEUTRAL": {
        "momentum": 0.03,
        "volume": 0.30,
        "boost": 25,
        "penalty": -25
    },
    "DEFENSIVE": {
        "momentum": 0.02,
        "volume": 0.40,
        "boost": 20,
        "penalty": -20
    }
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def load_picks_file(filepath):
    """Load picks from a single file."""
    if not filepath.exists():
        return []
    try:
        with open(filepath) as f:
            data = json.load(f)
        return data.get("picks", [])
    except Exception as e:
        log.error(f"Failed to load {filepath}: {e}")
        return []


def load_all_picks():
    """Load all picks from all files. Later screens overwrite earlier ones."""
    all_picks = {}
    for filepath in [PICKS_FILE, PICKS_1030, PICKS_1200, PICKS_1330]:
        picks = load_picks_file(filepath)
        for p in picks:
            sym = p.get("symbol", "")
            if sym:
                all_picks[sym] = p
    return list(all_picks.values())


def save_picks_atomic(picks):
    """Atomic write to picks.json."""
    import tempfile
    data = {
        "date": datetime.now().date().isoformat(),
        "mode": "evaluated",
        "evaluated_at": datetime.now().isoformat(),
        "picks": picks,
    }
    tmp = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json', dir=BASE_DIR)
    json.dump(data, tmp, indent=2, default=str)
    tmp.close()
    os.rename(tmp.name, PICKS_FILE)
    log.info(f"Saved {len(picks)} evaluated picks to {PICKS_FILE}")


def get_ws_price(symbol):
    """Fetch latest price from WS data."""
    date_str = datetime.now().date().isoformat()
    ws_file = BASE_DIR / f"ws_prices_{date_str}.jsonl"
    if not ws_file.exists():
        return None
    
    latest_price = None
    latest_ts = 0
    
    try:
        with open(ws_file) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("symbol") == symbol:
                        ts = entry.get("ts", 0)
                        if ts > latest_ts:
                            latest_ts = ts
                            latest_price = entry.get("price")
                except:
                    continue
    except Exception as e:
        log.debug(f"get_ws_price {symbol}: {e}")
    
    return latest_price


def fetch_data(symbol):
    """Fetch current price — WS primary, yfinance fallback."""
    base = symbol.replace(".SR", "")
    
    # Try WS first
    ws_price = get_ws_price(base)
    if ws_price is not None:
        return ws_price, None
    
    # Fallback to yfinance
    try:
        df = yf.download(symbol, period="1d", interval="5m", progress=False)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                price = float(df[("Close", symbol)].iloc[-1])
            else:
                price = float(df["Close"].iloc[-1])
            return price, df
    except Exception as e:
        log.debug(f"fetch_data {symbol}: {e}")
    
    return None, None


def classify_regime():
    """Get current market regime."""
    try:
        from market_regime import get_current_regime
        regime = get_current_regime()
        return regime.get("regime", "NEUTRAL")
    except Exception as e:
        log.warning(f"Could not classify regime: {e}")
        return "NEUTRAL"


def get_ws_metrics(symbol):
    """Read WS metrics from ws_prices jsonl file."""
    date_str = datetime.now().date().isoformat()
    ws_file = BASE_DIR / f"ws_prices_{date_str}.jsonl"
    
    if not ws_file.exists():
        return {}
    
    metrics = {
        "liquidity_ratio": 1.0,
        "spread_pct": 0.0,
        "net_flow": 0.0,
        "last_price": 0,
    }
    
    try:
        with open(ws_file) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("symbol") == symbol:
                        metrics["liquidity_ratio"] = entry.get("liquidity_ratio", 1.0)
                        metrics["spread_pct"] = entry.get("spread_pct", 0.0)
                        metrics["net_flow"] = entry.get("net_flow", 0.0)
                        metrics["last_price"] = entry.get("price", 0)
                except:
                    continue
    except Exception as e:
        log.debug(f"get_ws_metrics {symbol}: {e}")
    
    return metrics


# ─── Gate 1: Validation ──────────────────────────────────────────────────────

def gate1_validate(pick, regime_name):
    """
    Gate 1: Validate pick with regime-aware thresholds.
    Returns (pass: bool, adjusted_score: int, note: str)
    """
    symbol = pick.get("symbol", "")
    base = symbol.replace(".SR", "")
    original_score = pick.get("score", 0)
    
    # Get WS metrics
    ws = get_ws_metrics(base)
    liq = ws.get("liquidity_ratio", 1.0)
    
    # Get regime thresholds
    thresholds = REGIME_THRESHOLDS.get(regime_name, REGIME_THRESHOLDS["NEUTRAL"])
    
    # Calculate momentum from WS data if available
    momentum = 0.0
    if ws.get("last_price", 0) > 0:
        # Use change_pct from pick as momentum proxy
        pm = pick.get("pm_metrics", {})
        change_pct = pm.get("change_pct", 0)
        if change_pct is not None:
            momentum = change_pct
    
    # Apply regime-aware adjustments
    adjusted = original_score
    
    if momentum > thresholds["momentum"]:
        adjusted += thresholds["boost"]
        note = f"Momentum +{momentum:.2f}% > {thresholds['momentum']}% — boosted +{thresholds['boost']}"
    elif momentum < -thresholds["momentum"]:
        adjusted += thresholds["penalty"]
        note = f"Momentum {momentum:.2f}% < -{thresholds['momentum']}% — penalized {thresholds['penalty']}"
    else:
        note = f"Momentum {momentum:.2f}% within ±{thresholds['momentum']}% — no change"
    
    # Liquidity check
    if liq < 0.5:
        adjusted += -10
        note += f", liquidity {liq:.2f} < 0.5 — penalized -10"
    
    # Gate threshold
    if adjusted < 50:
        return False, adjusted, f"FAILED Gate 1: {note}, adjusted score {adjusted} < 50"
    
    return True, adjusted, f"PASSED Gate 1: {note}, adjusted score {adjusted}"


# ─── Gate 2: Evaluate ──────────────────────────────────────────────────────

def gate2_evaluate(pick, price):
    """
    Gate 2: Evaluate zone position and momentum.
    Returns (action, score, note)
    """
    symbol = pick.get("symbol", "")
    e_lo = pick.get("entry_low", 0)
    e_hi = pick.get("entry_high", 0)
    
    if not price or not e_lo or not e_hi:
        return "KEEP", 50, "No data available"
    
    if e_lo <= price <= e_hi:
        zone = "IN_ZONE"
    elif price > e_hi:
        zone = "ABOVE"
    else:
        zone = "BELOW"
    
    # Use change_pct as momentum proxy
    pm = pick.get("pm_metrics", {})
    change_pct = pm.get("change_pct", 0) or 0
    
    if zone == "IN_ZONE":
        if change_pct > 2.0:
            return "KEEP", 90, f"Valid: in zone, strong momentum +{change_pct:.1f}%"
        elif change_pct > 0:
            return "KEEP", 75, f"Stable: in zone, positive momentum +{change_pct:.1f}%"
        elif change_pct > -2.0:
            return "STALE", 50, f"Stale: in zone but fading ({change_pct:.1f}%)"
        else:
            return "SCRATCH", 20, f"Falling: in zone, negative momentum {change_pct:.1f}%"
    
    elif zone == "ABOVE":
        if change_pct > 3.0:
            return "TRENDING", 70, f"Strong: above zone +{change_pct:.1f}% — don't chase"
        elif change_pct > 0:
            return "TRENDING", 60, f"Rising: above zone +{change_pct:.1f}% — wait for pullback"
        else:
            return "STALE", 40, f"Saturated: above zone, fading momentum ({change_pct:.1f}%)"
    
    else:  # BELOW
        if change_pct > 1.0:
            return "RECOVERING", 65, f"Recovering: below zone but rising +{change_pct:.1f}%"
        elif change_pct > -1.0:
            return "RECOVERING", 55, f"Bounce: below zone, weak recovery ({change_pct:.1f}%)"
        else:
            return "SCRATCH", 20, f"Falling: below zone, negative {change_pct:.1f}%"


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now()
    log.info(f"Evaluator v4.12 started at {now.strftime('%Y-%m-%d %H:%M')}")
    
    # Get current regime
    regime = classify_regime()
    log.info(f"Market regime: {regime}")
    
    # Load all picks
    picks = load_all_picks()
    if not picks:
        log.info("No picks to evaluate")
        return
    
    log.info(f"Evaluating {len(picks)} picks...")
    
    evaluated = []
    for pick in picks:
        symbol = pick.get("symbol", "")
        if not symbol:
            continue
        
        log.info(f"\n--- Evaluating {symbol} ---")
        
        # Gate 1: Validation
        pass_gate1, adjusted_score, gate1_note = gate1_validate(pick, regime)
        log.info(f"  Gate 1: {gate1_note}")
        
        if not pass_gate1:
            # SCRATCH and remove
            pick["evaluator_action"] = "SCRATCH"
            pick["evaluator_score"] = 20
            pick["evaluator_note"] = gate1_note
            pick["evaluator_time"] = datetime.now().isoformat()
            pick["evaluator_price"] = None
            log.info(f"  → SCRATCHED (Gate 1 failed)")
            continue
        
        # Gate 2: Evaluate
        price, _ = fetch_data(symbol)
        action, score, note = gate2_evaluate(pick, price)
        
        pick["adjusted_score"] = adjusted_score
        pick["evaluator_score"] = score
        pick["evaluator_action"] = action
        pick["evaluator_note"] = note
        pick["evaluator_time"] = datetime.now().isoformat()
        pick["evaluator_price"] = price
        
        log.info(f"  Gate 2: {action} (score={score}) — {note}")
        evaluated.append(pick)
    
    # Save back
    save_picks_atomic(evaluated)
    log.info(f"\nEvaluation complete: {len(evaluated)} picks kept, {len(picks) - len(evaluated)} scratched")


if __name__ == "__main__":
    main()

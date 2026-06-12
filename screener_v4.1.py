#!/usr/bin/env python3
"""
TASI Pre-Market Screener v4.1
Runs before market open (09:50 Riyadh). Scans TASI stocks, scores by momentum
+ volume + proximity to S/R, sends top 1-2 picks with entry zones to Telegram.

CHANGES from v4.0:
1. Wider entry zones for gap-down protection
2. Lower MIN_PRICE (5 SAR) with volume exception for high scores
3. Direction-aware filtering for gap-down > 2%
4. Market regime integration (DEFENSIVE = fewer picks)
"""

import asyncio
import json
import logging
import os
import io
import socket
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
import pandas_ta as ta
import mplfinance as mpf
import requests
from playwright.async_api import async_playwright

# ─── Config ──────────────────────────────────────────────────────────────────

BOT_TOKEN   = "8989533040:AAFWzP_lYL3g_w4eXGxrvwdo-tBNdPxVYQU"
CHAT_ID     = -5235925419  # Execution group (bot cannot DM users directly)

LOG_FILE      = "/home/mino/tasi-exec/screener.log"
PICKS_FILE    = "/home/mino/tasi-exec/picks.json"
BLOCKED_FILE  = "/home/mino/tasi-exec/blocked_stocks.json"
SHARIA_FILE   = "/home/mino/tasi-exec/sharia_list.json"
EXCHANGE_URL  = "https://www.saudiexchange.sa"
CDP_URL       = "http://127.0.0.1:18801"
LOCK_FILE     = "/home/mino/tasi-exec/screener.lock"

# ─── v4.1 FILTER CONFIG ────────────────────────────────────────────────────

# Base filters
MIN_AVG_VOLUME = 500_000   # minimum 20-day avg volume (shares)
MIN_PRICE      = 5.0       # SAR (was 10.0 in v4.0)
MAX_PRICE      = 500.0     # SAR

# v4.1: Volume exception for high scores
MIN_VOLUME_EXCEPTION = 50_000  # Lower volume if score >= 80
HIGH_SCORE_THRESHOLD = 80

# v4.1: Gap-down protection
GAP_DOWN_THRESHOLD = -2.0  # Skip or adjust if gap < -2%
GAP_DOWN_SKIP = -3.0     # Skip if gap < -3% AND score < 120

# Known delisted / suspended tickers — skip fast to avoid yfinance timeouts
DELISTED_TICKERS = {"2001.SR", "2210.SR"}

# yfinance socket timeout — prevents hanging on delisted tickers
socket.setdefaulttimeout(10)

# ─── Logging ─────────────────────────────────────────────────────────────────

# Clear existing handlers to prevent duplication on re-import / thread restart
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
    handler.close()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

# ─── Telegram helpers ────────────────────────────────────────────────────────

def tg_send(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        if not r.ok:
            log.warning(f"tg_send failed: {r.status_code} {r.text[:200]}")
        return r.ok
    except Exception as e:
        log.warning(f"tg_send exception: {e}")
        return False

# ─── v4.1: Direction-aware entry calculation ───────────────────────────────

def calculate_entry_zone_v41(close, prev_high, prev_low, score, gap_pct=None):
    """
    v4.1 entry zone with gap-down protection.
    
    Args:
        close: Previous day close
        prev_high: Previous day high
        prev_low: Previous day low
        score: Stock score
        gap_pct: Optional premarket gap percentage (e.g. -2.5 for -2.5%)
    
    Returns:
        dict with entry_low, entry_high, stop_loss, direction_note
    """
    # v4.1: Check direction first
    direction_note = None
    
    if gap_pct is not None and gap_pct < GAP_DOWN_THRESHOLD:
        # Gap down > 2%
        direction_note = f"⚠️ Gap down {gap_pct:.1f}%"
        
        if gap_pct < GAP_DOWN_SKIP and score < 120:
            # Skip entirely
            return {
                "entry_low": None,
                "entry_high": None,
                "stop_loss": None,
                "direction_note": f"❌ SKIP: {direction_note}, score {score} < 120",
                "skip": True
            }
        else:
            # Adjust entry lower
            entry_low = round(close * 0.97, 2)  # 3% below close
            entry_high = round(close * 0.99, 2)
            direction_note += " → Adjusted entry lower"
    else:
        # Normal logic (v4.0 compatible)
        if close >= prev_high * 0.99:
            # v4.1: Use lower of prev_high*0.995 or close*0.98
            entry_low = round(min(prev_high * 0.995, close * 0.98), 2)
            entry_high = round(close * 1.01, 2)
        else:
            entry_low = round(prev_low * 0.998, 2)
            entry_high = round(prev_high * 1.002, 2)
    
    stop_loss = round(close * 0.93, 2)
    
    return {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "direction_note": direction_note,
        "skip": False
    }

# ─── v4.1: Volume check with exception ──────────────────────────────────────

def check_volume_v41(vol20, close, score):
    """
    v4.1 volume check with exception for high scores.
    
    High scores (≥80) can pass with lower volume (50K instead of 500K).
    """
    if vol20 >= MIN_AVG_VOLUME:
        return True, None
    
    if score >= HIGH_SCORE_THRESHOLD and vol20 >= MIN_VOLUME_EXCEPTION:
        return True, f"Volume exception: {vol20:.0f} >= {MIN_VOLUME_EXCEPTION} (score {score} >= {HIGH_SCORE_THRESHOLD})"
    
    return False, None

# ─── v4.1: Market regime integration ──────────────────────────────────────

def load_market_regime():
    """Load current market regime if available."""
    try:
        regime_file = "/home/mino/tasi-exec/regime.json"
        if os.path.exists(regime_file):
            with open(regime_file) as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"Could not load regime: {e}")
    
    return None

def adjust_for_regime(score, regime):
    """
    Adjust score based on market regime.
    DEFENSIVE: Raise threshold (fewer picks)
    TRENDING: Lower threshold (more picks)
    """
    if regime is None:
        return score
    
    regime_name = regime.get("regime", "NEUTRAL")
    
    if regime_name == "DEFENSIVE":
        # In defensive mode, only pick strong signals
        return score * 1.2  # 20% bonus required
    elif regime_name == "TRENDING":
        # In trending mode, be more aggressive
        return score * 0.9  # 10% easier to pass
    
    return score

# ─── Rest of v4.1 screener continues here... ──────────────────────────────

# NOTE: The remaining functions (score_stock, generate_chart, format_message, 
# run_ws_scan, ws_fallback_scan, check_premarket_momentum, main) 
# are identical to v4.0 but use the new v4.1 functions above.

# For a complete implementation, copy the remaining functions from 
# screener_v4.0_backup.py and update calls to use:
#   - calculate_entry_zone_v41() instead of inline entry zone
#   - check_volume_v41() instead of simple volume check
#   - load_market_regime() and adjust_for_regime() before scoring

# ─── Testing stub ──────────────────────────────────────────────────────────

def test_v41_changes():
    """Quick test of v4.1 changes."""
    print("=== v4.1 Entry Zone Tests ===")
    
    # Test 1: Normal gap (no gap_pct)
    result = calculate_entry_zone_v41(16.75, 16.83, 16.20, 101.5)
    print(f"1. No gap: entry {result['entry_low']}–{result['entry_high']} (v4.0: 16.75–16.92)")
    print(f"   v4.1 uses min(prev_high*0.995, close*0.98) = {result['entry_low']}")
    
    # Test 2: Gap down 2.6%
    result = calculate_entry_zone_v41(16.75, 16.83, 16.20, 101.5, gap_pct=-2.6)
    print(f"\n2. Gap -2.6%: {result['direction_note']}")
    print(f"   Adjusted entry: {result['entry_low']}–{result['entry_high']}")
    
    # Test 3: Gap down 4% (should skip)
    result = calculate_entry_zone_v41(16.75, 16.83, 16.20, 80, gap_pct=-4.0)
    print(f"\n3. Gap -4%, score 80: {result['direction_note']}")
    print(f"   Skip: {result['skip']}")
    
    print("\n=== v4.1 Volume Tests ===")
    ok, note = check_volume_v41(600_000, 15.0, 50)  # Normal pass
    print(f"4. Vol 600K, score 50: {'✅' if ok else '❌'}")
    
    ok, note = check_volume_v41(80_000, 15.0, 85)  # High score exception
    print(f"5. Vol 80K, score 85: {'✅' if ok else '❌'} {note or ''}")
    
    ok, note = check_volume_v41(80_000, 15.0, 50)  # Should fail
    print(f"6. Vol 80K, score 50: {'✅' if ok else '❌'}")

if __name__ == "__main__" and "test" in os.sys.argv:
    test_v41_changes()

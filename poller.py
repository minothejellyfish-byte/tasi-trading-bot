#!/usr/bin/env python3
"""
TASI Price Poller
Monitors screener picks for VWAP reclaim / breakout entry signals and open
positions for hard stop, trailing stop, and 14:45 hard-close alerts.
Runs every 5 minutes. Self-exits after 15:30 Riyadh.
Start via cron at 10:00 Sun-Thu.
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time as time_mod
from datetime import datetime, time
from pathlib import Path
import socket

# ─── Config (must be defined before session management import) ──────────────

BASE_DIR       = "/home/mino/tasi-exec"
PICKS_FILE     = f"{BASE_DIR}/picks.json"
POSITIONS_FILE = f"{BASE_DIR}/positions.json"
# ORDERS_FILE moved to order_helpers.py (v4.4)
LOG_FILE       = f"{BASE_DIR}/poller.log"

CDP_URL        = "http://127.0.0.1:18801"

# Market regime - loaded at startup, refreshed every 30 min intraday
from market_regime import get_current_regime, classify_intraday

# Capital tracking - dynamic from Derayah
from capital_tracker import load_capital, CAPITAL_FILE

# Order lifecycle helpers (v4.4) — shared with bot.py and bookkeeper.py
from order_helpers import (
    load_orders, save_orders, write_order_initiated, effective_holdings,
    trigger_bookkeeper_sync, get_outstanding_orders, get_booked_capital,
    get_status_name, STATUS_INITIATED, STATUS_PLACED, STATUS_PARTIAL,
    STATUS_FILLED, STATUS_CANCELLED, STATUS_REJECTED, STATUS_EXPIRED,
    TERMINAL_STATUSES, ORDERS_FILE, STATUS_NAMES,
    TRIGGER_PICK_ENTRY, TRIGGER_CYCLE_RECYCLE, TRIGGER_CYCLE_SWITCH,
    TRIGGER_HARD_STOP, TRIGGER_TRAILING_STOP, TRIGGER_TIME_STOP,
    TRIGGER_VWAP_BREAKDOWN, TRIGGER_VWAP_RECLAIM,
    TRIGGER_TARGET_REACHED, TRIGGER_TIER_1, TRIGGER_TIER_2, TRIGGER_TIER_3,
    TRIGGER_POSITION_UPGRADE, TRIGGER_SCRATCH_SELL, TRIGGER_MANUAL_COMMAND,
    TRIGGER_HARD_CLOSE, TRIGGER_BLOCK_REMOVAL, TRIGGER_UNKNOWN,
)

# Derayah API for direct order execution
import derayah_api

# Session management - validate before trades
sys.path.insert(0, BASE_DIR)
try:
    from bot_commands import validate_session
    SESSION_ENABLED = True
except ImportError as e:
    logging.warning(f"Session validation not available: {e}")
    SESSION_ENABLED = False
    validate_session = None

import pytz
import requests
import yfinance as yf
import pandas as pd
from playwright.async_api import async_playwright

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID    = -5235925419         # TASI Execution group - all alerts go here
EXEC_GROUP = -5235925419         # TASI Execution group - bot executes commands sent here
OWNER_ID   = 5529987063          # A A's personal Telegram chat_id (for DM alerts)
RIYADH     = pytz.timezone("Asia/Riyadh")

TC_URL         = "tickerchart"   # Derayah Trade tab (TickerChart - live)
TC_FALLBACK_URLS = ["derayah.tickerchart.net", "tickerchart.net", "newonline.derayah.com"]  # fallback patterns

# ─── Helper functions ─────────────────────────────────────────────────────────

def is_port_available(host, port):
    """Check if a port is available for connection."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result != 0  # True if port is available (not connected)
    except Exception:
        return False

def wait_for_port(host, port, timeout=30):
    """Wait for a port to become available with timeout."""
    start_time = time_mod.time()
    while time_mod.time() - start_time < timeout:
        if not is_port_available(host, port):
            return True  # Port is now in use (available for connection)
        time_mod.sleep(1)
    return False

# WebSocket price cache - populated by background CDP listener thread.
# Keys are base symbols (no .SR), values: {price, ts, change, pchange, vwap, volume}
_ws_price_cache: dict = {}
_ws_cache_lock = threading.Lock()
_ws_listener_thread: threading.Thread | None = None

# ─── Incremental WebSocket VWAP State ──────────────────────────────────────
# Per-symbol cumulative state for real-time VWAP calculation.
# Structure: {symbol: {"cum_pv": float, "cum_weight": float, "ticks": int, "reset_time": float}}
# Reset at market open (10:00) to avoid carryover from previous session.
_incremental_vwap_state: dict = {}
_INCREMENTAL_VWAP_MAX_AGE = 300.0  # seconds - max age to trust cached VWAP


def _get_weighted_price(price: float, change: float, real: bool) -> tuple[float, float]:
    """
    Calculate typical price and weight for incremental VWAP.
    
    Weight formula: real(2x) * change(1+change*30)
    - real=true: weight ×2 (actual market tick)
    - real=false: weight ×1 (interpolated/filled)
    - Larger price changes → higher weight (more significant trade)
    
    Returns: (typical_price, weight)
    """
    # Typical price is just the trade price (no H/L available in tick)
    tp = float(price)
    
    # Base weight from real flag
    real_multiplier = 2.0 if real else 1.0
    
    # Change multiplier: larger moves = more significant
    change_multiplier = 1.0 + abs(float(change)) * 30.0
    
    weight = real_multiplier * max(change_multiplier, 0.1)
    
    return tp, weight


def update_ws_vwap(symbol: str, price: float, change: float, real: bool, volume: float = 0.0) -> float | None:
    """
    Update incremental VWAP for a symbol from a websocket tick.
    
    Called on every websocket tick. Maintains cumulative state per symbol.
    Resets state if market just opened (before 10:00) or if stale (>6h old).
    
    Returns: Current VWAP or None if insufficient data.
    """
    global _incremental_vwap_state
    
    now = time_mod.time()
    
    # Initialize or reset state
    if symbol not in _incremental_vwap_state:
        _incremental_vwap_state[symbol] = {
            "cum_pv": 0.0,
            "cum_weight": 0.0,
            "ticks": 0,
            "last_update": now,
            "reset_time": now,
        }
    
    state = _incremental_vwap_state[symbol]
    
    # Reset if state is stale (>6 hours - new trading day)
    if now - state.get("reset_time", 0) > 21600:  # 6 hours
        state = {
            "cum_pv": 0.0,
            "cum_weight": 0.0,
            "ticks": 0,
            "last_update": now,
            "reset_time": now,
        }
        _incremental_vwap_state[symbol] = state
    
    # Calculate typical price and weight
    tp, weight = _get_weighted_price(price, change, real)
    
    # Update cumulative state
    state["cum_pv"] += tp * weight
    state["cum_weight"] += weight
    state["ticks"] += 1
    state["last_update"] = now
    
    # Calculate current VWAP
    if state["cum_weight"] > 0:
        vwap = state["cum_pv"] / state["cum_weight"]
        return vwap
    
    return None


def get_ws_vwap(symbol: str, max_age_s: float = _INCREMENTAL_VWAP_MAX_AGE) -> float | None:
    """
    Get cached incremental VWAP for a symbol if recent enough.
    
    Returns: VWAP value or None if stale/missing.
    """
    global _incremental_vwap_state
    
    state = _incremental_vwap_state.get(symbol)
    if not state:
        return None
    
    now = time_mod.time()
    if now - state.get("last_update", 0) > max_age_s:
        return None  # Stale
    
    if state.get("cum_weight", 0) <= 0:
        return None
    
    return state["cum_pv"] / state["cum_weight"]


# Trade execution lock and time guard to prevent race conditions
_trade_lock = threading.Lock()          # Global lock for trade execution
_last_trade_time: dict = {}             # {symbol: timestamp} - prevent re-trade within 30s
_MIN_TRADE_INTERVAL = 30                # Minimum seconds between trades for same symbol

FAST_INTERVAL   = 10           # seconds - position state watch (no network)
SLOW_INTERVAL   = 300          # seconds - price fetch + entry signals (yfinance)
# NOTE: Target/stop parameters now loaded from regime.json dynamically
WIN_PCT         = 2.0          # fallback default
HARD_STOP_PCT   = 0.07         # fallback default
TRAIL_TRIGGER   = 0.02         # fallback default
TRAIL_STOP_PCT  = 0.03         # fallback default
TIME_STOP_PCT   = 0.01         # fallback default
TIME_STOP_MINS  = 30           # fallback default

# ─── Regime-aware switching thresholds ─────────────────────────────────────────
# Position upgrade: sell OPEN position to buy better pick (conservative)
# Cycle switch: skip rebuy after win to buy better pick (aggressive)
POSITION_UPGRADE_THRESHOLDS = {
    "TRENDING":  1.4,  # 40% better - stick with strong momentum
    "NEUTRAL":   1.3,  # 30% better - balanced
    "DEFENSIVE": 1.2,  # 20% better - cut losers faster
}
CYCLE_SWITCH_THRESHOLDS = {
    "TRENDING":  1.2,  # 20% better - still selective after win
    "NEUTRAL":   1.15, # 15% better - balanced
    "DEFENSIVE": 1.1,  # 10% better - quick to rotate
}

# ─── Regime change tracking for dynamic exit updates ────────────────────────────
_regime_history: list[tuple[str, datetime]] = []  # [(regime, timestamp), ...]
_regime_confirmed: str | None = None  # Confirmed regime after 60-min hold
REGIME_CONFIRM_MINS = 60  # minutes to confirm regime change

HARD_CLOSE_START = time(14, 30)  # v4.1: Start watching for VWAP exit
HARD_CLOSE_END   = time(14, 50)  # v4.1: Final market sell deadline
HARD_CLOSE_TIME  = time(14, 50)   # v4.1: Final deadline - must match HARD_CLOSE_END
ENTRY_CUTOFF     = time(13, 30)  # no new entries after this - too close to hard close
MARKET_OPEN      = time(10, 0)
MARKET_CLOSE     = time(15, 30)

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
    ],
)
log = logging.getLogger(__name__)

# ─── Telegram ────────────────────────────────────────────────────────────────

def tg_send(text: str, chat_id: int = None, retries: int = 3):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    target = chat_id or CHAT_ID
    for attempt in range(retries):
        try:
            r = requests.post(
                url,
                json={"chat_id": target, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            r.raise_for_status()
            return
        except Exception as e:
            log.error(f"tg_send failed (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time_mod.sleep(2)


def _dm_owner(text: str):
    """DM the owner (Amin) directly. Used for critical alerts that shouldn't be
    lost in group noise — e.g. auto-buy failures, session-blocked trades, etc.
    Falls back to logging if Telegram is unreachable."""
    try:
        tg_send(text, chat_id=OWNER_ID)
    except Exception as e:
        log.error(f"_dm_owner failed: {e}")


async def refresh_derayah_and_update_files():
    """Refresh Derayah dashboard and update capital.json with actual values."""
    try:
        from playwright.async_api import async_playwright
        import re

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp('http://127.0.0.1:18801')

            contexts = browser.contexts
            page = None
            for ctx in contexts:
                for p_page in ctx.pages:
                    if 'newonline.derayah.com' in p_page.url:
                        page = p_page
                        break

            if not page:
                log.warning("refresh_derayah: Derayah page not found")
                return False

            # REFRESH the page
            try:
                await page.reload(wait_until='domcontentloaded', timeout=15000)
            except Exception as e:
                log.warning(f"refresh_derayah: reload error: {e}")
            await page.wait_for_timeout(3000)

            # Navigate to portfolio
            try:
                await page.goto('https://newonline.derayah.com/#/layout/trading-portfolio', wait_until='domcontentloaded', timeout=15000)
            except Exception as e:
                log.warning(f"refresh_derayah: navigation error: {e}")
            await page.wait_for_timeout(5000)

            # Scrape values
            text = await page.inner_text('body')
            lines = text.split('\n')

            result = {}

            for i, line in enumerate(lines):
                if 'Grand Total' in line and i+1 < len(lines):
                    match = re.search(r'([\d,]+\.?\d*)\s*SAR', lines[i+1])
                    if match:
                        result['grand_total'] = float(match.group(1).replace(',', ''))

                if 'Money Transfer' in line and i+1 < len(lines):
                    match = re.search(r'([\d,]+\.?\d*)\s*SAR', lines[i+1])
                    if match:
                        result['money_transfer'] = float(match.group(1).replace(',', ''))

                if 'Securities Value' in line:
                    for offset in [2, 1, -1, -2]:
                        check_idx = i - offset
                        if check_idx >= 0 and check_idx < len(lines):
                            match = re.search(r'([\d,]+\.?\d*)\s*SAR', lines[check_idx])
                            if match:
                                val = float(match.group(1).replace(',', ''))
                                if val > 0:
                                    result['securities_value'] = val
                                    break
                    if 'securities_value' not in result and i+1 < len(lines):
                        match = re.search(r'([\d,]+\.?\d*)\s*SAR', lines[i+1])
                        if match:
                            val = float(match.group(1).replace(',', ''))
                            if val > 0:
                                result['securities_value'] = val

            await browser.close()

            if result:
                # Update capital.json with scraped values
                try:
                    with open(CAPITAL_FILE, 'r') as f:
                        cap = json.load(f)

                    cap['available_capital'] = result.get('money_transfer', cap.get('available_capital', 0))
                    cap['grand_total'] = result.get('grand_total', cap.get('grand_total', 1000.66))
                    cap['invested'] = result.get('securities_value', cap.get('invested', 0))
                    cap['updated_at'] = datetime.now(RIYADH).isoformat()
                    cap['source'] = 'derayah-refresh-after-trade'

                    with open(CAPITAL_FILE, 'w') as f:
                        json.dump(cap, f, indent=2)

                    log.info(f"refresh_derayah: Updated capital.json - Grand Total: {result.get('grand_total')}, Available: {result.get('money_transfer')}, Invested: {result.get('securities_value')}")
                    return True
                except Exception as e:
                    log.error(f"refresh_derayah: Failed to update capital.json: {e}")
                    return False
            else:
                log.warning("refresh_derayah: No values scraped")
                return False
    except Exception as e:
        log.error(f"refresh_derayah_and_update_files error: {e}")
        return False


async def sync_positions_from_derayah():
    """Sync positions from Derayah API to local positions.json immediately after orders."""
    try:
        # Get token from derayah_tokens.json (refresh cron updates this file)
        try:
            with open("/home/mino/tasi-exec/derayah_tokens.json") as f:
                tokens = json.load(f)
            # Prefer TC_DERAYAH (TradingAPI scope) over Derayah_accesstoken (dashboard/idspark scope)
            # Derayah_accesstoken fails 401 on /trading/* endpoints; TC_DERAYAH has TradingAPI.All scope
            token = tokens.get('TC_DERAYAH', '') or tokens.get('Derayah_accesstoken', '')
        except Exception as e:
            log.warning(f"sync_positions: Could not read tokens file: {e}")
            return

        if not token:
            log.warning("sync_positions: No token in tokens file")
            return

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0'
        }

        # Get positions from Derayah
        r = requests.post(
            'https://api.derayah.com/trading/UserPosition/ListPositions',
            headers=headers,
            json={
                'currencyCode': 1,
                'exchangeCodes': [98, 99],
                'portfolio': 2063853
            },
            timeout=10
        )
        resp_data = r.json()

        if resp_data.get('isSuccess'):
            positions_data = resp_data.get('data', {}).get('tradingAccountPositionInfoList', [])

            if positions_data:
                # Build positions dict
                positions = {}
                total_cost = 0
                for p in positions_data:
                    sym = p.get('symbol')
                    qty = p.get('quantity', 0)
                    cost = p.get('cost', 0)
                    avg_price = cost / qty if qty > 0 else 0
                    total_cost += cost

                    positions[sym] = {
                        "symbol": sym,
                        "entry_price": round(avg_price, 2),
                        "qty": qty,
                        "entry_time": datetime.now(RIYADH).isoformat(),
                        "peak_price": round(avg_price, 2),
                        "closed": False,
                        "price_source": "derayah-api",
                        "signal": "auto",
                        "cost": cost
                    }

                # Save to positions.json
                save_positions(positions)
                log.info(f"sync_positions: Synced {len(positions)} positions from Derayah")

                # Update capital
                try:
                    with open(CAPITAL_FILE) as f:
                        cap = json.load(f)
                    cap["available_capital"] = max(0, cap.get("available_capital", 1000.66) - total_cost)
                    cap["updated_at"] = datetime.now(RIYADH).isoformat()
                    with open(CAPITAL_FILE, "w") as f:
                        json.dump(cap, f, indent=2)
                    log.info(f"sync_positions: Capital updated, remaining={cap['available_capital']:.2f}")
                except Exception as cap_err:
                    log.error(f"sync_positions: Failed to update capital: {cap_err}")

                # Notify
                tg_send(f"📊 <b>Positions Synced</b>\nSynced {len(positions)} positions from Derayah:\n" +
                       "\n".join([f"  {s}: {p['qty']} @ {p['entry_price']:.2f}" for s, p in positions.items()]))
            else:
                log.info("sync_positions: No open positions found")
        else:
            log.warning(f"sync_positions failed: {resp_data.get('message')}")
    except Exception as e:
        log.error(f"sync_positions error: {e}")


async def _execute_order_direct(action: str, symbol: str, qty: int, price: float = None,
                                  trigger_basis: str = TRIGGER_UNKNOWN,
                                  trigger_detail: str = "") -> dict:
    """
    Execute order directly via Derayah API.
    Phase 3: Validates session before executing trades.
    Phase 4.4: Tracks order lifecycle via orders.json.
    Falls back to Telegram command if API fails.
    Returns: {"success": bool, "message": str, "order_id": str|None}
    """
    # ─── Phase 3: Session validation before trades ──────────────────
    if SESSION_ENABLED and validate_session:
        is_valid, session_msg = validate_session()
        if not is_valid:
            log.warning(f"Trade BLOCKED: {action} {symbol} - {session_msg}")
            return {"success": False, "message": f"🚫 {session_msg}", "order_id": None}

    base = symbol.replace(".SR", "")

    # ─── Phase 4.4: Double-sell pre-check (SELL only) ──────────────
    if action == "SELL":
        effective = effective_holdings(symbol)
        if qty > effective:
            msg = (f"❌ BLOCKED: cannot sell {qty}×{base} — effective holdings only "
                   f"{effective} (filled - outstanding_sell + outstanding_buy)")
            log.warning(msg)
            tg_send(msg, chat_id=EXEC_GROUP)
            _dm_owner(f"🚨 Sell BLOCKED for {base}\nqty={qty}, effective_holdings={effective}")
            return {"success": False, "message": msg, "order_id": None}

    try:
        side = derayah_api.SIDE_BUY if action == "BUY" else derayah_api.SIDE_SELL
        order_type = derayah_api.TYPE_MARKET if price is None else derayah_api.TYPE_LIMIT

        # Execute the order
        # Format price to 2 decimal places for Derayah
        formatted_price = round(price, 2) if price else 0.0
        resp = await derayah_api.place_order(
            symbol=base,  # base already has .SR stripped
            side=side,
            qty=qty,
            order_type=order_type,
            price=formatted_price
        )

        if resp.get("isSuccess"):
            order_id = (resp.get("data") or {}).get("orderId", "?")
            # ─── Phase 4.4: Write INITIATED to orders.json ─────────────
            order_type_label = "MARKET" if order_type == derayah_api.TYPE_MARKET else "LIMIT"
            write_order_initiated(
                order_id=order_id,
                action=action,
                symbol=symbol,
                qty=qty,
                price=formatted_price,
                order_type=order_type_label,
                initiated_by="auto_buy" if action == "BUY" else "auto_sell",
                trigger_basis=trigger_basis,
                trigger_detail=trigger_detail,
            )
            # ─── Trigger bookkeeper sync (same pattern as bot.py:429) ──
            trigger_bookkeeper_sync()

            # ADD POSITION to positions.json
            if action == "BUY":
                try:
                    positions = load_positions()

                    # Calculate commission (0.05%) and VAT (15% on commission)
                    trade_value = price * qty if price else 0
                    commission = trade_value * 0.0005  # 0.05%
                    vat = commission * 0.15  # 15% of commission
                    total_cost = trade_value + commission + vat

                    positions[base] = {
                        "symbol": base,
                        "entry_price": price or 0.0,
                        "qty": qty,
                        "entry_time": datetime.now(RIYADH).isoformat(),
                        "peak_price": price or 0.0,
                        "closed": False,
                        "price_source": "derayah-api",
                        "signal": "auto",
                        "order_id": order_id,
                        "commission": round(commission, 2),
                        "vat": round(vat, 2),
                        "total_cost": round(total_cost, 2)
                    }
                    save_positions(positions)
                    log.info(f"Position added: {base} {qty}@{price} orderId={order_id} (commission: {commission:.2f} SAR, VAT: {vat:.2f} SAR)")
                    # Update capital
                    try:
                        with open(CAPITAL_FILE) as f:
                            cap = json.load(f)
                        cap["available_capital"] = max(0, cap.get("available_capital", 0) - total_cost)
                        cap["updated_at"] = datetime.now(RIYADH).isoformat()
                        with open(CAPITAL_FILE, "w") as f:
                            json.dump(cap, f, indent=2)
                        log.info(f"Capital updated: -{total_cost:.2f} SAR (trade: {trade_value:.2f} + commission: {commission:.2f} + VAT: {vat:.2f}), remaining={cap['available_capital']:.2f}")
                    except Exception as cap_err:
                        log.error(f"Failed to update capital: {cap_err}")
                except Exception as e:
                    log.error(f"Failed to save position: {e}")
            return {
                "success": True,
                "message": f"✅ {action} {qty} × {base} @ {'Market' if price is None else price} - orderId={order_id}",
                "order_id": order_id
            }
        else:
            err = resp.get("message") or resp.get("errorMessage") or str(resp)
            # If order failed due to duplicate or existing position, sync from Derayah
            if "ALREADY" in str(err).upper() or "BLOCKING" in str(err).upper():
                log.warning(f"Order may exist - syncing positions from Derayah")
                try:
                    asyncio.ensure_future(sync_positions_from_derayah())
                except Exception as sync_err:
                    log.error(f"Sync failed: {sync_err}")
            return {
                "success": False,
                "message": f"❌ Order rejected: {err}",
                "order_id": None
            }
    except Exception as e:
        log.error(f"Direct order execution failed: {e}")
        return {
            "success": False,
            "message": f"❌ Direct execution failed: {e}",
            "order_id": None
        }


def auto_sell(symbol: str, qty, reason: str,
              trigger_basis: str = TRIGGER_UNKNOWN,
              trigger_detail: str = ""):
    base = symbol.replace(".SR", "")

    # GUARD: Prevent re-sell within 30 seconds
    now = time_mod.time()
    if base in _last_trade_time and (now - _last_trade_time[base]) < _MIN_TRADE_INTERVAL:
        log.warning(f"auto_sell BLOCKED for {base}: traded {_last_trade_time[base]:.0f}s ago, min interval={_MIN_TRADE_INTERVAL}s")
        return False

    with _trade_lock:
        # Re-check under lock (double-check pattern)
        if base in _last_trade_time and (time_mod.time() - _last_trade_time[base]) < _MIN_TRADE_INTERVAL:
            log.warning(f"auto_sell DOUBLE-CHECK BLOCKED for {base}")
            return False

        _last_trade_time[base] = time_mod.time()

    # Execute sell synchronously
    try:
        result = asyncio.run(_execute_order_direct(
            "SELL", symbol, qty,
            trigger_basis=trigger_basis,
            trigger_detail=trigger_detail or reason
        ))

        # CRITICAL: Verify order success BEFORE updating any state
        if not result.get('success'):
            log.error(f"auto_sell FAILED for {base}: {result.get('message')}")
            tg_send(f"❌ Sell FAILED for {base}: {result.get('message')}", chat_id=EXEC_GROUP)
            return False

        # Order confirmed successful - now update state
        tg_send(f"✅ {base} SELL order placed successfully", chat_id=EXEC_GROUP)
        tg_send(f"🤖 Selling {base} {qty} shares - {result['message']}\n{reason}")

        # Update position to closed AND sync capital
        if result.get('success'):
            try:
                positions = load_positions()
                if base in positions:
                    # v4.1: Handle partial sells correctly
                    total_qty = positions[base].get('qty', 0)
                    remaining_qty = total_qty - qty

                    if remaining_qty <= 0:
                        # Full exit - mark position as closed
                        positions[base]['closed'] = True
                        positions[base]['close_price'] = result.get('price', 0)
                        positions[base]['close_time'] = datetime.now(RIYADH).isoformat()
                        positions[base]['qty'] = 0
                        log.info(f"Position fully closed: {base} (sold {qty}/{total_qty})")
                    else:
                        # Partial sell - update qty, keep position open
                        positions[base]['qty'] = remaining_qty
                        # Track realized profit from this partial sell
                        realized_so_far = positions[base].get('realized_pnl', 0)
                        sell_price = result.get('price', positions[base].get('entry_price', 0))
                        entry_price = positions[base].get('entry_price', 0)
                        pnl_from_this_sell = (sell_price - entry_price) * qty
                        positions[base]['realized_pnl'] = realized_so_far + pnl_from_this_sell
                        log.info(f"Partial sell: {base} sold {qty}/{total_qty}, remaining {remaining_qty}, realized +{pnl_from_this_sell:.2f} SAR")

                    save_positions(positions)

                    # Update capital - add back the sold value
                    try:
                        with open(CAPITAL_FILE) as f:
                            cap = json.load(f)

                        # Calculate returned amount (use actual sell price if available)
                        sell_price = result.get('price', positions[base].get('entry_price', 0))
                        trade_value = sell_price * qty

                        # Calculate commission (0.05%) and VAT (15% on commission)
                        commission = trade_value * 0.0005  # 0.05%
                        vat = commission * 0.15  # 15% of commission
                        total_fees = commission + vat
                        total_returned = trade_value - commission - vat

                        # Update ALL capital fields properly
                        cap["available_capital"] = cap.get("available_capital", 0) + total_returned
                        cap["grand_total"] = cap.get("grand_total", 1000.66) - total_fees  # Deduct fees from grand total
                        cap["securities_value"] = 0  # Position closed, no securities
                        cap["money_transfer"] = cap["available_capital"]  # Sync with available
                        cap["total_fees"] = cap.get("total_fees", 0) + total_fees
                        cap["updated_at"] = datetime.now(RIYADH).isoformat()
                        cap["source"] = "derayah-api-sell"

                        with open(CAPITAL_FILE, "w") as f:
                            json.dump(cap, f, indent=2)
                        log.info(f"Capital updated after sell: +{total_returned:.2f} SAR (trade: {trade_value:.2f} - commission: {commission:.2f} - VAT: {vat:.2f}), available={cap['available_capital']:.2f}, grand_total={cap['grand_total']:.2f}, securities={cap['securities_value']}")

                        # REFRESH DERAYAH DASHBOARD TO GET ACTUAL VALUES
                        asyncio.create_task(refresh_derayah_and_update_files())
                        # Sync positions with Derayah after trade
                        sync_positions_with_derayah()
                    except Exception as cap_err:
                        log.error(f"Failed to update capital after sell: {cap_err}")
            except Exception as e:
                log.error(f"Failed to update position after sell: {e}")
        return result['success']
    except RuntimeError as e:
        if "already running" in str(e):
            # Already in event loop - schedule and add callback
            try:
                loop = asyncio.get_event_loop()
                future = asyncio.ensure_future(_execute_order_direct(
                "SELL", symbol, qty,
                trigger_basis=trigger_basis,
                trigger_detail=trigger_detail or reason
            ))

                def on_sell_complete(fut):
                    try:
                        res = fut.result()
                        if res.get('success'):
                            positions = load_positions()
                            if base in positions:
                                positions[base]['closed'] = True
                                positions[base]['close_time'] = datetime.now(RIYADH).isoformat()
                                save_positions(positions)

                                # Update capital in callback
                                try:
                                    with open(CAPITAL_FILE) as f:
                                        cap = json.load(f)
                                    sell_price = res.get('price', positions[base].get('entry_price', 0))
                                    trade_value = sell_price * qty

                                    # Calculate commission (0.05%) and VAT (15% on commission)
                                    commission = trade_value * 0.0005  # 0.05%
                                    vat = commission * 0.15  # 15% of commission
                                    total_returned = trade_value - commission - vat

                                    # Update ALL capital fields properly
                                    cap["available_capital"] = cap.get("available_capital", 0) + total_returned
                                    cap["grand_total"] = cap.get("grand_total", 1000.66) - (commission + vat)
                                    cap["securities_value"] = 0
                                    cap["money_transfer"] = cap["available_capital"]
                                    cap["total_fees"] = cap.get("total_fees", 0) + commission + vat
                                    cap["updated_at"] = datetime.now(RIYADH).isoformat()
                                    with open(CAPITAL_FILE, "w") as f:
                                        json.dump(cap, f, indent=2)
                                    
                                    # REFRESH DERAYAH DASHBOARD TO GET ACTUAL VALUES
                                    # Use bookkeeper quick refresh instead of direct file write
                                    try:
                                        import sys
                                        sys.path.insert(0, '/home/mino/tasi-exec')
                                        import bookkeeper
                                        bookkeeper.quick_refresh()
                                    except Exception as bk_err:
                                        log.warning(f"Bookkeeper quick_refresh failed: {bk_err}")
                                except Exception as cap_err:
                                    log.error(f"Callback capital update failed: {cap_err}")
                    except Exception as cb_err:
                        log.error(f"Sell callback error: {cb_err}")

                future.add_done_callback(on_sell_complete)
                tg_send(cmd, chat_id=EXEC_GROUP)
                tg_send(f"🤖 Selling {base} {qty} shares - scheduled\n{reason}")
                return True
            except Exception as inner_e:
                log.error(f"Async sell failed: {inner_e}")
                return False
        else:
            raise
    except Exception as e:
        log.error(f"auto_sell direct execution error: {e}")
        tg_send(cmd, chat_id=EXEC_GROUP)
        tg_send(f"🤖 Selling {base} {qty} shares - Telegram fallback\n{reason}")
        return False


def auto_buy(symbol: str, qty, cycle_n: int, max_cyc: int, price: float,
             price_source: str = "yfinance",
             entry_zone: dict | None = None,
             trigger_basis: str = TRIGGER_PICK_ENTRY,
             trigger_detail: str = ""):
    """
    Execute buy order directly via Derayah API.
    Falls back to Telegram command if API fails.
    """
    base = symbol.replace(".SR", "")

    # GUARD: Prevent re-buy within 30 seconds
    now = time_mod.time()
    if base in _last_trade_time and (now - _last_trade_time[base]) < _MIN_TRADE_INTERVAL:
        log.warning(f"auto_buy BLOCKED for {base}: traded {_last_trade_time[base]:.0f}s ago, min interval={_MIN_TRADE_INTERVAL}s")
        return False

    with _trade_lock:
        # Re-check under lock
        if base in _last_trade_time and (time_mod.time() - _last_trade_time[base]) < _MIN_TRADE_INTERVAL:
            log.warning(f"auto_buy DOUBLE-CHECK BLOCKED for {base}")
            return False

        _last_trade_time[base] = time_mod.time()

    # Check entry zone if provided
    zone_msg = ""
    if entry_zone:
        e_lo = entry_zone.get("e_lo")
        e_hi = entry_zone.get("e_hi")
        if e_lo and e_hi:
            if price < e_lo:
                zone_msg = f" ⚠️ Below zone [{e_lo:.2f}-{e_hi:.2f}]"
                log.warning(f"auto_buy: {base} @ {price:.2f} below entry zone [{e_lo:.2f}-{e_hi:.2f}]")
                log.info(f"BLOCKED_ENTRY: {base} - price {price:.2f} BELOW zone [{e_lo:.2f}-{e_hi:.2f}]")
            elif price > e_hi:
                zone_msg = f" ⚠️ Above zone [{e_lo:.2f}-{e_hi:.2f}]"
                log.warning(f"auto_buy: {base} @ {price:.2f} above entry zone [{e_lo:.2f}-{e_hi:.2f}]")
                log.info(f"BLOCKED_ENTRY: {base} - price {price:.2f} ABOVE zone [{e_lo:.2f}-{e_hi:.2f}]")
            else:
                zone_msg = f" ✅ In zone [{e_lo:.2f}-{e_hi:.2f}]"
                log.info(f"auto_buy: {base} @ {price:.2f} within entry zone [{e_lo:.2f}-{e_hi:.2f}]")

    cmd  = f"BUY {base} {qty} MARKET"
    log.info(f"auto_buy: {cmd} | cycle {cycle_n}/{max_cyc} @ ~{price:.2f}{zone_msg}")

    # Execute buy synchronously (don't rely on async scheduling)
    try:
        result = asyncio.run(_execute_order_direct(
            "BUY", symbol, qty, price,
            trigger_basis=trigger_basis,
            trigger_detail=trigger_detail
        ))
        order_type_label = "MARKET" if price is None else f"LIMIT @ {price:.2f}"
        tg_send(cmd, chat_id=EXEC_GROUP)
        tg_send(
            f"🔄 <b>Cycle {cycle_n}/{max_cyc} - {order_type_label} BUY {base}</b>\n"
            f"{qty} shares{zone_msg}\n"
            f"{result['message']}\n"
            f"<i>{'Filled immediately' if price is None else f'Stays open until price hits {price:.2f}'}</i>"
        )
        # ── DM owner on failure (success message in group is enough for happy path) ──
        if not result.get('success'):
            _dm_owner(
                f"🚨 Auto-buy FAILED for {base}\n"
                f"qty={qty} price={price:.2f}{zone_msg}\n"
                f"Reason: {result.get('message', '?')}"
            )
        return result['success']
    except RuntimeError as e:
        if "already running" in str(e):
            # Already in an event loop - use ensure_future and add callback
            try:
                loop = asyncio.get_event_loop()
                future = asyncio.ensure_future(_execute_order_direct(
                "BUY", symbol, qty, price,
                trigger_basis=trigger_basis,
                trigger_detail=trigger_detail
            ))

                # Add callback to handle result when complete
                def on_complete(fut):
                    try:
                        res = fut.result()
                        if res.get('success'):
                            # Save position
                            positions = load_positions()
                            positions[base] = {
                                "symbol": base,
                                "entry_price": price,
                                "qty": qty,
                                "entry_time": datetime.now(RIYADH).isoformat(),
                                "peak_price": price,
                                "closed": False,
                                "price_source": price_source or "yfinance",
                                "signal": "auto",
                                "order_id": res.get('order_id', '?')
                            }
                            save_positions(positions)
                            log.info(f"Position added (async): {base} {qty}@{price}")
                            # Update capital
                            update_capital_after_buy(price * qty)
                            # REFRESH DERAYAH DASHBOARD TO GET ACTUAL VALUES
                            asyncio.create_task(refresh_derayah_and_update_files())
                            # ── GROUP: success announcement ──
                            order_id = res.get('order_id', '?')
                            order_type_label = "MARKET" if price is None else f"LIMIT @ {price:.2f}"
                            tg_send(
                                f"✅ <b>BUY {base} PLACED ({order_type_label})</b>\n"
                                f"{qty} shares{zone_msg}\n"
                                f"order_id={order_id}\n"
                                f"<i>Market orders fill immediately. Limit orders stay open until price hits {price:.2f}.</i>",
                                chat_id=EXEC_GROUP,
                            )
                    except Exception as cb_err:
                        log.error(f"Buy callback error: {cb_err}")
                        # ── GROUP + DM: failure announcement ──
                        tg_send(
                            f"❌ <b>BUY {base} FAILED (async)</b>\n"
                            f"{qty} shares @ ~{price:.2f}{zone_msg}\n"
                            f"Error: {cb_err}\n"
                            f"<i>No order placed — check poller.log</i>",
                            chat_id=EXEC_GROUP,
                        )
                        _dm_owner(f"🚨 Auto-buy FAILED for {base}\nqty={qty} price={price:.2f}\nError: {cb_err}")

                future.add_done_callback(on_complete)
                tg_send(cmd, chat_id=EXEC_GROUP)
                tg_send(f"🔄 <b>Cycle {cycle_n}/{max_cyc} - Buying {base}</b> {qty} shares @ ~{price:.2f}{zone_msg}\nDirect execution scheduled - check STATUS for confirmation")
                return True  # Optimistic return - actual result handled in callback
            except Exception as inner_e:
                log.error(f"Async buy failed: {inner_e}")
                tg_send(
                    f"❌ <b>BUY {base} FAILED (async)</b>\n"
                    f"{qty} shares @ ~{price:.2f}{zone_msg}\n"
                    f"Error: {inner_e}\n"
                    f"<i>No order placed — check poller.log</i>",
                    chat_id=EXEC_GROUP,
                )
                _dm_owner(f"🚨 Auto-buy FAILED for {base}\nqty={qty} price={price:.2f}\nError: {inner_e}")
                return False
        else:
            raise
    except Exception as e:
        log.error(f"auto_buy direct execution error: {e}")
        # Direct execution failed — be explicit, do NOT claim "Telegram fallback" works
        tg_send(cmd, chat_id=EXEC_GROUP)
        tg_send(
            f"❌ <b>BUY {base} FAILED</b>\n"
            f"{qty} shares @ ~{price:.2f}{zone_msg}\n"
            f"Error: {e}\n"
            f"<i>No order placed — direct execution failed</i>"
        )
        _dm_owner(f"🚨 Auto-buy FAILED for {base}\nqty={qty} price={price:.2f}\nError: {e}")
        return False

# ─── State helpers ────────────────────────────────────────────────────────────

# Mid-session screen times (for reference only - actual scans run via cron)
# 10:30: mid-screen #1, 12:00: mid-screen #2, 13:30: rescreen

PICKS_FILE_1030 = f"{BASE_DIR}/picks_1030.json"
PICKS_FILE_1200 = f"{BASE_DIR}/picks_1200.json"
PICKS_FILE_1330 = f"{BASE_DIR}/picks_1330.json"


def sync_positions_with_derayah():
    """Query Derayah API for actual positions and update positions.json to match."""
    try:
        import requests, json

        # Read token directly from file (sync wrapper for get_token, which is async)
        # TC_DERAYAH is the API token (not Derayah_accesstoken)
        try:
            with open("/home/mino/tasi-exec/derayah_tokens.json") as f:
                _tokens = json.load(f)
            token = _tokens.get('TC_DERAYAH', '') or _tokens.get('Derayah_accesstoken', '')
        except Exception as e:
            log.warning(f"sync_positions_with_derayah: Could not read tokens file: {e}")
            return

        if not token:
            log.warning("sync_positions_with_derayah: No token in tokens file")
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        r = requests.post(
            "https://api.derayah.com/trading/UserPosition/ListPositions",
            headers=headers,
            json={"currencyCode": 1, "exchangeCodes": [98, 99], "portfolio": 2063853},
            timeout=15
        )

        if r.status_code != 200:
            log.warning(f"Derayah position sync failed: HTTP {r.status_code}")
            return

        data = r.json()
        actual_positions = data.get("data", {}).get("tradingAccountPositionInfoList", [])

        # Build map of actual holdings
        actual_map = {}
        for p in actual_positions:
            sym = str(p.get("symbol", ""))
            qty = p.get("quantity", 0)
            cost = p.get("cost", 0)
            if qty > 0:
                actual_map[sym] = {"qty": qty, "cost": cost}

        # Load local positions
        local = load_positions()
        changed = False

        # Fix: Close any local positions that Derayah says we don't have
        for sym, pos in list(local.items()):
            if not pos.get("closed") and sym not in actual_map:
                # Derayah says we don't hold this, but local says open
                # Only close if the sell was attempted (has close_time or was recently traded)
                # Otherwise it might be a legitimate open position not yet reflected
                log.warning(f"Position sync: {sym} not in Derayah holdings - marking closed")
                local[sym]["closed"] = True
                local[sym]["qty"] = 0
                local[sym]["close_time"] = datetime.now(RIYADH).isoformat()
                local[sym]["sync_note"] = "closed_by_derayah_sync"
                changed = True

        # Fix: Open/add any positions Derayah says we have that local doesn't
        for sym, actual in actual_map.items():
            if sym not in local or local[sym].get("closed"):
                log.warning(f"Position sync: {sym} qty={actual['qty']} in Derayah but not local - adding")
                local[sym] = {
                    "symbol": sym,
                    "qty": actual["qty"],
                    "cost": actual["cost"],
                    "avg_price": actual["cost"] / actual["qty"] if actual["qty"] > 0 else 0,
                    "entry_price": actual["cost"] / actual["qty"] if actual["qty"] > 0 else 0,
                    "closed": False,
                    "entry_time": datetime.now(RIYADH).isoformat(),
                    "price_source": "derayah-sync",
                    "signal": "sync",
                    "sync_note": "added_by_derayah_sync"
                }
                changed = True
            elif not local[sym].get("closed") and local[sym].get("qty", 0) != actual["qty"]:
                # Qty mismatch
                log.warning(f"Position sync: {sym} local qty={local[sym].get('qty')} vs Derayah qty={actual['qty']} - updating")
                local[sym]["qty"] = actual["qty"]
                changed = True

        if changed:
            save_positions(local)
            log.info(f"Position sync complete: updated {len([s for s,p in local.items() if p.get('sync_note')])} positions")
        else:
            log.info("Position sync: local and Derayah match ✓")

    except Exception as e:
        log.error(f"Position sync error: {e}")


def load_picks_file(filepath: str) -> list:
    """Load picks from a single file if it's today's."""
    if not os.path.exists(filepath):
        return []
    with open(filepath) as f:
        data = json.load(f)
    today = datetime.now(RIYADH).date().isoformat()
    if data.get("date") != today:
        return []
    picks = data.get("picks", [])
    result = []
    for p in picks:
        sym = p.get("ticker") or p.get("symbol", "")
        if sym:
            result.append({
                "symbol": sym,
                "entry_high": p.get("entry_high", 0),
                "entry_low": p.get("entry_low", 0),
                "score": p.get("score", 0),
                "tier": p.get("tier", "main"),
                "pm_metrics": p.get("pm_metrics", {}),
                "source": p.get("source", os.path.basename(filepath).replace(".json", "")),
            })
    return result


def load_picks_all() -> list:
    """Load all picks from all screen files (09:50, 10:30, 12:00, 13:30).

    Logic:
    - Later screens OVERWRITE earlier screens for same symbol
    - All picks are FULL SIZE (no fallback reduction)
    - SORT BY SCORE GLOBALLY - highest momentum first, regardless of screen time
    """
    all_picks = {}  # symbol -> pick (later overwrites earlier)

    # Load in chronological order so later screens overwrite
    for filepath in [PICKS_FILE, PICKS_FILE_1030, PICKS_FILE_1200, PICKS_FILE_1330]:
        picks = load_picks_file(filepath)
        for p in picks:
            sym = p.get("ticker") or p.get("symbol", "")
            all_picks[sym] = p

    # Sort by score globally - highest momentum first
    # This ensures the BEST picks are monitored, not just the newest
    result = sorted(all_picks.values(), key=lambda x: x.get("score", 0), reverse=True)

    # Log what we loaded
    sources = {}
    for p in result:
        src = p.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    log.info(f"load_picks_all: {len(result)} unique picks loaded. Sources: {sources}")
    if result:
        log.info(f"  Top 5: {', '.join(f'{p['symbol']}({p.get('score',0):.0f})' for p in result[:5])}")

    return result


def load_picks() -> list:
    """Primary picks only (#1-2) for backward compat."""
    return load_picks_all()[:2]

# ─── File-watcher cache for positions.json ────────────────────────────────
_positions_cache = None
_positions_mtime = 0.0

def load_positions_cached() -> dict:
    """Load positions.json only if it changed since last call."""
    global _positions_cache, _positions_mtime
    try:
        mtime = os.path.getmtime(POSITIONS_FILE)
    except OSError:
        return {}
    if mtime != _positions_mtime:
        _positions_mtime = mtime
        with open(POSITIONS_FILE) as f:
            _positions_cache = json.load(f)
    return _positions_cache or {}

def load_positions() -> dict:
    """Load positions.json — bypass cache, force re-read."""
    global _positions_cache, _positions_mtime
    if not os.path.exists(POSITIONS_FILE):
        return {}
    with open(POSITIONS_FILE) as f:
        _positions_cache = json.load(f)
    _positions_mtime = os.path.getmtime(POSITIONS_FILE)
    return _positions_cache

def save_positions(positions: dict):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)

# ─── Order lifecycle helpers now in order_helpers.py (v4.4) ──────────────
# Status codes, load_orders, save_orders, write_order_initiated,
# effective_holdings, trigger_bookkeeper_sync are imported above.
# Status code mapping:
#   0 = INITIATED  (poller-written only, never from Derayah)
#   1 = PLACED     (pending in Derayah)
#   2 = PLACED     (partial — parent still has unfilled qty; child rows in Derayah)
#   3 = FILLED     (terminal)
#   4 = CANCELLED  (terminal)
#   5 = REJECTED   (terminal)
#   6 = EXPIRED    (terminal)
#   7 = CANCELLED  (terminal, user-cancelled)
#   8 = REJECTED   (terminal, system-rejected)
#   12 = FILLED    (terminal, bookkeeper's code)


# ─── WebSocket price listener ─────────────────────────────────────────────────

async def _ws_listener_loop():
    """
    Runs forever in a background thread.
    Connects to the TickerChart tab via CDP, enables Network domain, and
    processes every webSocketFrameReceived event - extracting QO.{SYM}.TAD
    frames and writing them to _ws_price_cache.
    Auto-reconnects on tab loss or CDP drop.
    """
    while True:
        pw = None
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=4000)
            ctx = browser.contexts[0]
            # PRIORITIZE actual TickerChart domain first
            pg = next((p for p in ctx.pages if "derayah.tickerchart.net" in p.url), None)
            if not pg:
                pg = next((p for p in ctx.pages if "RealPrices" in p.url), None)
            if not pg:
                pg = next((p for p in ctx.pages if any(pat in p.url for pat in [TC_URL] + TC_FALLBACK_URLS)), None)
            if not pg:
                log.warning("WS listener: no TC tab - retrying in 30s")
                await asyncio.sleep(30)
                continue

            # Activate TickerChart tab if not active (keepalive may have switched away)
            try:
                await pg.bring_to_front()
                log.info("WS listener: activated TickerChart tab")
            except Exception as e:
                log.debug(f"WS listener: tab activation note: {e}")

            # Use raw CDP websocket for Network domain - Playwright's wrapper
            # doesn't reliably emit webSocketFrameReceived for pre-existing WS connections.
            import websocket as _websocket
            tc_tab_id = None
            tc_ws_url = None
            for t in (await browser.new_page()).context.pages:
                if TC_URL in t.url:
                    # Need tab ID - get from CDP /json
                    break
            # Actually get tab info via HTTP CDP
            import requests
            tabs = requests.get(f"{CDP_URL}/json", timeout=5).json()
            # PRIORITIZE actual TickerChart domain in CDP /json
            tc_tab = next((t for t in tabs if "derayah.tickerchart.net" in t.get("url", "")), None)
            if not tc_tab:
                tc_tab = next((t for t in tabs if "RealPrices" in t.get("url", "")), None)
            if not tc_tab:
                for pattern in [TC_URL] + TC_FALLBACK_URLS:
                    tc_tab = next((t for t in tabs if pattern in t.get("url", "")), None)
                    if tc_tab:
                        log.info(f"WS listener: found tab matching '{pattern}': {tc_tab.get('url','')[:60]}...")
                        break
            if not tc_tab:
                log.warning("WS listener: no TickerChart tab in CDP /json")
                await asyncio.sleep(15)
                continue
            tc_ws_url = tc_tab.get("webSocketDebuggerUrl")
            tc_tab_id = tc_tab.get("id")

            log.info(f"WS listener: raw CDP WS URL = {tc_ws_url[:30]}...")
            cdp_ws = _websocket.create_connection(tc_ws_url, timeout=10, header={"Origin": "http://localhost"}, ping_interval=30, ping_payload="keepalive")
            cdp_ws.send(json.dumps({"id": 1, "method": "Network.enable", "params": {}}))
            log.info("WS listener: raw CDP Network enabled - streaming prices")

            def on_frame(payload):
                try:
                    if not payload:
                        return
                    d = json.loads(payload)
                    topic = d.get("topic", "")
                    if topic.startswith("QO.") and topic.endswith(".TAD"):
                        sym = topic[3:-4]
                        raw = d.get("last") or d.get("lasttradeprice")
                        approx = False
                        if not raw:
                            bid = d.get("bidprice")
                            ask = d.get("askprice")
                            if bid and ask:
                                raw = (float(bid) + float(ask)) / 2
                            elif bid:
                                raw = float(bid)
                            elif ask:
                                raw = float(ask)
                            if raw:
                                approx = True
                        if raw:
                            change_val = float(d.get("change",  0) or 0)
                            pchange_val = float(d.get("pchange", 0) or 0)
                            volume_val = float(d.get("tv", 0) or 0)
                            is_real = not approx
                            
                            # ── v4.7: Liquidity Direction — parse raw fields ──
                            bid_vol   = float(d.get("bidvolume", 0) or 0)
                            ask_vol   = float(d.get("askvolume", 0) or 0)
                            tbv       = float(d.get("tbv", 0) or 0)
                            tav       = float(d.get("tav", 0) or 0)

                            # v4.7b: Spread calculation from bid/ask
                            bid_price = float(d.get("bidprice", 0) or 0)
                            ask_price = float(d.get("askprice", 0) or 0)
                            if bid_price > 0 and ask_price > 0:
                                mid_price = (bid_price + ask_price) / 2
                                spread_pct = (ask_price - bid_price) / mid_price * 100 if mid_price > 0 else 0.0
                            else:
                                spread_pct = 0.0

                            # Calculate ratios (safe division)
                            liq_ratio   = bid_vol / ask_vol if ask_vol > 0 else 1.0
                            net_flow    = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0.0
                            depth_ratio = tbv / tav if tav > 0 else 1.0

                            # Update incremental VWAP
                            ws_vwap = update_ws_vwap(sym, float(raw), change_val, is_real, volume_val)

                            with _ws_cache_lock:
                                entry = _ws_price_cache.get(sym, {})
                                if not approx or not entry.get("real"):
                                    _ws_price_cache[sym] = {
                                        "price":   float(raw),
                                        "ts":      time_mod.time(),
                                        "change":  change_val,
                                        "pchange": pchange_val,
                                        "real":    is_real,
                                        "volume":  volume_val,
                                        "vwap":    ws_vwap,
                                        # v4.7 liquidity fields
                                        "bidvolume":   bid_vol,
                                        "askvolume":   ask_vol,
                                        "tbv":         tbv,
                                        "tav":         tav,
                                        "liquidity_ratio":  liq_ratio,
                                        "net_flow":         net_flow,
                                        "total_depth_ratio": depth_ratio,
                                        # v4.7b spread field
                                        "spread_pct":       round(spread_pct, 4),
                                    }
                            try:
                                from ws_logger import log_price
                                log_price(sym, float(raw), change_val, pchange_val, is_real, ws_vwap, volume_val,
                                          bid_vol, ask_vol, tbv, tav, liq_ratio, net_flow, depth_ratio,
                                          spread_pct)
                            except Exception:
                                pass
                except Exception:
                    pass

            # Read loop
            while True:
                try:
                    msg = json.loads(cdp_ws.recv())
                    if msg.get("method") == "Network.webSocketFrameReceived":
                        payload = msg.get("params", {}).get("response", {}).get("payloadData", "")
                        on_frame(payload)
                except Exception as e:
                    log.warning(f"WS listener read error: {e}")
                    break

            try:
                cdp_ws.close()
            except Exception:
                pass

        except Exception as e:
            log.warning(f"WS listener error: {e} - retrying in 15s")
            await asyncio.sleep(15)
        finally:
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass


def _start_ws_listener():
    global _ws_listener_thread
    if _ws_listener_thread and _ws_listener_thread.is_alive():
        return

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_ws_listener_loop())

    _ws_listener_thread = threading.Thread(target=_run, daemon=True, name="ws-listener")
    _ws_listener_thread.start()
    log.info("WS price listener started")


def _ws_price(symbol: str, max_age_s: float = 300.0) -> float | None:
    """Return WS-cached price if younger than max_age_s, else None."""
    base = symbol.replace(".SR", "")
    with _ws_cache_lock:
        # Check base symbol first
        entry = _ws_price_cache.get(base)
        if entry:
            age = time_mod.time() - entry["ts"]
            if age <= max_age_s:
                return entry["price"]
        # Fallback: check with .TAD suffix (used by ws_probe)
        entry = _ws_price_cache.get(f"{base}.TAD")
        if entry:
            age = time_mod.time() - entry["ts"]
            if age <= max_age_s:
                return entry["price"]
    return None

# ─── Tick-Based VWAP Fallback ────────────────────────────────────────────────

def _calculate_tick_based_vwap(symbol: str) -> float | None:
    """
    Calculate VWAP from websocket ticks stored in ws_prices_YYYY-MM-DD.jsonl.
    
    Fallback when incremental VWAP state is not available.
    Builds 1-minute candles from tick data and calculates VWAP.
    Uses tick count as volume proxy.
    
    Returns: VWAP value or None if insufficient data.
    """
    try:
        from datetime import datetime, timedelta
        import json
        
        date_str = datetime.now(RIYADH).strftime('%Y-%m-%d')
        filename = f'{BASE_DIR}/ws_prices_{date_str}.jsonl'
        
        if not os.path.exists(filename):
            return None
        
        # Load ticks for this symbol
        ticks = []
        with open(filename, 'r') as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    if d.get('symbol') == symbol:
                        dt = datetime.fromisoformat(d['time'].replace('Z', '+00:00'))
                        dt = dt.replace(tzinfo=pytz.UTC).astimezone(RIYADH)
                        ticks.append({
                            'ts': dt,
                            'price': float(d['price']),
                            'real': d.get('real', True)
                        })
                except:
                    continue
        
        if not ticks:
            return None
        
        # Sort by time
        ticks.sort(key=lambda x: x['ts'])
        
        # Build 1-minute candles
        candles = {}
        for tick in ticks:
            minute = tick['ts'].replace(second=0, microsecond=0)
            price = tick['price']
            real = tick['real']
            
            if minute not in candles:
                candles[minute] = {
                    'open': price, 'high': price, 'low': price, 'close': price,
                    'volume': 1, 'real_volume': 2 if real else 1  # Real ticks count more
                }
            else:
                c = candles[minute]
                c['high'] = max(c['high'], price)
                c['low'] = min(c['low'], price)
                c['close'] = price
                c['volume'] += 1
                c['real_volume'] += 2 if real else 1
        
        if not candles:
            return None
        
        # Calculate VWAP from candles
        # VWAP = sum(typical_price * volume) / sum(volume)
        total_pv = 0.0
        total_vol = 0.0
        
        for minute, c in sorted(candles.items()):
            tp = (c['high'] + c['low'] + c['close']) / 3  # Typical price
            vol = c['real_volume']  # Weighted volume (real ticks count more)
            total_pv += tp * vol
            total_vol += vol
        
        if total_vol > 0:
            vwap = total_pv / total_vol
            log.info(f"Tick-based VWAP for {symbol}: {vwap:.2f} ({len(candles)} candles, {len(ticks)} ticks)")
            return vwap
        
        return None
    except Exception as e:
        log.warning(f"Tick-based VWAP calculation failed for {symbol}: {e}")
        return None


# ─── 1-Minute Candle Builder from Raw WebSocket Data ───────────────────────────

def build_1min_candles(symbol: str, date_str: str) -> pd.DataFrame | None:
    """
    Build 1-minute OHLCV candles from ws_frames_raw.log.
    
    Groups ticks by minute and calculates:
    - Open: first price in minute
    - High: max price in minute
    - Low: min price in minute
    - Close: last price in minute
    - Volume: tick count (real=2, snapshot=1)
    
    Returns DataFrame or None if no data.
    """
    try:
        raw_log = f'{BASE_DIR}/ws_frames_raw.log'
        if not os.path.exists(raw_log):
            return None
        
        symbol_topic = f"QO.{symbol}.TAD"
        candles = {}
        
        with open(raw_log, 'r') as f:
            for line in f:
                if not line.startswith(date_str):
                    continue
                if symbol_topic not in line:
                    continue
                
                try:
                    # Extract timestamp
                    timestamp_str = line[:19]
                    dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                    minute = dt.replace(second=0, microsecond=0)
                    
                    # Extract JSON
                    json_start = line.find('{')
                    if json_start == -1:
                        continue
                    
                    d = json.loads(line[json_start:])
                    
                    # Get price
                    price = None
                    if 'last' in d and d['last'] != '#':
                        price = float(d['last'])
                    elif 'lasttradeprice' in d and d['lasttradeprice'] != '#':
                        price = float(d['lasttradeprice'])
                    elif 'bidprice' in d and d['bidprice'] != '#':
                        price = float(d['bidprice'])
                    
                    if price is None or price <= 0:
                        continue
                    
                    # Check if snapshot (less reliable)
                    is_snapshot = d.get('issnapshot') == 'yes'
                    vol_weight = 1 if is_snapshot else 2
                    
                    # Build candle
                    if minute not in candles:
                        candles[minute] = {
                            'Open': price,
                            'High': price,
                            'Low': price,
                            'Close': price,
                            'Volume': vol_weight,
                            'tick_count': 1
                        }
                    else:
                        c = candles[minute]
                        c['High'] = max(c['High'], price)
                        c['Low'] = min(c['Low'], price)
                        c['Close'] = price
                        c['Volume'] += vol_weight
                        c['tick_count'] += 1
                except:
                    continue
        
        if not candles:
            return None
        
        # Convert to DataFrame
        df_data = []
        for minute in sorted(candles.keys()):
            c = candles[minute]
            df_data.append({
                'datetime': minute,
                'Open': c['Open'],
                'High': c['High'],
                'Low': c['Low'],
                'Close': c['Close'],
                'Volume': c['Volume']
            })
        
        df = pd.DataFrame(df_data)
        df.set_index('datetime', inplace=True)
        
        log.info(f"Built {len(df)} 1-min candles for {symbol} from raw log")
        return df
    except Exception as e:
        log.warning(f"Failed to build 1-min candles for {symbol}: {e}")
        return None


# ─── Market data ──────────────────────────────────────────────────────────────

def fetch_data(symbol: str) -> tuple:
    """
    Returns (latest_price, df_5m, price_source, ws_vwap) or (None, None, None, None) on failure.
    Price priority: WS cache (real-time) → yfinance (15-min delay).
    df_5m always from yfinance (needed for VWAP/volume candles).
    ws_vwap from incremental websocket calculation (real-time).
    """
    try:
        ticker_sym = symbol if "." in symbol else f"{symbol}.SR"
        df = yf.Ticker(ticker_sym).history(period="1d", interval="5m")
        # yfinance returns empty early in the session before intraday data lands -
        # fall back to last 5 days so VWAP candles are always available.
        if df.empty:
            df = yf.Ticker(ticker_sym).history(period="5d", interval="5m")
        if df.empty:
            log.warning(f"fetch_data {symbol}: no data from yfinance")
            return None, None, None, None

        # 1. WS cache - real-time, zero network calls
        price = _ws_price(symbol)
        ws_vwap = get_ws_vwap(symbol.replace(".SR", ""))
        
        if price:
            vwap_str = f"{ws_vwap:.2f}" if ws_vwap is not None else "N/A"
            log.info(f"fetch_data {symbol}: WS price {price:.2f} VWAP={vwap_str}")
            return price, df, "websocket", ws_vwap

        # 2. yfinance delayed
        price = float(df["Close"].iloc[-1])
        log.warning(f"fetch_data {symbol}: WS cache miss - using yfinance delayed {price:.2f}")
        return price, df, "yfinance", None
    except Exception as e:
        log.warning(f"fetch_data {symbol}: {e}")
        return None, None, None, None

def calc_vwap(df: pd.DataFrame) -> float | None:
    try:
        df = df.copy()
        df["tp"] = (df["High"] + df["Low"] + df["Close"]) / 3
        cum_vol = df["Volume"].cumsum()
        if cum_vol.iloc[-1] == 0:
            return None
        return float((df["tp"] * df["Volume"]).cumsum().iloc[-1] / cum_vol.iloc[-1])
    except Exception:
        return None

# ─── VWAP Direction Calculation (for hard close) ──────────────────────────────
def calc_vwap_direction(df: pd.DataFrame, window: int = 5) -> float:
    """
    Calculate VWAP trend direction over last N candles.
    Uses yfinance data for direction only, NOT for VWAP value.
    Positive = rising, Negative = falling, 0 = flat.
    """
    try:
        if len(df) < 2:
            return 0.0
        # Calculate VWAP for each candle in window (using yfinance data for trend only)
        df_copy = df.tail(window).copy()
        df_copy["tp"] = (df_copy["High"] + df_copy["Low"] + df_copy["Close"]) / 3
        cumvol = df_copy["Volume"].cumsum()
        vwaps = (df_copy["tp"] * df_copy["Volume"]).cumsum() / cumvol
        # Return slope (last - first)
        return float(vwaps.iloc[-1] - vwaps.iloc[0]) if len(vwaps) >= 2 else 0.0
    except Exception:
        return 0.0

# ─── Dynamic Time Stop Helper ───────────────────────────────────────────────

def _time_stop_triggered(entry_time: datetime, regime: str, now: datetime) -> bool:
    """
    Check if dynamic time stop should trigger based on entry time and regime.
    
    TRENDING: No time stop (return False always)
    NEUTRAL: Before 10:30 -> 12:00, 10:30-12:00 -> 14:00, After 12:00 -> 14:30
    DEFENSIVE: Before 10:30 -> 11:30, 10:30-12:00 -> 13:00, After 12:00 -> 14:00
    """
    if regime == "TRENDING":
        return False  # No time stops in trending markets
    
    if entry_time is None:
        return False
    
    hour = entry_time.hour
    minute = entry_time.minute
    
    if regime == "NEUTRAL":
        if hour < 10 or (hour == 10 and minute < 30):
            stop_time = entry_time.replace(hour=12, minute=0)
        elif hour < 12:
            stop_time = entry_time.replace(hour=14, minute=0)
        else:
            stop_time = entry_time.replace(hour=14, minute=30)
    else:  # DEFENSIVE
        if hour < 10 or (hour == 10 and minute < 30):
            stop_time = entry_time.replace(hour=11, minute=30)
        elif hour < 12:
            stop_time = entry_time.replace(hour=13, minute=0)
        else:
            stop_time = entry_time.replace(hour=14, minute=0)
    
    return now >= stop_time

# ─── calc_vwap NOTE ──────────────────────────────────────────────────────────
# calc_vwap() below uses yfinance OHLCV data. This is ONLY used for:
# 1. VWAP direction calculation (trend, not absolute value)
# 2. As a last-resort fallback when both websocket VWAP methods fail
# For real-time VWAP decisions, always use:
#   - Primary: get_ws_vwap() - websocket incremental
#   - Secondary: _calculate_tick_based_vwap() - from ws_prices.jsonl
#   - Never: calc_vwap() for real-time decisions (15-min delayed)

def check_vwap_reclaim(df: pd.DataFrame, vwap: float, symbol: str = "") -> bool:
    """
    Check if price is reclaiming VWAP with volume confirmation.
    Uses 1-minute candles from websocket data for more accurate timing.
    Falls back to 5-min yfinance data if 1-min not available.
    """
    if len(df) < 2:
        return False
    
    # Try 1-min candles first for more accurate entry timing
    now = datetime.now(RIYADH)
    date_str = now.strftime('%Y-%m-%d')
    base_symbol = symbol.replace(".SR", "") if symbol else ""
    candles_1m = build_1min_candles(base_symbol, date_str)
    
    if candles_1m is not None and len(candles_1m) >= 2:
        # Use last 2 candles from 1-min data
        recent = candles_1m.tail(2)
        prev_close = float(recent["Close"].iloc[0])
        curr_close = float(recent["Close"].iloc[-1])
        avg_vol = float(candles_1m["Volume"].mean())
        curr_vol = float(recent["Volume"].iloc[-1])
    else:
        # Fallback to 5-min data
        prev_close = float(df["Close"].iloc[-2])
        curr_close = float(df["Close"].iloc[-1])
        avg_vol = float(df["Volume"].mean())
        curr_vol = float(df["Volume"].iloc[-1])
    
    # Relaxed volume threshold (0.5 instead of 0.8) for midday choppy markets
    volume_ok = curr_vol > avg_vol * 0.5
    
    return prev_close < vwap < curr_close and volume_ok

def check_breakout(df: pd.DataFrame) -> bool:
    try:
        # Require 6+ candles (30 min) - prevents gap-up opens from triggering at 10:00
        if len(df) < 6:
            return False
        prior_high = float(df["High"].iloc[:-1].max())
        curr_close = float(df["Close"].iloc[-1])
        avg_vol    = float(df["Volume"].mean())
        curr_vol   = float(df["Volume"].iloc[-1])
        return curr_close > prior_high and curr_vol > avg_vol * 1.5
    except Exception:
        return False

# ─── Core poll ────────────────────────────────────────────────────────────────

_alerted: set = set()

# Cycling state - reset at session start (module load)
cycles_today: dict    = {}   # {symbol: int}  - completed cycles per symbol today
consec_scratches: dict = {}  # {symbol: int}  - consecutive scratches per symbol
_prev_positions: dict  = {}  # snapshot from last poll - used to detect open→closed transitions


def _reset_symbol_alerts(symbol: str):
    """Clear per-symbol alert keys so they can fire again on re-entry."""
    for suffix in ("_hard_stop", "_trail", "_time_stop", "_vwap_exit", "_target",
                   "_vwap_entry", "_breakout", "_gap_entry", "_zone_hold"):
        _alerted.discard(symbol + suffix)
        _alerted.discard(symbol.replace(".SR", "") + suffix)

def fast_poll(regime: dict):
    """
    Runs every 10 seconds - pure file I/O, no network.
    Detects position closes (sells) and fires cycle re-entry alerts immediately.
    Also sends the 14:45 hard-close reminder.
    """
    global _prev_positions

    r_params     = regime.get("params", {"max_cycles": 2, "position_pct": 0.40})
    max_cycles   = r_params.get("max_cycles", 2)
    regime_name  = regime.get("regime", "NEUTRAL")

    now      = datetime.now(RIYADH)
    now_time = now.time()

    # v4.1: Dynamic hard close - VWAP-aware exit window 14:30-14:50
    
    # Phase 1: 14:30-14:49 - VWAP-based exits
    if now_time >= HARD_CLOSE_START and now_time < HARD_CLOSE_END and "hard_close_p1" not in _alerted:
        positions = load_positions_cached()
        open_syms = [s for s, p in positions.items() if not p.get("closed")]
        
        if open_syms:
            tg_send(f"⏰ Hard Close Window (14:30-14:50) - {len(open_syms)} position(s) still open")
            for s in open_syms:
                price, df_pos, _, ws_vwap = fetch_data(f"{s}.SR")
                # Primary: WebSocket incremental VWAP
                vwap_now = ws_vwap
                # Fallback: Calculate from websocket ticks if incremental not available
                if vwap_now is None:
                    vwap_now = _calculate_tick_based_vwap(s)
                
                # Direction calculation (uses yfinance df as last resort for trend)
                vwap_dir = calc_vwap_direction(df_pos, window=3) if df_pos is not None else 0
                entry = positions[s].get("entry_price", 0)
                gain_pct = (price - entry) / entry if entry else 0
                
                exit_reason = ""
                
                if gain_pct > 0:
                    # In profit - take it immediately, don't gamble near close
                    exit_reason = f"💰 Profit {gain_pct*100:.1f}% - taking profit before close"
                    
                elif abs(gain_pct) <= 0.001:
                    # Breakeven - exit now, don't risk turning into loss
                    exit_reason = f"⚖️ Breakeven - exiting to avoid loss near close"
                    
                elif gain_pct < -0.03:
                    # Deep loss - exit immediately
                    exit_reason = f"🛑 Deep loss {gain_pct*100:.1f}% - exiting now"
                    
                elif vwap_now and price < vwap_now:
                    # Below VWAP with loss - check VWAP direction
                    if vwap_dir > 0:
                        # VWAP rising - aim for breakeven
                        exit_reason = f"📈 VWAP recovering (dir={vwap_dir:.4f}) - aiming for breakeven from {gain_pct*100:.1f}%"
                        tg_send(f"{s}: {exit_reason}")
                        continue
                    else:
                        # VWAP flat or falling - exit now
                        exit_reason = f"📉 Below VWAP ({price:.2f} < {vwap_now:.2f}), VWAP falling - cutting loss at {gain_pct*100:.1f}%"
                        
                elif vwap_now and price >= vwap_now and gain_pct < 0:
                    # Above VWAP but still in loss - monitor
                    if vwap_dir > 0:
                        exit_reason = f"📊 Above VWAP ({price:.2f} >= {vwap_now:.2f}), recovering - monitoring for breakeven"
                        tg_send(f"{s}: {exit_reason}")
                        continue
                    else:
                        exit_reason = f"📊 Above VWAP ({price:.2f} >= {vwap_now:.2f}) but VWAP flat - exiting at {gain_pct*100:.1f}%"
                
                else:
                    # Default: monitor
                    exit_reason = f"⏳ {gain_pct*100:.1f}% - monitoring until 14:50"
                    tg_send(f"{s}: {exit_reason}")
                    continue
                
                auto_sell(s, positions[s].get("qty", "?"), f"Hard Close | {exit_reason}",
                          trigger_basis=TRIGGER_HARD_CLOSE,
                          trigger_detail=f"Hard close: {exit_reason}")
                
                try:
                    block_file = f"{BASE_DIR}/blocked_symbols.txt"
                    blocked = set()
                    if os.path.exists(block_file):
                        with open(block_file) as f:
                            blocked = set(line.strip() for line in f if line.strip())
                    blocked.add(s)
                    with open(block_file, "w") as f:
                        f.write("\n".join(sorted(blocked)) + "\n")
                except Exception as e:
                    log.error(f"Failed to update blocked_symbols.txt: {e}")
        
        _alerted.add("hard_close_p1")
        log.info("Hard close Phase 1 (VWAP exits) processed")
    
    # Phase 2: 14:50+ - FORCE SELL ALL remaining
    if now_time >= HARD_CLOSE_END and "hard_close_p2" not in _alerted:
        positions = load_positions_cached()
        remaining = [s for s, p in positions.items() if not p.get("closed")]
        
        if remaining:
            tg_send(f"⏰ HARD CLOSE 14:50 - Force selling {len(remaining)} remaining position(s)")
            for s in remaining:
                qty = positions[s].get("qty", "?")
                price, _, _, _ = fetch_data(f"{s}.SR")
                entry = positions[s].get("entry_price", 0)
                gain_pct = (price - entry) / entry if entry else 0
                auto_sell(s, qty, f"⏰ HARD CLOSE 14:50 - Force market sell | {gain_pct*100:+.1f}%",
                          trigger_basis=TRIGGER_HARD_CLOSE,
                          trigger_detail="Hard close 14:50 forced exit")
                log.info(f"HARD CLOSE forced sell: {s} qty={qty} at {price:.2f} ({gain_pct*100:.1f}%)")
        
        _alerted.add("hard_close_p2")
        log.info("Hard close Phase 2 (force sell) processed")
        
        # Create stand-down marker file (persists across restarts)
        try:
            stand_down_path = f"{BASE_DIR}/stand_down"
            if not os.path.exists(stand_down_path):
                with open(stand_down_path, "w") as f:
                    f.write(f"STAND DOWN activated at {datetime.now(RIYADH).isoformat()}\n")
                    f.write("No new buys allowed until next session\n")
                    f.write("Remove this file before next trading day\n")
                log.info("STAND DOWN mode activated - no new buys until tomorrow")
        except Exception as e:
            log.error(f"Failed to create stand_down marker: {e}")

def slow_poll(regime: dict):
    """
    Runs every 5 minutes - fetches prices from yfinance/Derayah.
    Monitors open positions for stop/trail/target alerts.
    Scans picks for fresh entry signals.
    """
    global _regime_history, _regime_confirmed

    r_params     = regime.get("params", {})
    regime_name  = regime.get("regime", "NEUTRAL")

    # ── Regime change confirmation logic ────────────────────────────────────
    now = datetime.now(RIYADH)

    # Add current regime to history
    _regime_history.append((regime_name, now))
    # Keep only last 2 hours of history
    _regime_history = [(r, t) for r, t in _regime_history if (now - t).total_seconds() < 7200]

    # Check if regime has been consistent for REGIME_CONFIRM_MINS
    if len(_regime_history) >= 2:
        # Check if all recent entries are same regime
        recent_regimes = [r for r, t in _regime_history if (now - t).total_seconds() <= REGIME_CONFIRM_MINS * 60]
        if recent_regimes and all(r == recent_regimes[0] for r in recent_regimes):
            if _regime_confirmed != recent_regimes[0]:
                old_regime = _regime_confirmed or "UNKNOWN"
                new_regime = recent_regimes[0]
                if old_regime != new_regime:
                    log.info(f"Regime change CONFIRMED: {old_regime} → {new_regime} (held for {REGIME_CONFIRM_MINS} min)")
                    tg_send(f"📊 <b>Regime Change Confirmed</b>\n{old_regime} → {new_regime}\nExit targets updated for all open positions")
                _regime_confirmed = new_regime

    # Use confirmed regime for exit parameters (falls back to current if not confirmed)
    effective_regime = _regime_confirmed or regime_name

    # Load parameters from confirmed regime
    from market_regime import REGIME_PARAMS
    effective_params = REGIME_PARAMS.get(effective_regime, r_params)

    # Dynamic targets/stops based on CONFIRMED regime
    win_pct         = effective_params.get("target_pct", r_params.get("target_pct", 0.02))
    hard_stop_pct   = effective_params.get("hard_stop", r_params.get("hard_stop", 0.07))
    trail_trigger   = effective_params.get("trail_trigger", r_params.get("trail_trigger", 0.02))
    trail_stop_pct  = effective_params.get("trail_stop", r_params.get("trail_stop", 0.03))
    time_stop_pct   = effective_params.get("time_stop_pct", r_params.get("time_stop_pct", 0.01))
    time_stop_mins  = effective_params.get("time_stop_mins", r_params.get("time_stop_mins", 30))

    # Position sizing from current regime (entry-time decision)
    max_cycles       = r_params.get("max_cycles", 2)
    position_pct     = r_params.get("position_pct", 0.40)
    alt_position_pct = r_params.get("alt_position_pct", position_pct)
    max_positions    = r_params.get("max_positions", 3)

    now_time = now.time()

    # Sync positions with Derayah at start of each cycle
    sync_positions_with_derayah()

    positions = load_positions_cached()
    updated   = False

    for symbol, pos in positions.items():
        if pos.get("closed"):
            continue

        price, df_pos, _, ws_vwap = fetch_data(symbol)
        # Primary: WebSocket incremental VWAP
        vwap_now = ws_vwap
        # Fallback: Tick-based VWAP from ws_prices
        if vwap_now is None:
            vwap_now = _calculate_tick_based_vwap(symbol.replace(".SR", ""))
        if price is None:
            continue

        entry = pos.get("entry_price", 0)
        peak  = pos.get("peak_price", entry)

        if price > peak:
            pos["peak_price"] = price
            peak = price
            updated = True

        gain_pct       = (price - entry) / entry if entry else 0
        peak_pct       = (peak - entry)  / entry if entry else 0
        drop_from_peak = (peak - price)  / peak  if peak  else 0

        mins_held  = 0
        entry_time = pos.get("entry_time")
        if entry_time:
            try:
                et = datetime.fromisoformat(entry_time)
                if et.tzinfo is None:
                    et = et.replace(tzinfo=RIYADH)
                mins_held = (now - et).total_seconds() / 60
            except Exception:
                pass

        # Minimum hold time for trail stops (to avoid spread noise)
        MIN_HOLD_MINS = 15

        key_stop      = f"{symbol}_hard_stop"
        key_trail     = f"{symbol}_trail"
        key_time_stop = f"{symbol}_time_stop"
        key_vwap_exit = f"{symbol}_vwap_exit"
        key_target    = f"{symbol}_target"

        qty = pos.get("qty", "?")

        # ── Dynamic regime-based exit thresholds ────────────────────────────
        regime_params = get_current_regime().get("params", {})
        win_pct         = regime_params.get("target_pct", 0.02)
        hard_stop_pct   = regime_params.get("hard_stop", 0.07)
        trail_trigger   = regime_params.get("trail_trigger", 0.02)
        trail_stop_pct  = regime_params.get("trail_stop", 0.03)
        time_stop_pct   = regime_params.get("time_stop_pct", 0.01)
        time_stop_mins  = regime_params.get("time_stop_mins", 30)

        if gain_pct <= -hard_stop_pct and key_stop not in _alerted:
            auto_sell(symbol, qty,
                      f"🛑 Hard stop {int(-hard_stop_pct*100)}% | Entry: {entry:.2f} | Now: {price:.2f} ({gain_pct*100:.1f}%)",
                      trigger_basis=TRIGGER_HARD_STOP,
                      trigger_detail=f"Hard stop {gain_pct*100:.1f}% (threshold: {hard_stop_pct*100:.1f}%)")
            _alerted.add(key_stop)
            cycles_today[symbol] = 999
            log.info(f"Hard stop: {symbol} {gain_pct*100:.1f}%")

        # v4.1: Tiered profits replace old +2% target
        # Only use old full-target if qty == 1 (can't do partial exits)
        elif gain_pct >= win_pct and key_target not in _alerted and qty == 1:
            auto_sell(symbol, qty,
                      f"🎯 Target +{int(win_pct*100)}% | Entry: {entry:.2f} | Now: {price:.2f} (+{gain_pct*100:.1f}%) - qty=1, full exit",
                      trigger_basis=TRIGGER_TARGET_REACHED,
                      trigger_detail=f"Target +{gain_pct*100:.1f}% (threshold: +{win_pct*100:.1f}%)")
            _alerted.add(key_target)
            log.info(f"Target +{int(win_pct*100)}%: {symbol} gain={gain_pct*100:.1f}% (qty=1, no tiers)")

        elif drop_from_peak >= trail_stop_pct and mins_held >= MIN_HOLD_MINS and key_trail not in _alerted:
            auto_sell(symbol, qty,
                      f"📉 Trailing stop | Peak: {peak:.2f} (+{peak_pct*100:.1f}%) | Now: {price:.2f} (-{drop_from_peak*100:.1f}% from peak)",
                      trigger_basis=TRIGGER_TRAILING_STOP,
                      trigger_detail=f"Trail stop: peak={peak:.2f} ({peak_pct*100:.1f}%), drop={drop_from_peak*100:.1f}% (threshold: {trail_stop_pct*100:.1f}%) [fixed: entry-based]")
            _alerted.add(key_trail)
            log.info(f"Trail stop: {symbol} peak={peak:.2f} now={price:.2f}")

        elif (gain_pct <= -time_stop_pct and key_time_stop not in _alerted and
              _time_stop_triggered(entry_time, regime, now)):
            auto_sell(symbol, qty,
                      f"⏱ Time stop | Held {int(mins_held)} min | Entry: {entry:.2f} | Now: {price:.2f} ({gain_pct*100:.1f}%)",
                      trigger_basis=TRIGGER_TIME_STOP,
                      trigger_detail=f"Time stop: held {int(mins_held)}min, gain={gain_pct*100:.1f}% (regime: {regime})")
            _alerted.add(key_time_stop)
            log.info(f"Time stop: {symbol} held={int(mins_held)}min gain={gain_pct*100:.1f}% regime={regime}")

        # v4.6: VWAP breakdown exit — DISABLED in TRENDING regime
        elif key_vwap_exit not in _alerted and regime_name != "TRENDING":
            if df_pos is not None:
                vwap_now = calc_vwap(df_pos)
                if vwap_now and price < vwap_now:
                    # ── Step 1: Minimum hold time ──
                    MIN_HOLD_MINS = 15  # v4.6: 15 min for ALL regimes (was 10 for 1-min candles)
                    if mins_held < MIN_HOLD_MINS:
                        log.info(f"VWAP breakdown: {symbol} price={price:.2f} vwap={vwap_now:.2f} gain={gain_pct*100:.1f}% — HELD {int(mins_held)}min < {MIN_HOLD_MINS}min min hold, skipping")
                        continue  # Skip sell, don't add to _alerted (re-evaluate next cycle)
                    
                    # ── Step 2: VWAP direction check (v4.6) ──
                    vwap_dir = calc_vwap_direction(df_pos, window=3) if df_pos is not None else 0
                    if vwap_dir > 0:
                        # VWAP is rising — momentum recovering, skip sell
                        log.info(f"VWAP breakdown: {symbol} price={price:.2f} vwap={vwap_now:.2f} gain={gain_pct*100:.1f}% — VWAP RISING (dir={vwap_dir:.4f}), skip breakdown exit")
                        continue  # Skip sell, re-evaluate next cycle
                    # Build 1-min candles from websocket data for more accurate recovery detection
                    date_str = now.strftime('%Y-%m-%d')
                    candles_1m = build_1min_candles(symbol.replace(".SR", ""), date_str)
                    
                    if candles_1m is not None and len(candles_1m) >= 10:
                        # Use last 15 candles (15-minute window)
                        recent_candles = candles_1m.tail(15)
                        closes = [float(c) for c in recent_candles["Close"]]
                        
                        # Calculate rising vs falling candles
                        rising = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
                        falling = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i-1])
                        total = rising + falling
                        recovery_prob = rising / total if total > 0 else 0.5
                        
                        # Volume strength (tick count as proxy)
                        recent_vol = float(recent_candles["Volume"].mean())
                        avg_vol = float(candles_1m["Volume"].mean())
                        vol_strength = min(recent_vol / avg_vol, 1.5) if avg_vol > 0 else 1.0
                        
                        # Weighted recovery score (adjusted threshold for 1-min)
                        recovery_score = recovery_prob * vol_strength
                        
                        # v4.7: Liquidity direction boost/penalty for recovery (Phase 3)
                        if r_params.get("enable_liquidity", False):
                            cache = _ws_price_cache.get(symbol.replace(".SR", ""), {})
                            nf = cache.get("net_flow", 0.0)
                            if nf > 0.3:
                                recovery_score += 0.15
                                log.info(f"VWAP breakdown: {symbol} recovery BOOST (net_flow={nf:.2f} > 0.3)")
                            elif nf < -0.3:
                                recovery_score -= 0.15
                                log.info(f"VWAP breakdown: {symbol} recovery PENALTY (net_flow={nf:.2f} < -0.3)")
                        
                        is_recovering = recovery_score > 0.60  # Adjusted from 0.66
                        
                        log.info(f"VWAP breakdown: {symbol} 1-min recovery: {len(recent_candles)} candles, score={recovery_score:.2f} (threshold: 0.60), rising={rising}/{total}")
                    else:
                        # Fallback to 5-min candles if 1-min not available
                        recent_candles = df_pos.tail(5)
                        if len(recent_candles) >= 3:
                            closes = [float(c) for c in recent_candles["Close"]]
                            rising = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
                            falling = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i-1])
                            total = rising + falling
                            recovery_prob = rising / total if total > 0 else 0.5
                            recent_vol = float(recent_candles["Volume"].mean()) if "Volume" in recent_candles else 0
                            avg_vol = float(df_pos["Volume"].mean()) if "Volume" in df_pos else 1
                            vol_strength = min(recent_vol / avg_vol, 1.5) if avg_vol > 0 else 1.0
                            recovery_score = recovery_prob * vol_strength
                            
                            # v4.7: Liquidity direction boost/penalty for 5-min fallback recovery
                            if r_params.get("enable_liquidity", False):
                                cache = _ws_price_cache.get(symbol.replace(".SR", ""), {})
                                nf = cache.get("net_flow", 0.0)
                                if nf > 0.3:
                                    recovery_score += 0.15
                                    log.info(f"VWAP breakdown: {symbol} 5-min recovery BOOST (net_flow={nf:.2f} > 0.3)")
                                elif nf < -0.3:
                                    recovery_score -= 0.15
                                    log.info(f"VWAP breakdown: {symbol} 5-min recovery PENALTY (net_flow={nf:.2f} < -0.3)")
                            
                            is_recovering = recovery_score > 0.66
                            log.info(f"VWAP breakdown: {symbol} 5-min fallback recovery: score={recovery_score:.2f} (threshold: 0.66), rising={rising}/{total}")
                        else:
                            recovery_score = 0.5
                            is_recovering = False
                            log.info(f"VWAP breakdown: {symbol} insufficient data, default recovery_score=0.5")
                    
                    # v4.6: Breakeven hold — if loss is small and recovering, skip sell
                    loss_pct = -gain_pct if gain_pct < 0 else 0
                    if loss_pct > 0 and loss_pct < 0.03 and is_recovering:
                        log.info(f"VWAP breakdown: {symbol} price={price:.2f} vwap={vwap_now:.2f} gain={gain_pct*100:.1f}% — BREAKEVEN HOLD (loss={loss_pct*100:.1f}% < 3%, recovery_score={recovery_score:.2f} > 0.66, rising={rising}/{total})")
                        continue  # Skip sell, re-evaluate next cycle
                    
                    # v4.7: Liquidity direction confirmation (Phase 3)
                    # Only act if liquidity data is available and enabled
                    if r_params.get("enable_liquidity", False):
                        cache = _ws_price_cache.get(symbol.replace(".SR", ""), {})
                        liq_ratio = cache.get("liquidity_ratio", 1.0)
                        
                        if liq_ratio < r_params.get("liquidity_exit_confirm", 0.5):
                            # Sellers dominant — confirmed breakdown
                            log.info(f"VWAP breakdown CONFIRMED: {symbol} liquidity_ratio={liq_ratio:.2f} < {r_params.get('liquidity_exit_confirm', 0.5)} — heavy selling pressure")
                        elif liq_ratio > r_params.get("liquidity_hold_min", 1.5):
                            # Buyers absorbing — hold
                            log.info(f"VWAP breakdown HOLD: {symbol} liquidity_ratio={liq_ratio:.2f} > {r_params.get('liquidity_hold_min', 1.5)} — buyers absorbing, skip sell")
                            continue  # Skip sell, re-evaluate next cycle
                    
                    # SELL — genuine breakdown
                    if gain_pct > 0:
                        reason = f"📉 VWAP breakdown | Price {price:.2f} below VWAP {vwap_now:.2f} - momentum broke (taking +{gain_pct*100:.1f}% profit)"
                    else:
                        reason = f"📉 VWAP breakdown | Price {price:.2f} below VWAP {vwap_now:.2f} - cutting {gain_pct*100:.1f}% loss"
                    
                    trigger_detail = f"VWAP breakdown: price={price:.2f} vwap={vwap_now:.2f} gain={gain_pct*100:.1f}% held={int(mins_held)}min recovery_score={recovery_score:.2f}"
                    auto_sell(symbol, qty, reason,
                              trigger_basis=TRIGGER_VWAP_BREAKDOWN,
                              trigger_detail=trigger_detail)
                    _alerted.add(key_vwap_exit)
                    log.info(f"VWAP breakdown: {symbol} price={price:.2f} vwap={vwap_now:.2f} gain={gain_pct*100:.1f}% — SELL (genuine breakdown)")

        # ── v4.1: Tiered profit targets (partial exits) ──
        # Tier 1: +2% - Sell 50% (keep runner)
        key_tier1 = f"{symbol}_tier1"
        if gain_pct >= win_pct and key_tier1 not in _alerted and qty > 1:
            sell_qty = max(1, int(qty * 0.5))
            auto_sell(symbol, sell_qty,
                      f"🎯 Tier 1 (+{int(win_pct*100)}%) - Sold {sell_qty}/{qty} shares | Entry: {entry:.2f} | Now: {price:.2f} | Runner: {qty-sell_qty} shares",
                      trigger_basis=TRIGGER_TIER_1,
                      trigger_detail=f"Tier 1 exit: sold {sell_qty}/{qty} at +{gain_pct*100:.1f}% (threshold: +{win_pct*100:.1f}%)")
            _alerted.add(key_tier1)
            # Update position qty for remaining tiers
            positions[symbol]["qty"] = qty - sell_qty
            log.info(f"Tier 1: {symbol} sold {sell_qty}/{qty} at +{gain_pct*100:.1f}%")

        # Tier 2: +5% - Sell 25% of original
        key_tier2 = f"{symbol}_tier2"
        tier2_threshold = win_pct * 2.5  # +5% if win is 2%
        if gain_pct >= tier2_threshold and key_tier2 not in _alerted and qty > 1:
            sell_qty = max(1, int(qty * 0.25))
            auto_sell(symbol, sell_qty,
                      f"🎯 Tier 2 (+{int(tier2_threshold*100)}%) - Sold {sell_qty}/{qty} shares | Runner: {qty-sell_qty} shares",
                      trigger_basis=TRIGGER_TIER_2,
                      trigger_detail=f"Tier 2 exit: sold {sell_qty}/{qty} at +{gain_pct*100:.1f}% (threshold: +{tier2_threshold*100:.1f}%)")
            _alerted.add(key_tier2)
            positions[symbol]["qty"] = qty - sell_qty
            log.info(f"Tier 2: {symbol} sold {sell_qty}/{qty} at +{gain_pct*100:.1f}%")

        # Tier 3: +10% - Sell remaining
        key_tier3 = f"{symbol}_tier3"
        tier3_threshold = win_pct * 5  # +10% if win is 2%
        if gain_pct >= tier3_threshold and key_tier3 not in _alerted:
            auto_sell(symbol, qty,
                      f"🎯 Tier 3 (+{int(tier3_threshold*100)}%) - Sold ALL remaining {qty} shares | Total realized: +{gain_pct*100:.1f}%",
                      trigger_basis=TRIGGER_TIER_3,
                      trigger_detail=f"Tier 3 exit: sold all {qty} at +{gain_pct*100:.1f}% (threshold: +{tier3_threshold*100:.1f}%)")
            _alerted.add(key_tier3)
            log.info(f"Tier 3: {symbol} sold all {qty} at +{gain_pct*100:.1f}%")

    if updated:
        save_positions(positions)

    # ── Load all picks (from all screens: 09:50, 10:30, 12:00, 13:30) ──────────
    picks_all = load_picks_all()

    # ── Count currently open positions ──────────────────────────────────────
    open_count = sum(1 for p in positions.values() if not p.get("closed"))
    max_positions = r_params.get("max_positions", 3)

    # ── POSITION UPGRADE LOGIC ─────────────────────────────────────────────
    # If we have open positions AND max slots filled, evaluate upgrades
    if open_count >= max_positions:
        # Check if any current pick is significantly worse than new top picks
        current_symbols = [s for s, p in positions.items() if not p.get("closed")]
        for current_sym in current_symbols:
            current_pick = next((p for p in picks_all if p["symbol"].replace(".SR", "") == current_sym), None)
            current_score = current_pick.get("score", 0) if current_pick else 0
            
            # Find best new pick that's NOT currently held
            best_new = None
            for p in picks_all:
                sym = p["symbol"].replace(".SR", "")
                if sym not in current_symbols:
                    best_new = p
                    break
            
            # Get regime-aware position upgrade threshold
            pu_thresh = POSITION_UPGRADE_THRESHOLDS.get(regime_name, 1.3)
            
            if best_new and best_new.get("score", 0) > current_score * pu_thresh:
                # New pick is 30%+ better score — check if it's IN ZONE before selling
                bn_sym = best_new['symbol'].replace(".SR", "")
                bn_price, bn_df, _, bn_vwap = fetch_data(best_new['symbol'])
                
                # FIX 1: Use entry_low/entry_high from root level (not entry_zone object)
                e_lo = best_new.get("entry_low", 0)
                e_hi = best_new.get("entry_high", 0)
                
                # CRITICAL: Only upgrade if new pick is IN ENTRY ZONE
                if e_lo and e_hi and bn_price:
                    if bn_price < e_lo or bn_price > e_hi:
                        log.info(f"Position upgrade BLOCKED: {best_new['symbol']} score is better but OUTSIDE zone [{e_lo:.2f}-{e_hi:.2f}], price={bn_price:.2f}")
                        continue  # Skip this upgrade — new pick not in zone
                
                # FIX 2: VWAP direction check for NEUTRAL/DEFENSIVE regimes
                if regime_name in ["NEUTRAL", "DEFENSIVE"] and bn_df is not None:
                    vwap_dir = calc_vwap_direction(bn_df, window=5)
                    if vwap_dir <= 0:
                        log.info(f"Position upgrade BLOCKED: {best_new['symbol']} VWAP falling in {regime_name} regime (dir={vwap_dir:.4f})")
                        continue  # Skip — don't upgrade into falling momentum
                
                # v4.7: Liquidity direction gate for position upgrade (Phase 3)
                if r_params.get("enable_liquidity", False):
                    cache = _ws_price_cache.get(bn_sym, {})
                    liq_ratio = cache.get("liquidity_ratio", 1.0)
                    liq_min = r_params.get("liquidity_entry_min", 1.2)
                    if liq_ratio < liq_min:
                        log.info(f"Position upgrade BLOCKED: {best_new['symbol']} liquidity_ratio={liq_ratio:.2f} < {liq_min} — insufficient buy pressure for upgrade")
                        continue  # Skip upgrade
                    else:
                        log.info(f"Position upgrade: {best_new['symbol']} liquidity_ratio={liq_ratio:.2f} >= {liq_min} — buy pressure confirmed")
                
                # Zone check passed — proceed with upgrade
                current_price, _, _, _ = fetch_data(f"{current_sym}.SR")
                current_pos = positions[current_sym]
                entry = current_pos.get("entry_price", 0)
                qty = current_pos.get("qty", "?")
                
                if current_price and entry:
                    gain_pct = (current_price - entry) / entry
                    
                    # Only switch if current position is NOT deep underwater
                    if gain_pct >= -0.02:  # Don't switch if down >2%
                        log.info(
                            f"Position upgrade: {current_sym}(score={current_score:.0f}) → "
                            f"{best_new['symbol']}(score={best_new.get('score',0):.0f}). "
                            f"Current P&L: {gain_pct*100:+.1f}%"
                        )
                        tg_send(
                            f"🔄 <b>Position Upgrade</b>\n"
                            f"Closing {current_sym} (score {current_score:.0f}, P&L {gain_pct*100:+.1f}%)\n"
                            f"Opening {best_new['symbol']} (score {best_new.get('score',0):.0f})\n"
                            f"New pick has {best_new.get('score',0)/max(current_score,1):.1f}x momentum"
                        )
                        auto_sell(current_sym, qty, 
                                  f"🔄 Position upgrade — switching to better momentum pick")
                        open_count -= 1  # Free up slot
                        
                        # FIX 3: Immediately buy the new pick (guaranteed)
                        if bn_price and e_lo and e_hi and e_lo <= bn_price <= e_hi:
                            auto_buy(best_new['symbol'], qty, cycle_n=1, max_cyc=max_cycles,
                                    price=bn_price, price_source="position_upgrade",
                                    entry_zone={"e_lo": e_lo, "e_hi": e_hi},
                                    trigger_basis=TRIGGER_POSITION_UPGRADE,
                                    trigger_detail=f"Position upgrade from {current_sym}")
                            open_count += 1  # Track new position
                            log.info(f"Position upgrade complete: Sold {current_sym}, bought {best_new['symbol']} @ {bn_price:.2f}")
                        else:
                            log.warning(f"Position upgrade: Sold {current_sym} but {best_new['symbol']} no longer in zone, skipping buy")
                        
                        # Clear alert for new pick so it can trigger immediately
                        best_sym = best_new["symbol"].replace(".SR", "")
                        _reset_symbol_alerts(best_sym)

    # ── Entry signals for picks ─────────────────────────────────────────────
    # Use smaller size for positions beyond the first 2 (in TRENDING)
    position_idx = 0

    # Load blocked symbols (prevent re-buy after hard close)
    blocked_symbols = set()
    try:
        if os.path.exists(f"{BASE_DIR}/blocked_symbols.txt"):
            with open(f"{BASE_DIR}/blocked_symbols.txt") as f:
                blocked_symbols = set(line.strip() for line in f if line.strip())
    except Exception:
        pass

    # HARD CLOSE BLOCK: After 14:45, block ALL new buys
    # Also check for stand_down file (persists across restarts)
    now_time = datetime.now(RIYADH).time()
    hard_close_triggered = now_time >= HARD_CLOSE_TIME or "hard_close_p2" in _alerted
    stand_down_active = os.path.exists(f"{BASE_DIR}/stand_down")

    if stand_down_active:
        log.info("STAND DOWN mode active - all entry signals blocked")
        return  # Exit slow_poll entirely - no entries allowed

    # ── OPTION B: In-Zone Priority ──────────────────────────────────────────
    # If top 5 picks are ALL out of zone (gapped or below), expand to top 10
    # Sort by actionability = raw_score × zone_bonus
    def _actionability_score(pick):
        """Calculate actionability based on zone proximity."""
        sym = pick.get("symbol", "")
        raw_score = pick.get("score", 0)
        e_lo = pick.get("entry_low", 0)
        e_hi = pick.get("entry_high", 0)

        if e_lo == 0 or e_hi == 0:
            return raw_score * 0.3  # No zone data = low priority

        # Fetch current price for zone check
        try:
            price, _, _, _ = fetch_data(sym)
            if price is None:
                return raw_score * 0.5
        except:
            return raw_score * 0.5

        if e_lo <= price <= e_hi:
            return raw_score * 1.5  # In zone = highest priority
        elif price <= e_hi * 1.02:
            return raw_score * 1.2  # Near zone (within 2% above)
        elif price > e_hi * 1.02:
            return raw_score * 0.3  # Gapped above = low priority
        else:
            return raw_score * 0.5  # Below zone

    # Check how many of top 5 are in or near zone
    top5 = picks_all[:5]
    in_zone_count = 0
    near_zone_count = 0
    for pick in top5:
        e_lo = pick.get("entry_low", 0)
        e_hi = pick.get("entry_high", 0)
        sym = pick.get("symbol", "")
        if e_lo > 0 and e_hi > 0 and sym:
            try:
                price, _, _, _ = fetch_data(sym)
                if price and e_lo <= price <= e_hi:
                    in_zone_count += 1
                elif price and price <= e_hi * 1.02:
                    near_zone_count += 1
            except:
                pass

    # If none of top 5 are in/near zone, re-sort by actionability and expand
    if in_zone_count == 0 and near_zone_count == 0 and len(picks_all) > 5:
        log.info(f"Top 5 all out of zone - re-sorting by actionability and expanding to top 10")
        # Sort all picks by actionability score
        scored_picks = [(p, _actionability_score(p)) for p in picks_all]
        scored_picks.sort(key=lambda x: x[1], reverse=True)
        picks_all = [p for p, _ in scored_picks]
        monitored = picks_all[:10]
        log.info(f"Re-sorted top 10: {', '.join(p['symbol']+'('+str(int(p.get('score',0)))+')' for p in monitored[:5])}")
    else:
        monitored = picks_all[:5]
        log.info(f"Top 5 monitoring: {in_zone_count} in zone, {near_zone_count} near zone")

    for pick in monitored:
        symbol = pick.get("symbol", "")
        if not symbol:
            continue
        base = symbol.replace(".SR", "")

        # BLOCK ALL BUYS AFTER HARD CLOSE
        if hard_close_triggered:
            log.info(f"HARD CLOSE ACTIVE - blocking all entry signals for {base}")
            continue

        # Skip blocked symbols (prevent re-buy)
        if base in blocked_symbols:
            log.info(f"{base} is BLOCKED - skipping entry")
            continue

        # Skip if already in open position for this symbol
        if base in positions and not positions[base].get("closed"):
            continue

        # Skip if max open positions reached
        if open_count >= max_positions:
            log.info(f"Max positions ({max_positions}) reached - skipping {base}")
            break

        # ── Market Open Cooldown (10:00-10:10) ─────────────────────────────
        # Reduced from 15 min to 10 min — 15 min too long, stocks move out of zone
        if now_time < time(10, 10):
            log.info(f"{base} skipped - market open cooldown (before 10:10)")
            continue

        price, df, price_src, ws_vwap = fetch_data(symbol)
        if price is None or df is None:
            continue

        # ── Regime + VWAP Direction Filter ──────────────────────────────────
        # In NEUTRAL/DEFENSIVE, only enter if VWAP is rising
        if regime_name in ["NEUTRAL", "DEFENSIVE"]:
            # Primary: WebSocket incremental VWAP
            vwap_now = ws_vwap
            # Fallback: Calculate from websocket ticks
            if vwap_now is None:
                vwap_now = _calculate_tick_based_vwap(base)
            
            # Final fallback: Use targets/time constraints (no VWAP entry filter)
            if vwap_now is None:
                log.info(f"{base} No VWAP available - using targets/time constraints only")
            else:
                # Use yfinance df for direction only (not for VWAP value)
                vwap_dir = calc_vwap_direction(df, window=5) if df is not None else 0
                if vwap_dir <= 0:
                    log.info(f"{base} skipped - VWAP falling in {regime_name} regime (dir={vwap_dir:.4f})")
                    continue
                else:
                    log.info(f"{base} VWAP rising in {regime_name} (dir={vwap_dir:.4f}) - allowing entry")

        # v4.7: Liquidity direction entry filter (Phase 3)
        if r_params.get("enable_liquidity", False):
            cache = _ws_price_cache.get(base, {})
            liq_ratio = cache.get("liquidity_ratio", 1.0)
            liq_min = r_params.get("liquidity_entry_min", 1.2)
            
            if liq_ratio < liq_min:
                log.info(f"{base} ENTRY BLOCKED: liquidity_ratio={liq_ratio:.2f} < min={liq_min} — insufficient buy pressure")
                continue  # Skip entry
            else:
                log.info(f"{base} liquidity_ratio={liq_ratio:.2f} >= {liq_min} — buy pressure confirmed")

        e_hi = pick.get("entry_high", 0)
        e_lo = pick.get("entry_low", 0)
        if e_hi == 0 or e_lo == 0:
            e_hi = round(price * 1.01, 2)
            e_lo = round(price * 0.99, 2)

        # Skip if gapped significantly above (>2% above zone)
        if e_hi and price > e_hi * 1.02:
            log.info(f"{base} skipped - gapped above entry zone ({price:.2f} > {e_hi:.2f} +2%)")
            continue

        # Determine position size based on index (1st/2nd = full, 3rd+ = alt)
        position_idx += 1
        if position_idx <= 2:
            use_pct = position_pct
        else:
            use_pct = r_params.get("alt_position_pct", position_pct)

        # ── Gap-up entry signal (only in first 30 min) ──────────────────────
        key_gap = f"{base}_gap_entry"
        if e_lo and e_hi and e_lo <= price <= e_hi * 1.02 and key_gap not in _alerted:
            now_time = datetime.now(RIYADH).time()
            if now_time <= time(10, 30):
                day_open = df["Open"].iloc[0] if not df.empty else price
                if price >= day_open * 0.998:
                    use_pct = use_pct  # Use determined size (full or alt)
                    tier = pick.get("source", "PRIMARY").upper()
                    tg_send(
                        f"📈 <b>ENTRY - Gap-Up / In-Zone</b> {base}<b>\n"
                        f"Price: {price:.2f} | Entry zone: {e_lo}-{e_hi}\n"
                        f"Day open: {day_open:.2f} | Currently {'in zone' if price <= e_hi else 'slight gap-up'}\n"
                        f"Regime: {regime_name} | Size: {int(use_pct*100)}% | Max cycles: {max_cycles}\n"
                        f"Tier: {tier} | Pos: {position_idx}/{max_positions}\n"
                        f"→ <code>BUY {base} QTY @ {price:.2f}</code>"
                    )
                    _alerted.add(key_gap)
                    open_count += 1  # Track new position
                    log.info(f"Gap-up entry: {base} price={price:.2f} zone={e_lo}-{e_hi} tier={tier} pos={position_idx}/{max_positions} size={use_pct}")

        # ── VWAP reclaim entry ──────────────────────────────────────────────
        # Use WebSocket incremental VWAP first, fallback to yfinance
        ws_vwap = get_ws_vwap(base)
        vwap = ws_vwap if ws_vwap is not None else calc_vwap(df)
        key_vwap = f"{base}_vwap_entry"
        if vwap and check_vwap_reclaim(df, vwap, symbol) and key_vwap not in _alerted:
            # Check position limit again
            if open_count >= max_positions:
                log.info(f"BLOCKED: {base} - max positions reached ({open_count}/{max_positions}). Pick score: {pick.get('score', 0)}")
                continue
            position_idx += 1
            use_pct = position_pct if position_idx <= 2 else r_params.get("alt_position_pct", position_pct)
            tier = pick.get("source", "PRIMARY").upper()
            tg_send(
                f"📈 <b>ENTRY - VWAP Reclaim</b> {base}\n"
                f"VWAP: {vwap:.2f} | Price: {price:.2f}\n"
                f"Entry zone: {e_lo}-{e_hi}\n"
                f"Regime: {regime_name} | Size: {int(use_pct*100)}% | Max cycles: {max_cycles}\n"
                f"Tier: {tier} | Pos: {position_idx}/{max_positions}\n"
                f"→ <code>BUY {base} QTY @ {e_hi}</code>"
            )
            _alerted.add(key_vwap)
            open_count += 1
            log.info(f"VWAP reclaim: {base} vwap={vwap:.2f} price={price:.2f} tier={tier} pos={position_idx}/{max_positions} size={use_pct}")

            # Execute buy
            capital = load_capital()
            qty = int((capital * use_pct) / price)
            cycle_n = cycles_today.get(base, 0) + 1
            if qty > 0:
                auto_buy(symbol, qty, cycle_n, max_cycles, price, price_source=price_src, entry_zone={"e_lo": e_lo, "e_hi": e_hi},
                         trigger_basis=TRIGGER_VWAP_RECLAIM,
                         trigger_detail=f"VWAP reclaim: price={price:.2f} vwap={vwap:.2f} zone={e_lo}-{e_hi}")

        # ── Fallback: Entry zone hold (for flat/choppy markets) ────────────
        # If price is in entry zone AND holding above VWAP for 2+ candles,
        # enter anyway (relaxed signal for trending stocks that never dipped)
        key_zone_hold = f"{base}_zone_hold"
        if key_zone_hold not in _alerted and e_lo <= price <= e_hi:
            if len(df) >= 3:
                last3 = df.tail(3)
                closes = [float(c) for c in last3["Close"]]
                vols = [float(v) for v in last3["Volume"]]
                avg_vol3 = sum(vols) / len(vols)
                avg_vol_all = float(df["Volume"].mean())

                # Price holding in zone + above VWAP + reasonable volume
                if all(c > vwap for c in closes) and sum(vols) > avg_vol_all * 1.5:
                    if open_count >= max_positions:
                        log.info(f"BLOCKED: {base} - max positions reached ({open_count}/{max_positions}). Pick score: {pick.get('score', 0)}")
                        continue
                    position_idx += 1
                    use_pct = position_pct if position_idx <= 2 else r_params.get("alt_position_pct", position_pct)
                    tier = pick.get("source", "PRIMARY").upper()
                    tg_send(
                        f"📈 <b>ENTRY - Zone Hold</b> {base}\n"
                        f"Price: {price:.2f} holding in zone {e_lo}-{e_hi}\n"
                        f"Above VWAP: {vwap:.2f} for 3 candles\n"
                        f"Regime: {regime_name} | Size: {int(use_pct*100)}% | Max cycles: {max_cycles}\n"
                        f"Tier: {tier} | Pos: {position_idx}/{max_positions}\n"
                        f"→ <code>BUY {base} QTY @ {price:.2f}</code>"
                    )
                    _alerted.add(key_zone_hold)
                    open_count += 1
                    log.info(f"Zone hold: {base} price={price:.2f} zone={e_lo}-{e_hi} vwap={vwap:.2f} tier={tier} pos={position_idx}/{max_positions} size={use_pct}")

                    capital = load_capital()
                    qty = int((capital * use_pct) / price)
                    cycle_n = cycles_today.get(base, 0) + 1
                    if qty > 0:
                        auto_buy(symbol, qty, cycle_n, max_cycles, price, price_source=price_src, entry_zone={"e_lo": e_lo, "e_hi": e_hi},
                         trigger_basis=TRIGGER_PICK_ENTRY,
                         trigger_detail=f"Zone hold: price={price:.2f} vwap={vwap:.2f} zone={e_lo}-{e_hi}")

        # ── Breakout entry ──────────────────────────────────────────────────
        key_break = f"{base}_breakout"
        if check_breakout(df) and key_break not in _alerted:
            # Check position limit again
            if open_count >= max_positions:
                log.info(f"BLOCKED: {base} - max positions reached ({open_count}/{max_positions}). Pick score: {pick.get('score', 0)}")
                continue
            position_idx += 1
            use_pct = position_pct if position_idx <= 2 else r_params.get("alt_position_pct", position_pct)
            tier = pick.get("source", "PRIMARY").upper()
            tg_send(
                f"🚀 <b>ENTRY - Breakout</b> {base}\n"
                f"Price: {price:.2f} (above prior high + volume surge)\n"
                f"Regime: {regime_name} | Size: {int(use_pct*100)}% | Max cycles: {max_cycles}\n"
                f"Tier: {tier} | Pos: {position_idx}/{max_positions}\n"
                f"→ <code>BUY {base} QTY @ {price:.2f}</code>"
            )
            _alerted.add(key_break)
            open_count += 1
            log.info(f"Breakout: {base} price={price:.2f} tier={tier} pos={position_idx}/{max_positions} size={use_pct}")

            # Execute buy
            capital = load_capital()
            qty = int((capital * use_pct) / price)
            cycle_n = cycles_today.get(base, 0) + 1
            if qty > 0:
                auto_buy(symbol, qty, cycle_n, max_cycles, price,
                         trigger_basis=TRIGGER_PICK_ENTRY,
                         trigger_detail=f"Breakout: price={price:.2f} above prior high + volume surge")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    now_time = datetime.now(RIYADH).time()
    if not (MARKET_OPEN <= now_time <= MARKET_CLOSE):
        log.info(f"Outside market hours ({now_time}) - exiting.")
        sys.exit(0)

    log.info("Price poller started.")
    tg_send(
        f"🔍 Price poller live - fast watch every {FAST_INTERVAL}s, "
        f"price scan every {SLOW_INTERVAL//60}min.\n"
        f"Fallback logic: monitoring top 2 picks until 10:30, then top 5 at 25% if idle."
    )

    # Start background WS price listener (TickerChart CDP feed)
    _start_ws_listener()
    time_mod.sleep(3)   # brief warm-up so cache has initial prices

    try:
        regime = get_current_regime()
    except Exception as e:
        log.warning(f"Regime load failed: {e} - using NEUTRAL")
        regime = {"regime": "NEUTRAL", "params": {"strategy": "B", "max_cycles": 2, "position_pct": 0.40}}

    max_cycles = regime['params'].get('max_cycles', regime['params'].get('max_positions', 2))
    log.info(
        f"Regime: {regime['regime']} | "
        f"max_cycles={max_cycles} | "
        f"position_pct={regime['params']['position_pct']}"
    )

    last_slow        = 0.0   # epoch seconds of last slow poll
    last_regime_chk  = 0.0

    while True:
        now_time = datetime.now(RIYADH).time()
        if now_time > MARKET_CLOSE:
            log.info("Market closed - poller exiting.")
            tg_send("🔕 Price poller stopped (market closed).")
            break

        now_epoch = time_mod.time()

        # ── Regime re-check every 30 minutes ─────────────────────────────────
        if now_epoch - last_regime_chk >= 1800:
            try:
                regime = classify_intraday()
                last_regime_chk = now_epoch
                log.info(f"Regime updated: {regime['regime']}")
            except Exception as e:
                log.warning(f"Intraday regime re-check failed: {e}")

        # ── Fast poll - every 10s, no network ────────────────────────────────
        try:
            fast_poll(regime)
        except Exception as e:
            log.error(f"fast_poll error: {e}")

        # ── Slow poll - every 5 min, yfinance ────────────────────────────────
        if now_epoch - last_slow >= SLOW_INTERVAL:
            try:
                slow_poll(regime)
                last_slow = now_epoch
                log.info("Slow poll done.")
            except Exception as e:
                import traceback
                log.error(f"slow_poll error: {e}\n{traceback.format_exc()}")

        time_mod.sleep(FAST_INTERVAL)


if __name__ == "__main__":
    main()

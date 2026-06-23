#!/usr/bin/env python3
"""
order_helpers.py — Order lifecycle helpers for v4.4

Shared by:
  - poller.py (auto_buy/auto_sell)
  - bot.py (manual /BUY /SELL commands)
  - bookkeeper.py (Phase 3: reconciliation)

Schema (orders.json):
  {
    "12345": {  # str(orderId) is the key
      "initiated_at": ISO timestamp,
      "initiated_by": "auto_buy" | "auto_sell" | "manual-buy" | "manual-sell" | "derayah-direct",
      "trigger_basis": "pick_entry" | "hard_stop" | "trailing_stop" | "vwap_breakdown" | "target_reached" | "scratch_sell" | ...,
      "trigger_detail": "Optional human-readable detail (e.g., 'Hard stop -7% | Entry: 100.00 | Now: 93.00')",
      "symbol": "2200",            # base, no .SR
      "side": "BUY" | "SELL",
      "qty": 25,
      "price": 7.66,
      "type": "MARKET" | "LIMIT",
      "status": 0,                 # see STATUS_* constants
      "updated_at": ISO timestamp,
    }
  }

Status codes (matches bot.py:755 + bookkeeper.py):
  0  = INITIATED  (poller/bot-written only, never from Derayah)
  1  = PLACED     (pending in Derayah)
  2  = PLACED     (partial — parent has unfilled qty, child rows exist)
  3  = FILLED     (terminal)
  4  = CANCELLED  (terminal)
  5  = REJECTED   (terminal)
  6  = EXPIRED    (terminal)
  7  = CANCELLED  (terminal, user-cancelled)
  8  = REJECTED   (terminal, system-rejected)
  12 = FILLED     (terminal, bookkeeper's code)

Writer pattern (same as positions.json/capital.json):
  - Poller/bot: write INITIATED immediately after place_order success
  - Bookkeeper (Phase 3): overwrite with Derayah's Order/List truth every 5 min
"""

import json
import os
import logging
import subprocess
from datetime import datetime
from pathlib import Path

import pytz

import fcntl

log = logging.getLogger(__name__)

# ─── Paths ─────────────────────────────────────────────────────────────────

BASE_DIR = "/home/mino/tasi-exec"
ORDERS_FILE    = f"{BASE_DIR}/orders.json"
POSITIONS_FILE = f"{BASE_DIR}/positions.json"
RIYADH = pytz.timezone("Asia/Riyadh")

# ─── Status code constants ─────────────────────────────────────────────────

STATUS_INITIATED = 0
STATUS_PLACED    = 1
STATUS_PARTIAL   = 2
STATUS_FILLED    = 3
STATUS_CANCELLED = 4
STATUS_REJECTED  = 5
STATUS_EXPIRED   = 6
TERMINAL_STATUSES = {3, 4, 5, 6, 7, 8, 12}

# Friendly names for display
STATUS_NAMES = {
    0:  "INITIATED",
    1:  "PLACED",
    2:  "PARTIAL",
    3:  "FILLED",
    4:  "CANCELLED",
    5:  "REJECTED",
    6:  "EXPIRED",
    7:  "CANCELLED",
    8:  "REJECTED",
    12: "FILLED",
}

# ─── Trigger basis constants ───────────────────────────────────────────────
# WHY was this order placed? Used for order log analysis and debugging.

TRIGGER_PICK_ENTRY       = "pick_entry"       # Initial buy from screener
TRIGGER_CYCLE_RECYCLE    = "cycle_recycle"    # Rebuy after win (same symbol)
TRIGGER_CYCLE_SWITCH     = "cycle_switch"     # Sell to buy better pick
TRIGGER_HARD_STOP        = "hard_stop"        # Hard stop loss hit
TRIGGER_TRAILING_STOP    = "trailing_stop"    # Trailing stop hit
TRIGGER_TIME_STOP        = "time_stop"        # Time-based stop
TRIGGER_VWAP_BREAKDOWN   = "vwap_breakdown"   # Price broke below VWAP
TRIGGER_VWAP_RECLAIM     = "vwap_reclaim"     # Buy on VWAP reclaim
TRIGGER_TARGET_REACHED   = "target_reached"   # Profit target hit
TRIGGER_TIER_1           = "tier_1"           # Tier 1 partial exit (+2%)
TRIGGER_TIER_2           = "tier_2"           # Tier 2 partial exit (+5%)
TRIGGER_TIER_3           = "tier_3"           # Tier 3 full exit (+10%)
TRIGGER_POSITION_UPGRADE = "position_upgrade" # Sell to upgrade to better pick
TRIGGER_SCRATCH_SELL     = "scratch_sell"     # Momentum cycling (non-winner)
TRIGGER_MANUAL_COMMAND   = "manual_command"   # /BUY /SELL from Telegram
TRIGGER_HARD_CLOSE       = "hard_close"       # End of day forced close
TRIGGER_BLOCK_REMOVAL    = "block_removal"    # Stand-down or block removal
TRIGGER_LIQUIDITY_EXIT   = "liquidity_exit"   # v4.7: Liquidity-confirmed breakdown exit
TRIGGER_LIQUIDITY_HOLD   = "liquidity_hold"   # v4.7: Liquidity held position despite breakdown
TRIGGER_UNKNOWN          = "unknown"          # Could not determine trigger

# Human-readable trigger descriptions
TRIGGER_DESCRIPTIONS = {
    TRIGGER_PICK_ENTRY:       "Screener pick entry",
    TRIGGER_CYCLE_RECYCLE:    "Win cycle recycle",
    TRIGGER_CYCLE_SWITCH:     "Win cycle switch to better pick",
    TRIGGER_HARD_STOP:        "Hard stop loss",
    TRIGGER_TRAILING_STOP:    "Trailing stop",
    TRIGGER_TIME_STOP:        "Time stop",
    TRIGGER_VWAP_BREAKDOWN:   "VWAP breakdown",
    TRIGGER_VWAP_RECLAIM:     "VWAP reclaim",
    TRIGGER_TARGET_REACHED:   "Profit target",
    TRIGGER_TIER_1:           "Tier 1 partial (+2%)",
    TRIGGER_TIER_2:           "Tier 2 partial (+5%)",
    TRIGGER_TIER_3:           "Tier 3 full exit (+10%)",
    TRIGGER_POSITION_UPGRADE: "Position upgrade",
    TRIGGER_SCRATCH_SELL:     "Scratch sell (momentum cycling)",
    TRIGGER_MANUAL_COMMAND:   "Manual Telegram command",
    TRIGGER_HARD_CLOSE:       "End of day hard close",
    TRIGGER_BLOCK_REMOVAL:    "Block removal",
    TRIGGER_LIQUIDITY_EXIT:   "Liquidity-confirmed breakdown exit",
    TRIGGER_LIQUIDITY_HOLD:   "Liquidity hold (breakdown overridden)",
    TRIGGER_UNKNOWN:          "Unknown trigger",
}


# ─── File I/O ──────────────────────────────────────────────────────────────

def load_orders() -> dict:
    """Load orders.json. Returns dict of {order_id_str: order_dict}."""
    return _locked_load(ORDERS_FILE, default=dict)


def save_orders(orders: dict):
    """Save orders.json. Atomic write via temp file + file locking."""
    tmp = ORDERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(orders, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ORDERS_FILE)


def _locked_load(path: str, default=None):
    """Load JSON with advisory file lock (read lock)."""
    if not os.path.exists(path):
        return default() if callable(default) else default
    try:
        with open(path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"{path} corrupted: {e} — starting fresh")
        return default() if callable(default) else default


def load_positions() -> dict:
    """Load positions.json (needed for effective_holdings)."""
    return _locked_load(POSITIONS_FILE, default=dict)


# ─── Core order operations ─────────────────────────────────────────────────

def write_order_initiated(order_id, action: str, symbol: str, qty: int,
                          price: float, order_type: str,
                          initiated_by: str = "auto_buy",
                          trigger_basis: str = TRIGGER_UNKNOWN,
                          trigger_detail: str = "") -> str:
    """
    Write INITIATED entry to orders.json right after place_order success.
    Returns the normalized order_id (str).

    Args:
        order_id: Can be int or str — converted to str for JSON key consistency.
        action: "BUY" or "SELL"
        symbol: Symbol with or without .SR
        qty: Number of shares
        price: Order price (0.0 for market orders if unknown)
        order_type: "MARKET" or "LIMIT"
        initiated_by: Who initiated (auto_buy, auto_sell, manual-buy, etc.)
        trigger_basis: WHY was this order placed (pick_entry, hard_stop, etc.)
        trigger_detail: Optional human-readable detail for debugging

    Returns:
        Normalized order_id (str)
    """
    orders = load_orders()
    oid = str(order_id)
    base = symbol.replace(".SR", "")
    now = datetime.now(RIYADH).isoformat()
    orders[oid] = {
        "initiated_at": now,
        "initiated_by": initiated_by,
        "trigger_basis": trigger_basis,
        "trigger_detail": trigger_detail,
        "symbol": base,
        "side": action,
        "qty": qty,
        "price": price or 0.0,
        "type": order_type,
        "status": STATUS_INITIATED,
        "updated_at": now,
    }
    save_orders(orders)
    trigger_desc = TRIGGER_DESCRIPTIONS.get(trigger_basis, trigger_basis)
    log.info(f"order INITIATED: {oid} {action} {qty}×{base} @ {price} ({order_type}) [{initiated_by}] trigger={trigger_basis} ({trigger_desc})")
    return oid


def effective_holdings(symbol: str, positions: dict = None, orders: dict = None) -> int:
    """
    Compute effective holdings for double-sell pre-check.
    Returns int qty: filled - outstanding_sell + outstanding_buy.
    
    Optional pre-loaded positions/orders dicts for performance (avoids re-reading files).
    """
    base = symbol.replace(".SR", "")
    if positions is None:
        positions = load_positions()
    filled_qty = positions.get(base, {}).get("qty", 0)

    if orders is None:
        orders = load_orders()
    outstanding_buy = sum(
        o["qty"] for o in orders.values()
        if o.get("symbol") == base
        and o.get("side") == "BUY"
        and o.get("status") in (STATUS_INITIATED, STATUS_PLACED, STATUS_PARTIAL)
    )
    outstanding_sell = sum(
        o["qty"] for o in orders.values()
        if o.get("symbol") == base
        and o.get("side") == "SELL"
        and o.get("status") in (STATUS_INITIATED, STATUS_PLACED, STATUS_PARTIAL)
    )
    return filled_qty - outstanding_sell + outstanding_buy


def get_outstanding_orders() -> dict:
    """Return all orders with non-terminal status (INITIATED/PLACED/PARTIAL)."""
    orders = load_orders()
    return {
        oid: o for oid, o in orders.items()
        if o.get("status") in (STATUS_INITIATED, STATUS_PLACED, STATUS_PARTIAL)
    }


def get_booked_capital() -> float:
    """
    Sum of (qty × price) for all non-terminal orders.
    Used for the 'booked' bucket in capital.json (Phase 4).
    """
    outstanding = get_outstanding_orders()
    return sum(
        o.get("qty", 0) * o.get("price", 0)
        for o in outstanding.values()
    )


def get_status_name(code: int) -> str:
    """Map status code to friendly name."""
    return STATUS_NAMES.get(code, f"UNKNOWN({code})")


# ─── Bookkeeper sync trigger ──────────────────────────────────────────────

def trigger_bookkeeper_sync():
    """
    Trigger bookkeeper quick_refresh in background.
    Ported from bot.py:429 — same pattern used by bot for manual trades.
    """
    try:
        subprocess.Popen(
            ["/usr/bin/python3", "-c",
             "import sys; sys.path.insert(0, '/home/mino/tasi-exec'); "
             "import bookkeeper; bookkeeper.quick_refresh()"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Bookkeeper sync triggered (background)")
    except Exception as e:
        log.warning(f"Failed to trigger bookkeeper sync: {e}")


# ─── Self-test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Smoke test — uses temp files, never touches production
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="order_helpers_test_")
    orig_orders_file = ORDERS_FILE
    orig_positions_file = POSITIONS_FILE
    
    # Override paths for testing
    globals()["ORDERS_FILE"] = f"{tmpdir}/orders.json"
    globals()["POSITIONS_FILE"] = f"{tmpdir}/positions.json"
    
    try:
        print("Testing order_helpers.py...")
        save_orders({})
        oid = write_order_initiated("100", "BUY", "2200.SR", 25, 7.66, "LIMIT", "manual-buy")
        orders = load_orders()
        assert "100" in orders
        assert orders["100"]["status"] == STATUS_INITIATED
        assert orders["100"]["initiated_by"] == "manual-buy"
        assert effective_holdings("2200.SR") == 25  # 0 filled + 25 buy
        outstanding = get_outstanding_orders()
        assert "100" in outstanding
        booked = get_booked_capital()
        assert booked == 25 * 7.66
        print(f"  ✅ write_order_initiated: order {oid} created")
        print(f"  ✅ effective_holdings: 25 outstanding buys = 25")
        print(f"  ✅ get_outstanding_orders: 1 outstanding")
        print(f"  ✅ get_booked_capital: {booked:.2f} SAR")
        print(f"  ✅ All status code mappings: {STATUS_NAMES}")
        print("All smoke tests passed ✅")
    finally:
        # Restore paths
        globals()["ORDERS_FILE"] = orig_orders_file
        globals()["POSITIONS_FILE"] = orig_positions_file
        # Clean up temp dir
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

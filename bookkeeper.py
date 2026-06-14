#!/usr/bin/env python3
"""
TASI Bookkeeper — Single Source of Truth
=======================================

Handles:
1. Capital sync from Derayah dashboard/API
2. Position sync from Derayah API
3. Trade logging and reconciliation
4. P&L calculation and fee tracking
5. Historical capital table
6. Daily/weekly/monthly reports

Called by:
- Cron every 30 minutes (full dashboard sync)
- Bot after each trade (quick API refresh)
- Manual run for reports/reconciliation

Bot NEVER writes capital.json or positions.json.
Bot only READS these files.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

import requests
import logging
import time

# ─── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)




# ─── Config ─────────────────────────────────────────────────────────────────
BASE_DIR       = "/home/mino/tasi-exec"
CAPITAL_FILE   = f"{BASE_DIR}/capital.json"
POSITIONS_FILE = f"{BASE_DIR}/positions.json"
TOKEN_FILE     = f"{BASE_DIR}/derayah_tokens.json"
TRADE_BOOK     = f"{BASE_DIR}/trade_book.json"
HISTORY_DIR    = f"{BASE_DIR}/history"
PORTFOLIO      = 2063853
RIYADH_TZ      = timezone(timedelta(hours=3))

# Ensure history dir exists
os.makedirs(HISTORY_DIR, exist_ok=True)

# Telegram bot (for announcing order status changes)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # Set via environment variable — never hardcode
TELEGRAM_CHAT_ID   = -5235925419  # TASI Execution group

# Ensure history dir exists
os.makedirs(HISTORY_DIR, exist_ok=True)

# ─── Telegram helper ──────────────────────────────────────────────────────

def _tg_send(text: str, chat_id: int = None, retries: int = 3) -> bool:
    """Send a message to Telegram with retry logic and proper error logging.
    
    Args:
        text: Message text (HTML parse mode)
        chat_id: Override chat ID (default: TASI_EXEC_GROUP)
        retries: Number of retry attempts (default: 3)
        
    Returns:
        True if message was delivered successfully, False otherwise
    """
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat:
        log.error("[_tg_send] Missing TELEGRAM_BOT_TOKEN or CHAT_ID — cannot send")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=15)
            
            # Check HTTP status
            if r.status_code != 200:
                log.warning(f"[_tg_send] Attempt {attempt}/{retries}: HTTP {r.status_code} — {r.text[:100]}")
                if attempt < retries:
                    time.sleep(2 ** attempt)  # Exponential backoff: 2, 4, 8 seconds
                    continue
                return False
            
            # Check Telegram API response
            resp = r.json()
            if resp.get("ok"):
                log.info(f"[_tg_send] Message delivered to {target_chat}")
                return True
            else:
                error_desc = resp.get("description", "Unknown error")
                log.error(f"[_tg_send] Attempt {attempt}/{retries}: Telegram API error: {error_desc}")
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                return False
                
        except requests.exceptions.Timeout:
            log.warning(f"[_tg_send] Attempt {attempt}/{retries}: Timeout after 15s")
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            log.error("[_tg_send] All retry attempts exhausted — message NOT delivered")
            return False
            
        except requests.exceptions.ConnectionError as e:
            log.warning(f"[_tg_send] Attempt {attempt}/{retries}: Connection error — {e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            log.error("[_tg_send] All retry attempts exhausted — message NOT delivered")
            return False
            
        except Exception as e:
            log.error(f"[_tg_send] Unexpected error on attempt {attempt}/{retries}: {e}", exc_info=True)
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            log.error("[_tg_send] All retry attempts exhausted — message NOT delivered")
            return False
    
    return False

# ─── Derayah status code → our status code mapping ───────────────────────────

# Phase 4.4: Order lifecycle reconciliation
# Phase 5: Local history I/O (Daily P&L + Order History CSVs)
sys.path.insert(0, BASE_DIR)
try:
    from order_helpers import (
        load_orders, save_orders, ORDERS_FILE,
        STATUS_INITIATED, STATUS_PLACED, STATUS_PARTIAL, STATUS_FILLED,
        STATUS_CANCELLED, STATUS_REJECTED, STATUS_EXPIRED, TERMINAL_STATUSES,
    )
    from history_io import append_order_history, append_daily_pnl
    HISTORY_IO_AVAILABLE = True
    ORDER_HELPERS_AVAILABLE = True
except ImportError as e:
    print(f"[WARN] order_helpers/history_io not available: {e}")
    HISTORY_IO_AVAILABLE = False
    ORDER_HELPERS_AVAILABLE = False
    # Fallback constants
    STATUS_INITIATED = 0
    STATUS_PLACED    = 1
    STATUS_PARTIAL   = 2
    STATUS_FILLED    = 3
    STATUS_CANCELLED = 4
    STATUS_REJECTED  = 5
    STATUS_EXPIRED   = 6
    TERMINAL_STATUSES = {3, 4, 5, 6, 7, 8, 12}
    ORDERS_FILE = f"{BASE_DIR}/orders.json"


def map_derayah_status(derayah_status_code: int) -> int:
    """
    Map Derayah's orderStatusId (or status) field to our internal code.
    Derayah codes (from bot.py:755 + bookkeeper.py):
      1 = pending, 2 = partial, 3-8 = terminal, 12 = filled (bookkeeper)
    """
    mapping = {
        1: STATUS_PLACED,      # pending
        2: STATUS_PARTIAL,     # partial
        3: STATUS_FILLED,      # filled
        4: STATUS_CANCELLED,   # cancelled
        5: STATUS_REJECTED,    # rejected
        6: STATUS_EXPIRED,     # expired
        7: STATUS_CANCELLED,   # user-cancelled
        8: STATUS_REJECTED,    # system-rejected
        12: STATUS_FILLED,     # filled (bookkeeper's code)
    }
    return mapping.get(derayah_status_code, STATUS_PLACED)

# Friendly status names
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

def _status_name(code: int) -> str:
    return STATUS_NAMES.get(code, f"UNKNOWN({code})")

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(RIYADH_TZ).isoformat()

def _today() -> str:
    return datetime.now(RIYADH_TZ).strftime("%m-%d")

def load_tokens() -> dict:
    with open(TOKEN_FILE) as f:
        return json.load(f)

def api_headers(tokens: dict = None) -> dict:
    if tokens is None:
        tokens = load_tokens()
    return {
        "Authorization": f"Bearer {tokens.get('TC_DERAYAH', '')}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def api_call(method: str, path: str, body: dict = None, tokens: dict = None) -> dict:
    if tokens is None:
        tokens = load_tokens()
    url = f"https://api.derayah.com/trading/{path}"
    try:
        if method == "GET":
            r = requests.get(url, headers=api_headers(tokens), timeout=15)
        else:
            r = requests.post(url, headers=api_headers(tokens), json=body or {}, timeout=15)
        if r.status_code == 200:
            return r.json()
        print(f"API error {r.status_code}: {path}")
        return {}
    except Exception as e:
        print(f"API exception {path}: {e}")
        return {}

def get_positions_api(tokens: dict = None) -> list:
    resp = api_call("POST", "UserPosition/ListPositions", {
        "currencyCode": 1,
        "exchangeCodes": [98, 99],
        "portfolio": PORTFOLIO,
    }, tokens)
    return resp.get("data", {}).get("tradingAccountPositionInfoList", [])

def get_orders_api(date_str: str = None, tokens: dict = None) -> list:
    if date_str is None:
        date_str = _today()
    resp = api_call("POST", "Order/List", {
        "portfolio": PORTFOLIO,
        "orderStatusGroup": 0,
        "isIntraDay": True,
        "exchanges": [98, 99],
    }, tokens)
    return resp.get("data", {}).get("orders", [])

def get_regular_orders(date_str: str = None, tokens: dict = None) -> list:
    """Get T+2 (regular) orders for a date."""
    if date_str is None:
        date_str = _today()
    resp = api_call("POST", "Order/List", {
        "portfolio": PORTFOLIO,
        "orderStatusGroup": 0,
        "isIntraDay": False,
        "exchanges": [98, 99],
    }, tokens)
    return resp.get("data", {}).get("orders", [])

# ─── Capital Sync ───────────────────────────────────────────────────────────

def scrape_dashboard_cash(tokens: dict = None) -> dict:
    """Scrape Derayah dashboard for cash breakdown."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.connect_over_cdp("http://127.0.0.1:18801", timeout=5000)
            ctx = br.contexts[0]
            for page in ctx.pages:
                if "derayah" in page.url.lower() and "dashboard" in page.url.lower():
                    page.wait_for_timeout(2000)
                    text = page.inner_text("body")
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    
                    cash_data = {"grand_total": None, "money_transfer": None, "cash_accounts": None}
                    for i, line in enumerate(lines):
                        ll = line.lower()
                        if "grand total" in ll and i + 1 < len(lines):
                            m = re.search(r'(\d+\.\d{2})', lines[i + 1])
                            if m: cash_data["grand_total"] = float(m.group(1))
                        elif "money transfer" in ll and i + 1 < len(lines):
                            m = re.search(r'(\d+\.\d{2})', lines[i + 1])
                            if m: cash_data["money_transfer"] = float(m.group(1))
                        elif "total cash" in ll and i + 1 < len(lines):
                            m = re.search(r'(\d+\.\d{2})', lines[i + 1])
                            if m: cash_data["cash_accounts"] = float(m.group(1))
                    
                    return {"success": True, **cash_data, "source": "dashboard"}
            return {"success": False, "error": "Dashboard tab not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def sync_capital() -> dict:
    """Full capital sync from dashboard. Called by cron."""
    print(f"[{_now()}] Capital sync starting...")
    
    scrape = scrape_dashboard_cash()
    positions = get_positions_api()
    
    if scrape.get("success") and scrape.get("grand_total"):
        grand_total = scrape["grand_total"]
        money_transfer = scrape.get("money_transfer", 0) or 0
        cash_accounts = scrape.get("cash_accounts", 0) or 0
        api_invested = sum(p.get("cost", 0) for p in positions)
        invested = cash_accounts if cash_accounts > 0 and not api_invested else api_invested
        available = money_transfer or (grand_total - invested)
        
        capital = {
            "available_capital": round(available, 2),
            "updated_at": _now(),
            "source": "derayah-dashboard-sync",
            "grand_total": round(grand_total, 2),
            "securities_value": round(invested, 2),
            "invested": round(invested, 2),
            "money_transfer": round(money_transfer, 2),
            "total_fees": 0,
            "cash_breakdown": {
                "total_cash": round(grand_total, 2),
                "money_transfer": round(money_transfer, 2),
                "cash_accounts": round(cash_accounts, 2),
            },
        }
    else:
        # Fallback
        print(f"Dashboard scrape failed: {scrape.get('error')}. Using API fallback.")
        api_invested = sum(p.get("cost", 0) for p in positions)
        try:
            with open(CAPITAL_FILE) as f:
                existing = json.load(f)
            grand_total = existing.get("grand_total", 1000)
        except:
            grand_total = 1000
        
        available = grand_total - api_invested
        capital = {
            "available_capital": round(available, 2),
            "updated_at": _now(),
            "source": "derayah-api-fallback",
            "grand_total": round(grand_total, 2),
            "securities_value": round(api_invested, 2),
            "invested": round(api_invested, 2),
            "money_transfer": round(available, 2),
            "total_fees": 0,
        }
    
    with open(CAPITAL_FILE, "w") as f:
        json.dump(capital, f, indent=2)
    
    # Record to history
    _record_capital_history(capital)
    
    print(f"  Grand Total: {capital['grand_total']}, Available: {capital['available_capital']}, Invested: {capital['invested']}")
    return capital

def _record_capital_history(capital: dict):
    """Append capital snapshot to historical table."""
    today = _today()
    hist_file = f"{HISTORY_DIR}/capital_{today}.jsonl"
    
    entry = {
        "timestamp": _now(),
        "grand_total": capital.get("grand_total"),
        "available": capital.get("available_capital"),
        "invested": capital.get("invested"),
    }
    
    with open(hist_file, "a") as f:
        f.write(json.dumps(entry) + "\n")

def quick_refresh() -> dict:
    """Quick sync with Derayah truth. Called by bot/poller after trades.
    
    Capital: from dashboard scraping (Grand Total, Money Transfer)
    Positions/Orders: from Derayah API
    """
    # ── 1. Get capital from dashboard scrape ──────────────────────────
    scrape = scrape_dashboard_cash()
    
    if scrape.get("success") and scrape.get("grand_total"):
        grand_total = scrape["grand_total"]
        money_transfer = scrape.get("money_transfer", 0) or 0
        # Equity = Grand Total - Available (Money Transfer)
        equity = grand_total - money_transfer
        source = "derayah-dashboard-scrape"
        print(f"[{_now()}] Dashboard scrape OK: grand_total={grand_total}, money_transfer={money_transfer}")
    else:
        # Fallback: read from existing capital.json
        print(f"[{_now()}] Dashboard scrape failed: {scrape.get('error')}. Using file fallback.")
        try:
            with open(CAPITAL_FILE) as f:
                existing = json.load(f)
            grand_total = existing.get("grand_total", 1000)
            money_transfer = existing.get("available_capital", 0)
            equity = existing.get("invested", 0)
        except:
            grand_total = 1000
            money_transfer = 0
            equity = 0
        source = "file-fallback"
    
    # ── 2. Get positions/orders from API ───────────────────────────────
    positions = get_positions_api()
    invested = sum(p.get("cost", 0) for p in positions)
    
    # Override equity with API invested if more accurate
    # But keep dashboard grand_total as source of truth
    if invested > 0:
        equity = invested
    
    capital = {
        "available_capital": round(money_transfer, 2),
        "updated_at": _now(),
        "source": source,
        "grand_total": round(grand_total, 2),
        "securities_value": round(equity, 2),
        "invested": round(equity, 2),
        "money_transfer": round(money_transfer, 2),
        "total_fees": 0,
        "cash_breakdown": {
            "total_cash": round(grand_total, 2),
            "money_transfer": round(money_transfer, 2),
            "cash_accounts": round(equity, 2),
        },
    }
    
    # Add 3-bucket fields (Phase 4) — equity/booked/cash
    if ORDER_HELPERS_AVAILABLE:
        try:
            from order_helpers import get_booked_capital
            capital["equity"] = round(equity, 2)
            capital["booked"] = round(get_booked_capital(), 2)
            capital["cash_3bucket"] = round(money_transfer, 2)
            capital["total_3bucket"] = round(equity + get_booked_capital() + money_transfer, 2)
        except Exception as e:
            print(f"[{_now()}] 3-bucket calc failed: {e}")

    with open(CAPITAL_FILE, "w") as f:
        json.dump(capital, f, indent=2)
    print(f"[{_now()}] Capital synced: grand_total={grand_total}, money_transfer={money_transfer}, equity={equity} (source: {source})")

    # Sync positions too
    sync_positions()

    # Phase 4.4: Reconcile orders.json with Derayah truth
    try:
        reconcile_orders()
    except Exception as e:
        print(f"[{_now()}] reconcile_orders in quick_refresh failed: {e}")

    return capital

# ─── Position Sync ──────────────────────────────────────────────────────────

def sync_positions() -> dict:
    """Sync positions.json with Derayah API."""
    actual = get_positions_api()
    
    try:
        with open(POSITIONS_FILE) as f:
            local = json.load(f)
    except:
        local = {}
    
    actual_map = {}
    for p in actual:
        sym = str(p.get("symbol", ""))
        qty = p.get("quantity", 0)
        cost = p.get("cost", 0)
        free_qty = p.get("freeQuantity", qty)
        if qty > 0:
            actual_map[sym] = {
                "qty": qty, "free_qty": free_qty, "cost": cost,
                "avg_price": round(cost / qty, 3) if qty > 0 else 0,
            }
    
    # Mark closed
    for sym, pos in list(local.items()):
        if not pos.get("closed") and sym not in actual_map:
            local[sym]["closed"] = True
            local[sym]["qty"] = 0
            local[sym]["close_time"] = _now()
            local[sym]["sync_note"] = "closed_by_bookkeeper"
    
    # Add/update
    for sym, a in actual_map.items():
        if sym not in local:
            local[sym] = {
                "symbol": sym, "entry_price": a["avg_price"], "qty": a["qty"],
                "free_qty": a["free_qty"], "entry_time": _now(),
                "peak_price": a["avg_price"], "closed": False,
                "price_source": "derayah-api", "signal": "bookkeeper",
                "cost": a["cost"], "sync_note": "added_by_bookkeeper",
            }
        elif local[sym].get("closed"):
            local[sym]["closed"] = False
            local[sym]["qty"] = a["qty"]
            local[sym]["free_qty"] = a["free_qty"]
            local[sym]["cost"] = a["cost"]
            local[sym]["entry_price"] = a["avg_price"]
            local[sym]["entry_time"] = _now()
            local[sym]["sync_note"] = "reopened"
        elif local[sym].get("qty") != a["qty"]:
            local[sym]["qty"] = a["qty"]
            local[sym]["free_qty"] = a["free_qty"]
            local[sym]["cost"] = a["cost"]
    
    with open(POSITIONS_FILE, "w") as f:
        json.dump(local, f, indent=2)
    
    return local

# ─── Trade Logging ──────────────────────────────────────────────────────────

def load_trade_book() -> dict:
    if os.path.exists(TRADE_BOOK):
        with open(TRADE_BOOK) as f:
            return json.load(f)
    return {"trades": [], "daily_summary": {}, "version": "2.0"}

def save_trade_book(book: dict):
    with open(TRADE_BOOK, "w") as f:
        json.dump(book, f, indent=2, default=str)

# ─── Phase 4.4: Order lifecycle reconciliation ─────────────────────────────

def reconcile_orders() -> dict:
    """
    Overwrite orders.json with Derayah's Order/List truth.
    Announce status changes (INITIATED → PLACED, PLACED → FILLED, etc.)
    in the TASI group.

    Per A A 2026-06-11:
    - INITIATED that's missing from API for 1 cycle (5 min) → mark REJECTED
    - PLACED that disappears from API → mark EXPIRED (probably EOD cleanup)
    - Each Derayah execution = its own row in orders.json
    - Partial fill: parent stays as PARTIAL, each child fill is a new row

    Returns summary dict with counts of transitions.
    """
    if not ORDER_HELPERS_AVAILABLE:
        return {"error": "order_helpers not available"}

    # 1. Load current local state
    local_orders = load_orders()

    # 2. Fetch Derayah's truth
    try:
        api_orders_raw = get_orders_api()
    except Exception as e:
        print(f"[{_now()}] reconcile_orders: API call failed: {e}")
        return {"error": str(e)}

    # 3. Build API order map
    api_order_map = {}
    for o in api_orders_raw:
        oid = str(o.get("orderId", ""))
        if not oid:
            continue
        # Prefer orderStatusId (per bot.py:755), fall back to status (per bookkeeper.py)
        derayah_code = o.get("orderStatusId") or o.get("status") or 0
        api_order_map[oid] = {
            "raw": o,
            "derayah_code": derayah_code,
            "our_status": map_derayah_status(derayah_code),
            "symbol": str(o.get("symbol", "")).replace(".SR", ""),
            "side": "BUY" if o.get("side") == 1 else "SELL",
            "qty": o.get("quantity", 0),
            "price": o.get("price", 0),
            "type": "MARKET" if o.get("price", 0) == 0 else "LIMIT",
            "order_date": o.get("orderDate") or o.get("orderDateTime"),
        }

    # 4. Build new orders.json: API truth + adjustments for missing local orders
    new_orders = {}
    transitions = {"initiated_to_rejected": [], "placed_to_expired": [],
                   "status_changes": [], "new_from_api": []}

    # 4a. Process API orders (source of truth)
    for oid, api_data in api_order_map.items():
        existing = local_orders.get(oid, {})
        new_orders[oid] = {
            "initiated_at": existing.get("initiated_at") or api_data["order_date"] or _now(),
            "initiated_by": existing.get("initiated_by") or "derayah-direct",
            "trigger_basis": existing.get("trigger_basis") or "unknown",
            "trigger_detail": existing.get("trigger_detail") or "",
            "symbol": api_data["symbol"],
            "side": api_data["side"],
            "qty": api_data["qty"],
            "price": api_data["price"],
            "type": api_data["type"],
            "status": api_data["our_status"],
            "updated_at": _now(),
        }
        # Detect status change vs local
        old_status = existing.get("status")
        new_status = api_data["our_status"]
        if old_status is not None and old_status != new_status:
            transitions["status_changes"].append({
                "order_id": oid, "old": old_status, "new": new_status,
                "symbol": api_data["symbol"], "side": api_data["side"],
                "qty": api_data["qty"], "price": api_data["price"],
            })
        elif old_status is None:
            # New from API (not previously seen locally)
            transitions["new_from_api"].append({
                "order_id": oid, "status": new_status,
                "symbol": api_data["symbol"], "side": api_data["side"],
                "qty": api_data["qty"], "price": api_data["price"],
            })

    # 4b. Process local orders NOT in API (handle INITIATED/PLACED absence)
    for oid, local_o in local_orders.items():
        if oid in api_order_map:
            continue  # already processed
        if local_o.get("status") in TERMINAL_STATUSES:
            # Already terminal locally, but not in API — keep as-is (terminal row)
            new_orders[oid] = local_o
        elif local_o.get("status") == STATUS_INITIATED:
            # INITIATED but Derayah doesn't see it by order_id
            # Check if there's a matching FILLED order in API (fuzzy match)
            # Match by: symbol, side, qty, price, AND time window (±5 min)
            matched_api_order = None
            local_time = local_o.get("initiated_at", "")
            local_symbol = local_o.get("symbol", "")
            local_side = local_o.get("side", "")
            local_qty = local_o.get("qty", 0)
            local_price = local_o.get("price", 0)
            
            for api_oid, api_data in api_order_map.items():
                if api_data["our_status"] == STATUS_FILLED:
                    # Check symbol, side, qty, price match
                    if (api_data["symbol"] == local_symbol and 
                        api_data["side"] == local_side and 
                        api_data["qty"] == local_qty and
                        api_data["price"] == local_price):
                        # Check time window (±5 minutes)
                        api_time = api_data.get("order_date", "")
                        if api_time and local_time:
                            try:
                                from datetime import datetime
                                # Parse times (handle ISO format)
                                local_dt = datetime.fromisoformat(local_time.replace('Z', '+00:00'))
                                api_dt = datetime.fromisoformat(api_time.replace('Z', '+00:00'))
                                time_diff = abs((api_dt - local_dt).total_seconds())
                                if time_diff <= 300:  # 5 minutes = 300 seconds
                                    matched_api_order = api_oid
                                    break
                            except Exception:
                                # If time parsing fails, match anyway (fallback)
                                matched_api_order = api_oid
                                break
            
            if matched_api_order:
                # Found matching FILLED order — update with real order ID and FILLED status
                new_orders[matched_api_order] = {
                    "initiated_at": local_o.get("initiated_at") or api_order_map[matched_api_order]["order_date"] or _now(),
                    "initiated_by": local_o.get("initiated_by") or "derayah-direct",
                    "trigger_basis": local_o.get("trigger_basis") or "unknown",
                    "trigger_detail": local_o.get("trigger_detail") or "",
                    "symbol": api_order_map[matched_api_order]["symbol"],
                    "side": api_order_map[matched_api_order]["side"],
                    "qty": api_order_map[matched_api_order]["qty"],
                    "price": api_order_map[matched_api_order]["price"],
                    "type": api_order_map[matched_api_order]["type"],
                    "status": STATUS_FILLED,
                    "updated_at": _now(),
                    "matched_from_api": True,
                    "original_order_id": oid,
                }
                transitions["status_changes"].append({
                    "order_id": matched_api_order, "old": STATUS_INITIATED, "new": STATUS_FILLED,
                    "symbol": local_o.get("symbol"), "side": local_o.get("side"),
                    "qty": local_o.get("qty"), "price": local_o.get("price"),
                })
            else:
                # No matching FILLED order found → mark REJECTED
                new_orders[oid] = {**local_o, "status": STATUS_REJECTED, "updated_at": _now()}
                transitions["initiated_to_rejected"].append({
                    "order_id": oid, "symbol": local_o.get("symbol"),
                    "side": local_o.get("side"), "qty": local_o.get("qty"),
                    "price": local_o.get("price"),
                })
        elif local_o.get("status") in (STATUS_PLACED, STATUS_PARTIAL):
            # PLACED/PARTIAL but not in API anymore → mark EXPIRED
            new_orders[oid] = {**local_o, "status": STATUS_EXPIRED, "updated_at": _now()}
            transitions["placed_to_expired"].append({
                "order_id": oid, "symbol": local_o.get("symbol"),
                "side": local_o.get("side"), "qty": local_o.get("qty"),
                "price": local_o.get("price"),
            })

    # 5. Save (overwrite pattern, same as positions.json)
    save_orders(new_orders)

    # 6. Announce transitions (only meaningful ones)
    _announce_transitions(transitions)

    # 7. Record terminal orders to local history (Phase 5)
    if HISTORY_IO_AVAILABLE:
        try:
            today = _today()
            for oid, o in new_orders.items():
                if o.get("status") in TERMINAL_STATUSES:
                    # Only record if this is a NEW terminal (not already in history)
                    # Heuristic: check if last row in order_history.csv matches
                    append_order_history({
                        "date": today,
                        "order_id": oid,
                        "symbol": o.get("symbol", ""),
                        "side": o.get("side", ""),
                        "qty": o.get("qty", 0),
                        "price": o.get("price", 0),
                        "type": o.get("type", ""),
                        "status": _status_name(o.get("status", 0)),
                        "initiated_by": o.get("initiated_by", "derayah-direct"),
                        "trigger_basis": o.get("trigger_basis", "unknown"),
                        "trigger_detail": o.get("trigger_detail", ""),
                    })
        except Exception as e:
            print(f"[{_now()}] record_to_history failed: {e}")

    return {
        "local_count": len(local_orders),
        "api_count": len(api_order_map),
        "new_count": len(new_orders),
        "transitions": {k: len(v) for k, v in transitions.items()},
    }





def record_daily_pnl(date_str: str = None, notes: str = "") -> dict:
    """
    Record today's P&L snapshot to daily_pnl.csv.
    Called at hard close (15:30) by the tasi-stand-down-cleanup cron.

    Calculates pnl from actual trade history (realized P&L) and capital snapshot.
    """
    if not HISTORY_IO_AVAILABLE:
        return {"error": "history_io not available"}

    from datetime import datetime, timezone, timedelta
    if date_str is None:
        date_str = datetime.now(RIYADH_TZ).strftime("%Y-%m-%d")

    # Read capital.json
    try:
        with open(CAPITAL_FILE) as f:
            cap = json.load(f)
    except Exception as e:
        return {"error": f"capital.json read failed: {e}"}

    equity = cap.get("equity", 0) or cap.get("invested", 0) or 0
    booked = cap.get("booked", 0) or 0
    cash = cap.get("cash_3bucket", 0) or cap.get("available_capital", 0) or 0
    total = cap.get("total_3bucket", 0) or cap.get("grand_total", 0) or 0

    # Calculate REALIZED P&L from order history
    pnl_data = get_daily_pnl(date_str)
    realized_pnl = pnl_data.get("realized_pnl", 0)
    gross_pnl = pnl_data.get("gross_pnl", 0)
    fees = pnl_data.get("fees", 0)
    trades = len(pnl_data.get("trades", []))

    # Account value change (for reference)
    from history_io import read_daily_pnl
    prev_rows = read_daily_pnl(last_n=5)
    account_change = 0.0
    previous_total = 0.0
    if prev_rows:
        for r in reversed(prev_rows):
            if r.get("date") != date_str:
                try:
                    previous_total = float(r.get("total", 0))
                    account_change = round(total - previous_total, 2)
                except (ValueError, TypeError):
                    account_change = 0.0
                break

    # Auto-detect deposits/withdrawals (>= 100 SAR threshold)
    deposits = 0.0
    withdrawals = 0.0
    if abs(account_change) >= 100:
        # Significant capital movement detected
        if account_change > 0:
            deposits = account_change
            notes = f"Deposit detected: +{deposits:.2f} SAR. {notes}"
        else:
            withdrawals = abs(account_change)
            notes = f"Withdrawal detected: -{withdrawals:.2f} SAR. {notes}"
    else:
        # Small change = trading variance, use calculated PnL
        pass

    append_daily_pnl(
        date=date_str,
        equity=equity,
        booked=booked,
        cash=cash,
        total=total,
        pnl=realized_pnl,
        trades=trades,
        deposits=deposits,
        withdrawals=withdrawals,
        notes=notes,
    )

    return {
        "date": date_str,
        "equity": equity,
        "booked": booked,
        "cash": cash,
        "total": total,
        "pnl": realized_pnl,
        "gross_pnl": gross_pnl,
        "fees": fees,
        "account_change": account_change,
        "trades": trades,
        "notes": notes,
    }


def prune_orders_json_terminal():
    """
    Phase 5: At hard close, remove all terminal orders from orders.json
    (they're already in order_history.csv).
    Per A A: orders.json should only hold outstanding orders.
    """
    if not ORDER_HELPERS_AVAILABLE:
        return {"error": "order_helpers not available"}

    local_orders = load_orders()
    kept = {oid: o for oid, o in local_orders.items()
            if o.get("status") not in TERMINAL_STATUSES}
    save_orders(kept)
    return {
        "before": len(local_orders),
        "after": len(kept),
        "pruned": len(local_orders) - len(kept),
    }


def _announce_transitions(transitions: dict):
    """Send Telegram announcements for order status changes. Best-effort."""
    # INITIATED → REJECTED (Derayah never accepted)
    for t in transitions["initiated_to_rejected"]:
        msg = (f"❌ <b>Order {t['order_id']} REJECTED</b>\n"
               f"{t['side']} {t['qty']}×{t['symbol']} @ {t['price']}\n"
               f"<i>Not seen in Derayah after 1 cycle (5 min)</i>")
        _tg_send(msg)

    # PLACED → EXPIRED (disappeared from API)
    for t in transitions["placed_to_expired"]:
        msg = (f"⚠️ <b>Order {t['order_id']} EXPIRED</b>\n"
               f"{t['side']} {t['qty']}×{t['symbol']} @ {t['price']}\n"
               f"<i>No longer in Derayah Order/List (likely EOD cleanup)</i>")
        _tg_send(msg)

    # Status changes (FILLED, CANCELLED, etc.) — but only interesting ones
    for t in transitions["status_changes"]:
        old_name = _status_name(t["old"])
        new_name = _status_name(t["new"])
        if new_name == "FILLED":
            msg = (f"🎯 <b>Order {t['order_id']} FILLED</b>\n"
                   f"{t['side']} {t['qty']}×{t['symbol']} @ {t['price']}\n"
                   f"<i>{old_name} → FILLED</i>")
            _tg_send(msg)
        elif new_name == "CANCELLED":
            msg = (f"🚫 <b>Order {t['order_id']} CANCELLED</b>\n"
                   f"{t['side']} {t['qty']}×{t['symbol']} @ {t['price']}\n"
                   f"<i>{old_name} → CANCELLED</i>")
            _tg_send(msg)
        elif new_name == "REJECTED":
            msg = (f"❌ <b>Order {t['order_id']} REJECTED</b>\n"
                   f"{t['side']} {t['qty']}×{t['symbol']} @ {t['price']}\n"
                   f"<i>Derayah rejected</i>")
            _tg_send(msg)
        elif new_name == "PARTIAL":
            # Partial fill — bookkeeper adds new child row, parent stays
            msg = (f"🔄 <b>Order {t['order_id']} PARTIAL</b>\n"
                   f"{t['side']} {t['qty']}×{t['symbol']} @ {t['price']}\n"
                   f"<i>Partially filled, may add more rows</i>")
            _tg_send(msg)
        # PLACED transitions are not announced (too noisy during reconciliation)

    # New orders from API (only for visibility on manual trades)
    # Skip — too noisy. User can check /Orders if curious.

def record_trade(symbol: str, side: str, qty: int, price: float, order_id: str = "", fees: float = 0) -> dict:
    """Record a trade. Called by bot after execution."""
    book = load_trade_book()
    
    trade = {
        "timestamp": _now(),
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "order_id": order_id,
        "fees": fees,
        "source": "bot",
    }
    
    book["trades"].append(trade)
    save_trade_book(book)
    
    print(f"[{_now()}] Trade recorded: {side} {qty}×{symbol} @ {price} (fees: {fees})")
    return trade

def rebuild_trade_book_from_derayah(date_str: str = None) -> dict:
    """
    Rebuild trade_book.json from Derayah API orders.
    This overwrites local trade book with ground truth.
    """
    if date_str is None:
        date_str = _today()
    
    orders = get_orders_with_fees(date_str)  # Use fee-enriched version
    
    # Separate buys and sells
    trades = []
    for o in orders:
        sym = o.get("symbol", "")
        side = "BUY" if o.get("side") == 1 else "SELL"
        qty = o.get("quantity", 0)
        price = o.get("price", 0)
        status = o.get("status", 0)
        
        # Get actual timestamp from orderDateTime, fallback to orderDate
        ts = o.get("orderDateTime") or o.get("orderDate") or _now()
        
        if status == 12:  # FILLED
            trade = {
                "timestamp": ts,
                "symbol": sym,
                "side": side,
                "qty": qty,
                "price": price,
                "order_id": o.get("orderId", ""),
                "fees": o.get("feesCollected", 0),
                "source": "derayah-api",
            }
            trades.append(trade)
    
    # Save
    book = {
        "version": "2.0",
        "rebuilt_at": _now(),
        "source": "derayah-api",
        "trades": trades,
    }
    save_trade_book(book)
    
    print(f"[{_now()}] Trade book rebuilt from Derayah: {len(trades)} trades")
    return book

# ─── Order Details ──────────────────────────────────────────────────────────

def get_order_details(order_id: int, tokens: dict = None) -> dict:
    """Get detailed order info including actual fees."""
    resp = api_call("POST", "Order/Details", {
        "portfolio": PORTFOLIO,
        "orderId": order_id,
    }, tokens)
    return resp.get("data", {}).get("result", {})

def get_orders_with_fees(date_str: str = None, tokens: dict = None) -> list:
    """Get orders with actual fees from Derayah."""
    orders = get_orders_api(date_str, tokens)
    
    # Enrich with fee data from Order/Details
    for o in orders:
        if o.get("status") == 12 and o.get("orderId"):
            details = get_order_details(o["orderId"], tokens)
            o["feesCollected"] = details.get("feesCollected", 0)
            o["orderDateTime"] = details.get("orderDateTime") or o.get("orderDate")
    
    return orders

# ─── P&L & Fee Calculator ───────────────────────────────────────────────────

def calculate_fees(trade_value: float) -> dict:
    """Calculate Derayah fees for a trade."""
    commission = trade_value * 0.0005  # 0.05%
    vat = commission * 0.15  # 15%
    total = commission + vat
    return {
        "commission": round(commission, 2),
        "vat": round(vat, 2),
        "total": round(total, 2),
    }

def get_daily_pnl(date_str: str = None) -> dict:
    """Calculate P&L for a specific date using FIFO matching from order_history.csv."""
    if date_str is None:
        date_str = _today()
    
    # Read from order_history.csv (reliable source)
    from history_io import read_order_history
    orders = read_order_history(last_n_orders=1000, days=1)
    
    # Normalize date format: date_str is YYYY-MM-DD, CSV uses MM-DD
    search_date = date_str
    if len(date_str) == 10 and date_str.count("-") == 2:
        search_date = date_str[5:]  # Extract MM-DD
    
    # Deduplicate by order_id (bookkeeper may append duplicates)
    # Also filter out test orders
    seen_ids = set()
    day_orders = []
    for o in orders:
        if o.get("date") == search_date and o.get("status") == "FILLED":
            oid = o.get("order_id", "")
            # Skip test orders
            if oid and oid.startswith("TEST"):
                continue
            if oid and oid not in seen_ids:
                seen_ids.add(oid)
                day_orders.append(o)
            elif not oid:
                day_orders.append(o)  # Include orders without ID
    
    # Separate buys and sells by symbol
    buys = defaultdict(list)
    sells = defaultdict(list)
    
    for o in day_orders:
        sym = o.get("symbol", "")
        side = o.get("side", "")
        qty = int(o.get("qty", 0))
        price = float(o.get("price", 0))
        
        if side == "BUY":
            buys[sym].append({"qty": qty, "price": price})
        elif side == "SELL":
            sells[sym].append({"qty": qty, "price": price})
    
    pnl_data = {
        "date": date_str,
        "realized_pnl": 0,
        "fees": 0,
        "gross_pnl": 0,
        "trades": [],
    }
    
    for sym in set(buys.keys()) | set(sells.keys()):
        buy_queue = list(buys[sym])
        sell_queue = list(sells[sym])
        
        for sell in sell_queue:
            sell_qty = sell["qty"]
            sell_price = sell["price"]
            
            while sell_qty > 0 and buy_queue:
                buy = buy_queue[0]
                buy_qty = buy["qty"]
                buy_price = buy["price"]
                
                matched = min(sell_qty, buy_qty)
                gross = matched * (sell_price - buy_price)
                
                # Estimate fees (0.0575% commission + VAT)
                trade_value = matched * sell_price
                commission = trade_value * 0.0005
                vat = commission * 0.15
                fees = commission + vat
                
                net = gross - fees
                
                pnl_data["realized_pnl"] += net
                pnl_data["gross_pnl"] += gross
                pnl_data["fees"] += fees
                
                pnl_data["trades"].append({
                    "symbol": sym,
                    "qty": matched,
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "gross": round(gross, 2),
                    "fees": round(fees, 2),
                    "net": round(net, 2),
                })
                
                sell_qty -= matched
                buy["qty"] -= matched
                if buy["qty"] <= 0:
                    buy_queue.pop(0)
    
    pnl_data["realized_pnl"] = round(pnl_data["realized_pnl"], 2)
    pnl_data["gross_pnl"] = round(pnl_data["gross_pnl"], 2)
    pnl_data["fees"] = round(pnl_data["fees"], 2)
    
    return pnl_data

# ─── Reconciliation ─────────────────────────────────────────────────────────

def reconcile_with_derayah(date_str: str = None) -> dict:
    """Compare bot's trade book with Derayah API orders."""
    if date_str is None:
        date_str = _today()
    
    book = load_trade_book()
    bot_trades = [t for t in book["trades"] if date_str in t.get("timestamp", "")]
    api_orders = get_orders_api(date_str)
    
    discrepancies = {
        "date": date_str,
        "bot_count": len(bot_trades),
        "api_count": len(api_orders),
        "missing_in_bot": [],
        "missing_in_api": [],
        "qty_mismatch": [],
        "status": "OK",
    }
    
    # Build maps
    bot_map = defaultdict(list)
    for t in bot_trades:
        bot_map[t["symbol"]].append(t)
    
    api_map = defaultdict(list)
    for o in api_orders:
        sym = o.get("symbol", "")
        api_map[sym].append(o)
    
    # Check for orders in API but not in bot
    for sym, orders in api_map.items():
        bot_for_sym = bot_map.get(sym, [])
        api_qty = sum(o.get("quantity", 0) for o in orders if o.get("status") == 12)
        bot_qty = sum(t.get("qty", 0) for t in bot_for_sym)
        
        if not bot_for_sym:
            discrepancies["missing_in_bot"].append({
                "symbol": sym,
                "api_orders": len(orders),
                "issue": "Order exists in Derayah but not in bot book",
            })
            discrepancies["status"] = "MISMATCH"
        elif abs(api_qty - bot_qty) > 0:
            discrepancies["qty_mismatch"].append({
                "symbol": sym,
                "api_qty": api_qty,
                "bot_qty": bot_qty,
                "diff": api_qty - bot_qty,
            })
            discrepancies["status"] = "MISMATCH"
    
    # Check for orders in bot but not in API
    for sym, trades in bot_map.items():
        if sym not in api_map:
            discrepancies["missing_in_api"].append({
                "symbol": sym,
                "bot_trades": len(trades),
                "issue": "Trade in bot book but not in Derayah API",
            })
            discrepancies["status"] = "MISMATCH"
    
    return discrepancies

# ─── Reports ────────────────────────────────────────────────────────────────

def generate_daily_report(date_str: str = None) -> str:
    """Generate human-readable daily report using order_history.csv (not trade_book.json)."""
    if date_str is None:
        date_str = _today()
    
    # Use order_history.csv for accurate PnL
    pnl_data = get_daily_pnl(date_str)
    trades = pnl_data.get("trades", [])
    
    # Count from order_history.csv using normalized date
    from history_io import read_order_history
    all_orders = read_order_history(last_n_orders=1000, days=1)
    # Normalize date: date_str might be YYYY-MM-DD, CSV uses MM-DD
    search_date = date_str
    if len(date_str) == 10 and date_str.count("-") == 2:
        search_date = date_str[5:]  # Extract MM-DD
    day_orders = [o for o in all_orders if o.get("date") == search_date]
    filled_count = len([o for o in day_orders if o.get("status") == "FILLED"])
    
    report = f"""
# TASI Daily Report — {date_str}

## P&L Summary
| Metric | Value |
|--------|-------|
| Gross P&L | {pnl_data['gross_pnl']:.2f} SAR |
| Fees (cost) | {pnl_data['fees']:.2f} SAR |
| Net P&L | {pnl_data['realized_pnl']:.2f} SAR |
| Round-trips | {len(trades)} |
| Total Orders | {filled_count} |

## Trade Details
| Symbol | Qty | Buy | Sell | Gross | Fees | Net |
|--------|-----|-----|------|-------|------|-----|
"""
    for t in trades:
        net = t.get('net', 0)
        emoji = "🟢" if net >= 0 else "🔴"
        report += f"| {emoji} {t['symbol']} | {t['qty']} | {t['buy_price']:.2f} | {t['sell_price']:.2f} | {t['gross']:.2f} | {t['fees']:.2f} | {net:.2f} |\n"
    
    return report

def generate_capital_report(days: int = 7) -> str:
    """Generate capital history report."""
    from datetime import timedelta
    
    report = f"# Capital History — Last {days} Days\n\n"
    report += "| Date | Grand Total | Available | Invested |\n"
    report += "|------|-------------|-----------|----------|\n"
    
    for i in range(days):
        d = (datetime.now(RIYADH_TZ) - timedelta(days=i)).strftime("%Y-%m-%d")
        hist_file = f"{HISTORY_DIR}/capital_{d}.jsonl"
        
        if os.path.exists(hist_file):
            with open(hist_file) as f:
                lines = f.readlines()
            if lines:
                last = json.loads(lines[-1])
                report += f"| {d} | {last.get('grand_total', 'N/A')} | {last.get('available', 'N/A')} | {last.get('invested', 'N/A')} |\n"
        else:
            report += f"| {d} | — | — | — |\n"
    
    return report

# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    """Run full sync (dashboard scrape + position sync)."""
    print(f"\n{'='*60}")
    print(f"TASI Bookkeeper Full Sync — {_now()}")
    print(f"{'='*60}")
    
    capital = sync_capital()
    positions = sync_positions()
    
    print(f"\n{'='*60}")
    print("SYNC COMPLETE")
    print(f"{'='*60}")
    print(f"Capital: {capital['grand_total']} total, {capital['available_capital']} available")
    print(f"Positions: {len([p for p in positions.values() if not p.get('closed')])} open")
    
    return capital, positions

if __name__ == "__main__":
    main()

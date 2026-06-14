#!/usr/bin/env python3
"""
History I/O — Local CSV files for Daily P&L and Order History (Phase 5).

Per A A 2026-06-11 03:12: No cloud, no Google Sheets.
- Local CSV files in /home/mino/tasi-exec/history/
- Daily P&L: rolling CSV, one row per trading day
- Order History: rolling CSV, one row per order ever placed
- User can read these directly via SSH/SCP, or ask Mino to email them

Both CSVs are append-only and grow forever (no rotation).
File pattern allows easy tail/grep/jq from the command line.
"""

import csv
import fcntl
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = "/home/mino/tasi-exec"
HISTORY_DIR = f"{BASE_DIR}/history"

DAILY_PNL_FILE = f"{HISTORY_DIR}/daily_pnl.csv"
ORDER_HISTORY_FILE = f"{HISTORY_DIR}/order_history.csv"

RIYADH_TZ = timezone(timedelta(hours=3))

# ─── Daily P&L schema ─────────────────────────────────────────────────────
DAILY_PNL_HEADERS = [
    "date",           # YYYY-MM-DD (trading day)
    "equity",         # SAR — positions at market
    "booked",         # SAR — outstanding orders
    "cash",           # SAR — available
    "total",          # SAR — grand_total
    "pnl",            # SAR — realized P&L for the day (vs previous close)
    "trades",         # int — number of fills
    "notes",          # free text (e.g., "weekend gap", "EOD cancel")
    "recorded_at",    # ISO timestamp
]

# ─── Order History schema ─────────────────────────────────────────────────
ORDER_HISTORY_HEADERS = [
    "date",           # MM-DD (trading day, no year)
    "time",           # HH:MM (human-readable)
    "order_id",       # Derayah orderId as string
    "symbol",         # e.g., "2200"
    "side",           # "BUY" or "SELL"
    "qty",            # int
    "price",          # float (0 for MARKET)
    "total",          # qty × price (total value)
    "fees",           # commission + VAT (0.0575%)
    "type",           # "MARKET" or "LIMIT"
    "status",         # "FILLED", "CANCELLED", "REJECTED", "EXPIRED"
    "initiated_by",   # "auto_buy" | "auto_sell" | "manual-buy" | etc.
    "trigger_basis",  # WHY was this order placed
    "trigger_detail", # Human-readable detail
    "pnl",            # Realized P&L (populated when sell pairs with buy)
]


def _now_iso() -> str:
    return datetime.now(RIYADH_TZ).strftime("%H:%M")


def _today_str() -> str:
    return datetime.now(RIYADH_TZ).strftime("%m-%d")


def _ensure_files():
    """Create CSV files with headers if they don't exist."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    for path, headers in [
        (DAILY_PNL_FILE, DAILY_PNL_HEADERS),
        (ORDER_HISTORY_FILE, ORDER_HISTORY_HEADERS),
    ]:
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(headers)


def _locked_read_csv(path: str):
    """Read CSV with advisory read lock."""
    rows = []
    fieldnames = None
    try:
        with open(path, "r+", newline="") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                rows = list(reader)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    return fieldnames, rows


def _locked_write_csv(path: str, fieldnames: list, rows: list):
    """Write CSV with advisory write lock. Filters rows to only include known fields."""
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            # Filter each row to only include known fields
            filtered_rows = []
            for row in rows:
                filtered = {k: v for k, v in row.items() if k in fieldnames}
                filtered_rows.append(filtered)
            writer.writerows(filtered_rows)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    os.replace(tmp, path)



def append_daily_pnl(date: str, equity: float, booked: float, cash: float,
                     total: float, pnl: float, trades: int, notes: str = "") -> bool:
    """
    Append a daily P&L row. If a row for this date already exists, it is
    overwritten in-place (we only keep the latest snapshot per day).
    Returns True on success.
    """
    _ensure_files()
    fieldnames, rows = _locked_read_csv(DAILY_PNL_FILE)
    if not fieldnames:
        fieldnames = DAILY_PNL_HEADERS

    found = False
    for row in rows:
        if row.get("date") == date:
            row.update({
                "equity": round(equity, 2),
                "booked": round(booked, 2),
                "cash": round(cash, 2),
                "total": round(total, 2),
                "pnl": round(pnl, 2),
                "trades": trades,
                "notes": notes,
                "recorded_at": _now_iso(),
            })
            found = True

    if not found:
        rows.append({
            "date": date,
            "equity": round(equity, 2),
            "booked": round(booked, 2),
            "cash": round(cash, 2),
            "total": round(total, 2),
            "pnl": round(pnl, 2),
            "trades": trades,
            "notes": notes,
            "recorded_at": _now_iso(),
        })

    _locked_write_csv(DAILY_PNL_FILE, DAILY_PNL_HEADERS, rows)
    return True


def append_order_history(order: dict) -> bool:
    """
    Append an order row. Deduplicates by order_id+date+side+status.
    Calculates total value and fees automatically.
    """
    _ensure_files()
    fieldnames, rows = _locked_read_csv(ORDER_HISTORY_FILE)
    if not fieldnames:
        fieldnames = ORDER_HISTORY_HEADERS

    # Deduplicate: skip if exact same order_id+date+side+status+symbol+qty+price+time already exists
    order_id = str(order.get("order_id", ""))
    date = order.get("date") or _today_str()
    side = str(order.get("side", ""))
    status = str(order.get("status", ""))
    symbol = str(order.get("symbol", ""))
    qty = str(order.get("qty", ""))
    price = str(order.get("price", ""))
    time = str(order.get("time", ""))
    
    for existing in rows:
        if (existing.get("order_id") == order_id and
            existing.get("date") == date and
            existing.get("side") == side and
            existing.get("status") == status and
            existing.get("symbol") == symbol and
            existing.get("qty") == qty and
            existing.get("price") == price and
            existing.get("time") == time):
            return False  # Duplicate, skip

    qty = int(order.get("qty", 0))
    price = float(order.get("price", 0))
    total = round(qty * price, 2)
    
    # Fees: 0.05% commission + 15% VAT on commission = 0.0575%
    fees = round(total * 0.000575, 2)
    
    now = datetime.now(RIYADH_TZ)
    # Use passed date if available, otherwise current date
    date_str = order.get("date") or now.strftime("%m-%d")
    # If date is in YYYY-MM-DD format, convert to MM-DD
    if date_str and len(date_str) == 10 and date_str.count("-") == 2:
        date_str = date_str[5:]  # Extract MM-DD from YYYY-MM-DD
    
    # Use passed time if available, otherwise current time
    time_str = order.get("time") or now.strftime("%H:%M")
    
    row = {
        "date": date_str,
        "time": time_str,             # HH:MM
        "order_id": order_id,
        "symbol": str(order.get("symbol", "")),
        "side": side,
        "qty": qty,
        "price": price,
        "total": total,
        "fees": fees,
        "type": str(order.get("type", "")),
        "status": status,
        "initiated_by": str(order.get("initiated_by", "")),
        "trigger_basis": str(order.get("trigger_basis", "")),
        "trigger_detail": str(order.get("trigger_detail", "")),
        "pnl": "",  # Populated later when sell pairs with buy
    }
    rows.append(row)
    _locked_write_csv(ORDER_HISTORY_FILE, ORDER_HISTORY_HEADERS, rows)
    return True


def read_daily_pnl(last_n: int = 2) -> list:
    """Return the last N rows from daily_pnl.csv (most recent last)."""
    _ensure_files()
    fieldnames, rows = _locked_read_csv(DAILY_PNL_FILE)
    return rows[-last_n:] if last_n > 0 else rows


def read_order_history(last_n_orders: int = 50, days: int = 2) -> list:
    """
    Return orders from the last N trading days, capped at last_n_orders.
    Most recent first.
    """
    _ensure_files()
    fieldnames, rows = _locked_read_csv(ORDER_HISTORY_FILE)

    # Get unique dates (most recent first), keep last N trading days
    dates = sorted({r.get("date", "") for r in rows if r.get("date")}, reverse=True)
    keep_dates = set(dates[:days])

    filtered = [r for r in rows if r.get("date") in keep_dates]
    # Most recent first
    filtered.sort(key=lambda r: (r.get("date", ""), r.get("time", "")), reverse=True)
    return filtered[:last_n_orders]


def calculate_order_pnl(symbol: str = None, days: int = 2) -> list:
    """
    Calculate realized PnL by pairing BUY and SELL orders (FIFO).
    Returns list of completed round-trips with PnL.
    """
    _ensure_files()
    fieldnames, rows = _locked_read_csv(ORDER_HISTORY_FILE)
    
    # Filter by days
    dates = sorted({r.get("date", "") for r in rows if r.get("date")}, reverse=True)
    keep_dates = set(dates[:days])
    filtered = [r for r in rows if r.get("date") in keep_dates]
    
    # Filter by symbol if specified
    if symbol:
        filtered = [r for r in filtered if r.get("symbol") == symbol]
    
    # Separate buys and sells (only FILLED orders)
    buys = [r for r in filtered if r.get("side") == "BUY" and r.get("status") == "FILLED" and r.get("order_id") and r.get("order_id") != "?"]
    sells = [r for r in filtered if r.get("side") == "SELL" and r.get("status") == "FILLED" and r.get("order_id") and r.get("order_id") != "?"]
    
    # Sort by order_id numeric value (since IDs increment)
    def sort_key(r):
        oid = r.get("order_id", "")
        try:
            return (0, int(oid))
        except:
            return (1, oid)
    
    buys.sort(key=sort_key)
    sells.sort(key=sort_key)
    
    results = []
    buy_queue = list(buys)
    
    for sell in sells:
        sell_qty = int(sell.get("qty", 0) or 0)
        sell_price = float(sell.get("price", 0) or 0)
        sell_fees = float(sell.get("fees", 0) or 0)
        sell_symbol = sell.get("symbol", "")
        
        # Find matching buy in queue (don't pop non-matching, just skip)
        matched_buy = None
        buy_idx = 0
        while buy_idx < len(buy_queue):
            buy = buy_queue[buy_idx]
            if buy.get("symbol") == sell_symbol:
                matched_buy = buy
                break
            buy_idx += 1
        
        if not matched_buy:
            continue  # No matching buy found
        
        buy_qty = int(matched_buy.get("qty", 0) or 0)
        buy_price = float(matched_buy.get("price", 0) or 0)
        buy_fees = float(matched_buy.get("fees", 0) or 0)
        
        matched = min(sell_qty, buy_qty)
        
        # Calculate PnL
        gross = matched * (sell_price - buy_price)
        total_fees = (buy_fees / max(int(matched_buy.get("qty", 1) or 1), 1) * matched) + \
                    (sell_fees / max(int(sell.get("qty", 1) or 1), 1) * matched)
        net_pnl = gross - total_fees
        
        results.append({
            "symbol": sell_symbol,
            "qty": matched,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "gross": round(gross, 2),
            "fees": round(total_fees, 2),
            "pnl": round(net_pnl, 2),
            "date": sell.get("date", ""),
            "time": sell.get("time", ""),
        })
        
        # Reduce buy qty or remove if fully consumed
        sell_qty -= matched
        matched_buy["qty"] = str(buy_qty - matched)
        if int(matched_buy["qty"] or 0) <= 0:
            buy_queue.pop(buy_idx)
    
    return results


# ─── Self-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Use temp files for self-test — never touch production CSVs
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp(prefix="history_io_test_")
    orig_daily_pnl = DAILY_PNL_FILE
    orig_order_hist = ORDER_HISTORY_FILE
    globals()["DAILY_PNL_FILE"] = f"{tmpdir}/daily_pnl.csv"
    globals()["ORDER_HISTORY_FILE"] = f"{tmpdir}/order_history.csv"
    globals()["HISTORY_DIR"] = tmpdir

    try:
        print("=" * 60)
        print("Phase 5: history_io self-test")
        print("=" * 60)

        # Test 1: Append daily P&L
        print("\n[1] append_daily_pnl for today + yesterday")
        today = _today_str()
        yesterday = (datetime.now(RIYADH_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        append_daily_pnl(yesterday, equity=1000, booked=0, cash=2000, total=3000, pnl=50, trades=2, notes="yesterday")
        append_daily_pnl(today, equity=1100, booked=100, cash=1900, total=3100, pnl=80, trades=3, notes="today")
        rows = read_daily_pnl(2)
        print(f"  Read back: {len(rows)} rows")
        for r in rows:
            print(f"    {r['date']}: equity={r['equity']} cash={r['cash']} total={r['total']} pnl={r['pnl']} trades={r['trades']}")
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
        assert rows[-1]["date"] == today
        print("  ✅ daily_pnl.csv: 2 rows appended and read back")

        # Test 2: Update existing day's row (no duplicate)
        print("\n[2] Update today's row (should NOT create duplicate)")
        append_daily_pnl(today, equity=1200, booked=200, cash=1800, total=3200, pnl=100, trades=4, notes="updated")
        rows = read_daily_pnl(2)
        print(f"  Read back: {len(rows)} rows (should still be 2)")
        for r in rows:
            print(f"    {r['date']}: equity={r['equity']} pnl={r['pnl']} notes='{r['notes']}'")
        assert len(rows) == 2, f"Expected 2 rows (no duplicate), got {len(rows)}"
        assert rows[-1]["pnl"] == "100.0" or rows[-1]["pnl"] == "100"
        print("  ✅ Update-in-place works (no duplicate)")

        # Test 3: Append order history
        print("\n[3] append_order_history for 3 orders")
        append_order_history({
            "date": today, "order_id": "100", "symbol": "1010", "side": "BUY",
            "qty": 10, "price": 20.50, "type": "LIMIT", "status": "FILLED",
            "initiated_by": "manual-buy",
        })
        append_order_history({
            "date": today, "order_id": "101", "symbol": "2200", "side": "BUY",
            "qty": 25, "price": 7.66, "type": "MARKET", "status": "FILLED",
            "initiated_by": "auto_buy",
        })
        append_order_history({
            "date": yesterday, "order_id": "099", "symbol": "4050", "side": "BUY",
            "qty": 5, "price": 51.00, "type": "MARKET", "status": "REJECTED",
            "initiated_by": "auto_buy",
        })
        orders = read_order_history(last_n_orders=10, days=2)
        print(f"  Read back: {len(orders)} orders (last 2 days)")
        for o in orders:
            print(f"    {o['date']} #{o['order_id']}: {o['side']} {o['qty']}×{o['symbol']} @ {o['price']} [{o['status']}] by {o['initiated_by']}")
        assert len(orders) == 3
        assert orders[0]["date"] == today  # most recent first
        print("  ✅ order_history.csv: 3 orders, sorted by date desc")

        # Test 4: Days filter
        print("\n[4] read_order_history with days=1 (should only return today's)")
        orders = read_order_history(last_n_orders=10, days=1)
        print(f"  Read back: {len(orders)} orders (last 1 day)")
        for o in orders:
            print(f"    {o['date']} #{o['order_id']}")
        assert all(o["date"] == today for o in orders)
        assert len(orders) == 2
        print("  ✅ Days filter works (1 day → 2 orders, 0 from yesterday)")

        print("\n" + "=" * 60)
        print("ALL HISTORY_IO TESTS PASSED ✅")
        print("=" * 60)
    finally:
        globals()["DAILY_PNL_FILE"] = orig_daily_pnl
        globals()["ORDER_HISTORY_FILE"] = orig_order_hist
        globals()["HISTORY_DIR"] = "/home/mino/tasi-exec/history"
        shutil.rmtree(tmpdir, ignore_errors=True)

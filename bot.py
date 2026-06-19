#!/usr/bin/env python3
"""
TASI Execution Bot
Listens to the TASI Execution Telegram group and places orders on Derayah.
"""

import asyncio
import base64
import logging
import re
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
import pytz
import requests  # Added for Derayah API calls
import yfinance as yf
from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
from playwright.async_api import async_playwright
import derayah_api
import tasi_telegram_handler as tasi_handler
import os

# ─── Constants ──────────────────────────────────────────────────────────────

# [Session Management code moved below BASE_DIR definition]

BASE_DIR       = "/home/mino/tasi-exec"  # Base directory for TASI exec

# ─── Session Management ────────────────────────────────────────────────────
# Phase 1/2/3: TASI Session Management System (v4.2)
sys.path.insert(0, BASE_DIR)
try:
    import bot_commands
    from bot_commands import SessionCommands, validate_session
    SESSION_ENABLED = True
except ImportError as e:
    logging.warning(f"Session commands not available: {e}")
    SESSION_ENABLED = False
    SessionCommands = None
    validate_session = None

# ─── Order lifecycle helpers (v4.4) ──────────────────────────────────
try:
    from order_helpers import (
        load_orders, save_orders, write_order_initiated, effective_holdings,
        trigger_bookkeeper_sync, get_outstanding_orders, get_booked_capital,
        get_status_name, STATUS_INITIATED, STATUS_PLACED, STATUS_PARTIAL,
        STATUS_FILLED, STATUS_CANCELLED, STATUS_REJECTED, STATUS_EXPIRED,
        TERMINAL_STATUSES, ORDERS_FILE, STATUS_NAMES,
    )
    ORDER_HELPERS_AVAILABLE = True
except ImportError as e:
    logging.warning(f"order_helpers not available: {e}")
    ORDER_HELPERS_AVAILABLE = False

# ─── Config ──────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")  # MUST be set via systemd Environment= or .env file
GROUP_CHAT_ID = -5235925419
OWNER_ID      = 5529987063
MINO_BOT_ID   = 8612182758  # Mino's bot — also allowed to send commands

DERAYAH_URL   = "https://newonline.derayah.com/#/layout/dashboard"
CDP_URL       = "http://127.0.0.1:18801"
PORTFOLIO_ID  = "2063853"
CHROMIUM_CMD  = [
    "/usr/bin/google-chrome-stable",
    "--remote-debugging-port=18801",
    "--user-data-dir=/home/mino/.config/google-chrome/derayah-live",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
        "--no-first-run", "--disable-sync", "--no-default-browser-check",
    "--proxy-server=socks5://localhost:1080",
]

LOG_FILE       = "/home/mino/tasi-exec/exec.log"
POSITIONS_FILE = "/home/mino/tasi-exec/positions.json"
CAPITAL_FILE   = "/home/mino/tasi-exec/capital.json"
RIYADH         = pytz.timezone("Asia/Riyadh")

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

# ─── State ───────────────────────────────────────────────────────────────────

browser_context = None
page = None

def _load_positions() -> dict:
    if not os.path.exists(POSITIONS_FILE):
        return {}
    with open(POSITIONS_FILE) as f:
        return json.load(f)

def _save_positions(pos: dict):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(pos, f, indent=2)

DERAYAH_LIVE    = True    # Derayah real-time subscription active
SELECTOR_FILE   = "/home/mino/tasi-exec/selector_map.json"
TC_URL          = "tickerchart"  # Derayah Trade tab (TickerChart — matches old and new URLs)
TC_FALLBACK_URLS = ["derayah.tickerchart.net", "newonline.derayah.com/Home/RealPrices"]

# SlickGrid scroll map — built once per process lifetime on first cache miss
_TC_SCROLL_MAP: dict = {}   # {base_symbol: scroll_top_px}
_TC_SCROLL_MAP_BUILT = False

_FALLBACK_SELECTORS = [
    "[class*='last-price']", "[class*='lastPrice']",
    "[class*='current-price']", "[class*='currentPrice']",
    "[data-field='lastPrice']", "[data-field='last']",
    ".price-value", ".ag-cell-value",
]

def _load_confirmed_selector() -> str | None:
    try:
        if os.path.exists(SELECTOR_FILE):
            with open(SELECTOR_FILE) as f:
                sel = json.load(f).get("selector")
            if sel:
                return sel
    except Exception:
        pass
    return None

_TC_VP_SELECTOR = (
    ".slick-viewport-top.slick-viewport-left, "
    ".slick-viewport-top, .slick-viewport"
)

_TC_READ_JS = """(base) => {
    const rows = document.querySelectorAll('.slick-row');
    for (const row of rows) {
        const sym = row.querySelector('.slick-cell.symbol');
        if (sym && sym.textContent.trim() === base) {
            const last = row.querySelector('.slick-cell.last');
            return last ? last.textContent.trim() : null;
        }
    }
    return null;
}"""

async def _tc_build_scroll_map(pg) -> dict:
    """Scan full SlickGrid to build {symbol: scroll_top_px}. Runs once per session."""
    scroll_map = {}
    for sp in range(0, 7500, 200):
        await pg.evaluate(f"""() => {{
            const vps = document.querySelectorAll('{_TC_VP_SELECTOR}');
            for (const vp of vps) if (vp.scrollHeight > 500) vp.scrollTop = {sp};
        }}""")
        await pg.wait_for_timeout(80)
        syms = await pg.evaluate("""() =>
            [...document.querySelectorAll('.slick-row')].map(r => {
                const s = r.querySelector('.slick-cell.symbol');
                return s ? s.textContent.trim() : null;
            }).filter(Boolean)
        """)
        for sym in syms:
            if sym and sym not in scroll_map:
                scroll_map[sym] = sp
    await pg.evaluate(f"""() => {{
        const vps = document.querySelectorAll('{_TC_VP_SELECTOR}');
        for (const vp of vps) if (vp.scrollHeight > 500) vp.scrollTop = 0;
    }}""")
    return scroll_map


async def _tc_price(symbol: str) -> float | None:
    """
    Read live price from the TickerChart SlickGrid DOM.
    Fast path: visible rows (no interaction).
    Fallback: scroll to symbol's row via cached scroll map, read, restore.
    """
    global _TC_SCROLL_MAP, _TC_SCROLL_MAP_BUILT
    try:
        pw  = await async_playwright().start()
        br  = await pw.chromium.connect_over_cdp(CDP_URL, timeout=3000)
        ctx = br.contexts[0]
        pg  = next((p for p in ctx.pages if TC_URL in p.url), None)
        if not pg:
            return None

        base = symbol.replace(".SR", "")

        # Fast path — already visible
        price_str = await pg.evaluate(_TC_READ_JS, base)
        if price_str:
            val = float(price_str.replace(",", ""))
            if val > 0:
                log.info(f"TC price {base}: {val} (visible)")
                return val

        # Build scroll map once on first cache miss
        if not _TC_SCROLL_MAP_BUILT:
            _TC_SCROLL_MAP = await _tc_build_scroll_map(pg)
            _TC_SCROLL_MAP_BUILT = True
            log.info(f"TC scroll map built: {len(_TC_SCROLL_MAP)} symbols")

        sp = _TC_SCROLL_MAP.get(base)
        if sp is None:
            return None

        # Save current scroll position
        orig = await pg.evaluate(f"""() => {{
            const vps = document.querySelectorAll('{_TC_VP_SELECTOR}');
            for (const vp of vps) if (vp.scrollHeight > 500) return vp.scrollTop;
            return 0;
        }}""")

        # Scroll to symbol's row, read, restore
        await pg.evaluate(f"""() => {{
            const vps = document.querySelectorAll('{_TC_VP_SELECTOR}');
            for (const vp of vps) if (vp.scrollHeight > 500) vp.scrollTop = {sp};
        }}""")
        await pg.wait_for_timeout(100)

        price_str = await pg.evaluate(_TC_READ_JS, base)

        await pg.evaluate(f"""() => {{
            const vps = document.querySelectorAll('{_TC_VP_SELECTOR}');
            for (const vp of vps) if (vp.scrollHeight > 500) vp.scrollTop = {orig};
        }}""")

        if price_str:
            val = float(price_str.replace(",", ""))
            if val > 0:
                log.info(f"TC price {base}: {val} (scroll={sp})")
                return val
        return None
    except Exception as e:
        log.debug(f"_tc_price {symbol}: {e}")
        return None

async def _derayah_price_async(symbol: str) -> float | None:
    """
    Scrape live price from the open Derayah browser tab.
    Uses confirmed selector from selector_map.json if available,
    falls back to built-in list otherwise.
    """
    try:
        pw  = await async_playwright().start()
        br  = await pw.chromium.connect_over_cdp(CDP_URL, timeout=3000)
        ctx = br.contexts[0]
        pg  = next((p for p in ctx.pages if "derayah.com" in p.url), None)
        if not pg:
            return None

        base = symbol.replace(".SR", "")
        target = f"https://newonline.derayah.com/#/layout/market-watch?symbol={base}"
        try:
            await pg.goto(target, wait_until="domcontentloaded", timeout=6000)
            await pg.wait_for_timeout(2000)
        except Exception:
            pass

        confirmed = _load_confirmed_selector()
        selectors = ([confirmed] if confirmed else []) + _FALLBACK_SELECTORS

        for sel in selectors:
            try:
                for el in await pg.query_selector_all(sel):
                    text = (await el.inner_text()).strip().replace(",", "")
                    try:
                        val = float(text)
                        if 5.0 < val < 5000.0:
                            log.info(f"Derayah live {symbol}: {val} (selector: {sel})")
                            return val
                    except ValueError:
                        pass
            except Exception:
                pass

        log.warning(f"Derayah scraper: no price for {symbol} — run map_selectors.py 1010 at 10:00")
        return None
    except Exception as e:
        log.warning(f"derayah_price {symbol}: {e}")
        return None

def fetch_last_price(symbol: str) -> float | None:
    """
    Best-effort current price. Priority:
    1. Derayah Trade tab (TickerChart — live, no navigation)
    2. Derayah online tab (live scrape, navigates to market-watch)
    3. yfinance (~15-min delayed)
    """
    # 1. TickerChart — fastest, real-time
    try:
        price = asyncio.run(_tc_price(symbol))
        if price:
            return price
    except Exception as e:
        log.debug(f"TC price failed: {e}")

    # 2. Derayah live scrape
    if DERAYAH_LIVE:
        try:
            price = asyncio.run(_derayah_price_async(symbol))
            if price:
                log.info(f"fill price {symbol}: {price:.2f} (Derayah live)")
                return price
        except Exception as e:
            log.warning(f"Derayah fill lookup failed: {e}")

    # 3. WebSocket file — same as poller uses
    try:
        base = symbol.replace(".SR", "")
        ws_file = f"{BASE_DIR}/ws_prices_{datetime.now(RIYADH).strftime('%Y-%m-%d')}.jsonl"
        if os.path.exists(ws_file):
            with open(ws_file, 'r') as f:
                lines = f.readlines()
                for line in reversed(lines):
                    try:
                        d = json.loads(line.strip())
                        if d.get("symbol") == base:
                            price = float(d["price"])
                            log.info(f"fill price {symbol}: {price:.2f} (WebSocket live)")
                            return price
                    except:
                        continue
    except Exception as e:
        log.debug(f"WebSocket price lookup failed: {e}")

    # 4. yfinance
    try:
        ticker_sym = symbol if "." in symbol else f"{symbol}.SR"
        df = yf.Ticker(ticker_sym).history(period="1d", interval="1m")
        if df.empty:
            df = yf.Ticker(ticker_sym).history(period="5d", interval="1m")
        if not df.empty:
            price = float(df["Close"].iloc[-1])
            log.info(f"fill price {symbol}: {price:.2f} (yfinance ~15-min delayed)")
            return price
    except Exception as e:
        log.warning(f"fetch_last_price yfinance {symbol}: {e}")
    return None

def record_buy(symbol: str, qty: int, price: float):
    """Record a buy: add to existing position (net qty tracking) + update capital with fees."""
    source = "limit"
    if price == 0.0:
        # MARKET order — use yfinance last price as fill proxy (~15-min delayed)
        fetched = fetch_last_price(symbol)
        if fetched:
            log.info(f"MARKET fill proxy for {symbol}: {fetched:.2f} (yfinance last)")
            price = fetched
            source = "market-proxy"
        else:
            log.warning(f"Could not fetch fill price for {symbol} — entry_price will be 0")
            source = "unknown"

    # Calculate fees (0.05% commission + 15% VAT on commission)
    trade_value = qty * price if price else 0
    commission = trade_value * 0.0005  # 0.05%
    vat = commission * 0.15  # 15% of commission
    total_cost = trade_value + commission + vat

    pos = _load_positions()
    existing = pos.get(symbol)

    if existing and not existing.get("closed", False):
        # Add to existing position — weighted average
        old_qty = existing.get("qty", 0)
        old_cost = existing.get("cost", old_qty * existing.get("entry_price", 0))
        new_qty = old_qty + qty
        new_cost = old_cost + (qty * price)
        avg_entry = new_cost / new_qty if new_qty > 0 else price

        pos[symbol] = {
            "symbol":       symbol,
            "entry_price":  round(avg_entry, 4),
            "qty":          new_qty,
            "cost":         round(new_cost, 2),
            "entry_time":   existing.get("entry_time", datetime.now(RIYADH).isoformat()),
            "peak_price":   max(existing.get("peak_price", price), price),
            "closed":       False,
            "close_price":  None,
            "close_time":   None,
            "price_source": source,
            "realized_pnl": existing.get("realized_pnl", 0),
            "commission":   round(existing.get("commission", 0) + commission, 2),
            "vat":          round(existing.get("vat", 0) + vat, 2),
            "total_cost":   round(existing.get("total_cost", old_cost) + total_cost, 2),
        }
        log.info(f"Position updated: {symbol} +{qty} → total={new_qty} avg={avg_entry:.2f} cost={new_cost:.2f} (fees: {commission:.2f}+{vat:.2f})")
    else:
        # New position
        pos[symbol] = {
            "symbol":       symbol,
            "entry_price":  price,
            "qty":          qty,
            "cost":         round(qty * price, 2),
            "entry_time":   datetime.now(RIYADH).isoformat(),
            "peak_price":   price,
            "closed":       False,
            "close_price":  None,
            "close_time":   None,
            "price_source": source,
            "realized_pnl": 0,
            "commission":   round(commission, 2),
            "vat":          round(vat, 2),
            "total_cost":   round(total_cost, 2),
        }
        log.info(f"Position opened: {symbol} qty={qty} entry={price:.2f} cost={qty*price:.2f} (fees: {commission:.2f}+{vat:.2f})")

    _save_positions(pos)

    # Update capital.json
    try:
        with open(CAPITAL_FILE) as f:
            cap = json.load(f)
        cap["available_capital"] = max(0, cap.get("available_capital", 0) - total_cost)
        cap["total_fees"] = cap.get("total_fees", 0) + commission + vat
        cap["updated_at"] = datetime.now(RIYADH).isoformat()
        cap["source"] = "bot-manual-buy"
        with open(CAPITAL_FILE, "w") as f:
            json.dump(cap, f, indent=2)
        log.info(f"Capital updated: -{total_cost:.2f} SAR (trade: {trade_value:.2f} + commission: {commission:.2f} + VAT: {vat:.2f}), remaining={cap['available_capital']:.2f}")
    except Exception as cap_err:
        log.error(f"Failed to update capital after buy: {cap_err}")

    # Trigger bookkeeper sync for truth
    _trigger_bookkeeper_sync()

def _trigger_bookkeeper_sync():
    """Trigger bookkeeper quick_refresh in background to sync with Derayah truth."""
    try:
        import subprocess
        subprocess.Popen(
            ["/usr/bin/python3", "-c", "import sys; sys.path.insert(0, '/home/mino/tasi-exec'); import bookkeeper; bookkeeper.quick_refresh()"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        log.info("Bookkeeper sync triggered (background)")
    except Exception as e:
        log.warning(f"Failed to trigger bookkeeper sync: {e}")

def record_sell(symbol: str, qty: int, price: float):
    """Record a sell: reduce position qty, track realized P&L, update capital with fees."""
    pos = _load_positions()
    existing = pos.get(symbol)

    if not existing or existing.get("closed", False):
        log.warning(f"Sell recorded but no open position for {symbol}")
        return

    old_qty = existing.get("qty", 0)
    entry = existing.get("entry_price", 0)
    realized = existing.get("realized_pnl", 0)

    # Calculate fees on sell
    trade_value = qty * price if price else 0
    commission = trade_value * 0.0005  # 0.05%
    vat = commission * 0.15  # 15% of commission
    total_returned = trade_value - commission - vat

    if qty >= old_qty:
        # Full close (or oversell — clamp to full close)
        pnl = (price - entry) * old_qty
        realized += pnl
        pos[symbol] = {
            "symbol":       symbol,
            "entry_price":  entry,
            "qty":          0,
            "cost":         0,
            "entry_time":   existing.get("entry_time"),
            "peak_price":   existing.get("peak_price", entry),
            "closed":       True,
            "close_price":  price,
            "close_time":   datetime.now(RIYADH).isoformat(),
            "price_source": existing.get("price_source", "unknown"),
            "realized_pnl": round(realized, 2),
        }
        log.info(f"Position closed: {symbol} sold {old_qty} @ {price:.2f} pnl={pnl:.2f} (fees: {commission:.2f}+{vat:.2f})")
    else:
        # Partial close
        pnl = (price - entry) * qty
        realized += pnl
        new_qty = old_qty - qty
        new_cost = new_qty * entry

        pos[symbol] = {
            "symbol":       symbol,
            "entry_price":  entry,
            "qty":          new_qty,
            "cost":         round(new_cost, 2),
            "entry_time":   existing.get("entry_time"),
            "peak_price":   existing.get("peak_price", entry),
            "closed":       False,
            "close_price":  None,
            "close_time":   None,
            "price_source": existing.get("price_source", "unknown"),
            "realized_pnl": round(realized, 2),
        }
        log.info(f"Position reduced: {symbol} -{qty} → remaining={new_qty} realized_pnl={realized:.2f} (fees: {commission:.2f}+{vat:.2f})")

    _save_positions(pos)

    # Update capital.json
    try:
        with open(CAPITAL_FILE) as f:
            cap = json.load(f)
        cap["available_capital"] = cap.get("available_capital", 0) + total_returned
        cap["total_fees"] = cap.get("total_fees", 0) + commission + vat
        cap["updated_at"] = datetime.now(RIYADH).isoformat()
        cap["source"] = "bot-manual-sell"
        with open(CAPITAL_FILE, "w") as f:
            json.dump(cap, f, indent=2)
        log.info(f"Capital updated after sell: +{total_returned:.2f} SAR (trade: {trade_value:.2f} - commission: {commission:.2f} - VAT: {vat:.2f}), available={cap['available_capital']:.2f}")
    except Exception as cap_err:
        log.error(f"Failed to update capital after sell: {cap_err}")

    # Trigger bookkeeper sync for truth
    _trigger_bookkeeper_sync()

async def close_all_positions() -> str:
    """Market sell all open positions."""
    pos = _load_positions()
    open_pos = {k: v for k, v in pos.items() if not v.get("closed") and v.get("qty", 0) > 0}

    if not open_pos:
        return "📭 No open positions to close."

    lines = ["🚨 CLOSE ALL — Market selling positions:"]
    sold, failed = 0, 0

    for sym, data in open_pos.items():
        qty = data.get("qty", 0)
        if qty <= 0:
            continue

        result = await place_order(sym, "SELL", qty, "MARKET")
        if result.startswith("✅"):
            # Extract price from result if possible
            price = 0.0
            try:
                # Parse "✅ SELL 6 × 4008 @ Market — orderId=..."
                # or "✅ SELL 6 × 4008 @ 24.50 — orderId=..."
                if "@" in result:
                    price_str = result.split("@")[1].split("—")[0].strip()
                    if price_str != "Market":
                        price = float(price_str.replace("SAR", "").strip())
            except:
                pass

            record_sell(sym, qty, price)
            lines.append(f"  ✅ {sym}: sold {qty} shares @ {price or 'Market'}")
            sold += 1
        else:
            lines.append(f"  ❌ {sym}: {result}")
            failed += 1

    lines.append(f"\n📊 Result: {sold} sold, {failed} failed")
    return "\n".join(lines)

# ─── Security ────────────────────────────────────────────────────────────────

def is_authorized(update: Update) -> bool:
    msg = update.message
    if msg.from_user and msg.from_user.id == OWNER_ID:
        return True
    if msg.chat_id != GROUP_CHAT_ID:
        return False
    # Allow Mino bot relayed commands (via_bot or forward)
    if msg.via_bot and msg.via_bot.id == MINO_BOT_ID:
        return True
    return False

# ─── Command Parsing ─────────────────────────────────────────────────────────

def parse_command(text: str):
    """
    Accepted formats:
      BUY 1010 100 @ 45.50       → limit buy
      BUY 1010 100 MARKET        → market buy
      SELL 1010 100 @ 45.50      → limit sell
      SELL 1010 100 MARKET       → market sell
      STATUS                     → show positions
      CANCEL                     → cancel all pending orders
      CLOSE ALL                  → market sell all positions
    Returns dict or None.
    """
    text = text.strip().upper()

    if text in ("STATUS",):
        return {"action": "STATUS"}

    if text in ("CANCEL", "CANCEL ALL"):
        return {"action": "CANCEL"}

    if text in ("CLOSE ALL", "CLOSE"):
        return {"action": "CLOSE_ALL"}

    # HELP
    if text in ("HELP", "?", "COMMANDS"):
        return {"action": "HELP"}

    # STAND DOWN
    if text in ("STAND DOWN", "STOP BUYING", "NO BUY"):
        return {"action": "STAND_DOWN"}

    # PRICE symbol
    m = re.match(r'^PRICE\s+(\w+)$', text)
    if m:
        return {"action": "PRICE", "symbol": m.group(1)}

    # P/L or PNL
    if text in ("P/L", "PNL", "PROFIT", "LOSS"):
        return {"action": "PNL"}

    # HISTORY
    if text in ("HISTORY", "ORDERS", "TRADES"):
        return {"action": "HISTORY"}

    # PORTFOLIO
    if text in ("PORTFOLIO", "ACCOUNT", "BALANCE"):
        return {"action": "PORTFOLIO"}

    # VISUALIZE — Generate image via ComfyUI
    m = re.match(r'^VISUALIZE\s+(.+)$', text, re.IGNORECASE)
    if m:
        return {"action": "VISUALIZE", "prompt": m.group(1)}

    # DRY RUN mode toggle
    if text in ("DRY RUN", "SIMULATE", "TEST"):
        return {"action": "DRY_RUN"}

    # REPORT commands
    if text in ("REPORT", "WEEKLY REPORT", "WEEKLY"):
        return {"action": "REPORT"}

    if text in ("DAILY REPORT", "DAILY"):
        return {"action": "DAILY_REPORT"}

    # BUY/SELL with limit price
    m = re.match(r'^(BUY|SELL)\s+(\w+)\s+(\d+)\s+@\s*([\d.]+)$', text)
    if m:
        return {
            "action": m.group(1),
            "symbol": m.group(2),
            "qty":    int(m.group(3)),
            "price":  float(m.group(4)),
            "type":   "LIMIT",
        }

    # BUY/SELL at market
    m = re.match(r'^(BUY|SELL)\s+(\w+)\s+(\d+)\s+MARKET$', text)
    if m:
        return {
            "action": m.group(1),
            "symbol": m.group(2),
            "qty":    int(m.group(3)),
            "type":   "MARKET",
        }

    return None

# ─── Derayah Browser Automation ──────────────────────────────────────────────

async def ensure_page():
    """Attach to running Chromium via CDP, auto-starting it if needed."""
    global browser_context, page
    if page is not None:
        try:
            await page.title()
            return page
        except Exception:
            page = None

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=3000)
    except Exception:
        log.info("CDP unreachable — attempting Chrome auto-restart")
        # Clear stale lock files (Chrome IPC requires this after a hard kill)
        profile_dir = pathlib.Path("/home/mino/.config/google-chrome/derayah-live")
        for lock in ("SingletonLock", "DevToolsActivePort"):
            p = profile_dir / lock
            try:
                if p.is_symlink() or p.exists():
                    p.unlink()
                    log.info(f"Removed stale {lock}")
            except Exception as e:
                log.warning(f"Could not remove {lock}: {e}")
        # Use the blueprint startup script (more robust than CHROMIUM_CMD)
        try:
            subprocess.Popen(
                ["/bin/bash", "/home/mino/tasi-exec/start-chrome.sh"],
                env=env if 'env' in dir() else {**os.environ, "DISPLAY": ":0"},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            log.error(f"Failed to spawn start-chrome.sh: {e}")
            # Fall back to original CHROMIUM_CMD if script unavailable
            env = {**os.environ, "DISPLAY": ":0"}
            subprocess.Popen(CHROMIUM_CMD, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(6)
        # Verify CDP is actually up; retry once if not
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=8000)
        except Exception:
            log.warning("CDP still unreachable after 6s — waiting 4s more")
            await asyncio.sleep(4)
            browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=8000)

    contexts = browser.contexts
    ctx = contexts[0] if contexts else await browser.new_context()
    browser_context = ctx

    pages = ctx.pages
    derayah_page = None
    for p in pages:
        if "derayah.com" in p.url:
            derayah_page = p
            break

    if derayah_page is None:
        derayah_page = await ctx.new_page()
        await derayah_page.goto(DERAYAH_URL, wait_until="networkidle")

    page = derayah_page
    return page

async def place_order(symbol: str, action: str, qty: int, price_type: str, price: float = None) -> str:
    """Place an order via Derayah REST API. Returns a result message."""
    try:
        if DRY_RUN_MODE:
            price_str = "Market" if price_type == "MARKET" else f"{price} SAR"
            return f"🧪 [DRY RUN] {action} {qty} × {symbol} @ {price_str} — NOT EXECUTED"
        
        # ─── Phase 4.4: Double-sell pre-check (manual trades too) ─────
        if action == "SELL" and ORDER_HELPERS_AVAILABLE:
            eff = effective_holdings(symbol)
            if qty > eff:
                msg = (f"❌ BLOCKED: cannot sell {qty}×{symbol} — effective holdings only "
                       f"{eff} (filled - outstanding_sell + outstanding_buy)")
                log.warning(msg)
                return msg

        side       = derayah_api.SIDE_BUY  if action == "BUY"    else derayah_api.SIDE_SELL
        order_type = derayah_api.TYPE_MARKET if price_type == "MARKET" else derayah_api.TYPE_LIMIT
        resp = await derayah_api.place_order(symbol, side, qty, order_type, price or 0.0)
        if resp.get("isSuccess"):
            order_id  = (resp.get("data") or {}).get("orderId", "?")
            # ─── Phase 4.4: Write INITIATED to orders.json ─────────────
            if ORDER_HELPERS_AVAILABLE and order_id != "?":
                initiated_by = "manual-buy" if action == "BUY" else "manual-sell"
                write_order_initiated(
                    order_id=order_id,
                    action=action,
                    symbol=symbol,
                    qty=qty,
                    price=price or 0.0,
                    order_type=price_type,
                    initiated_by=initiated_by,
                    trigger_basis=order_helpers.TRIGGER_MANUAL_COMMAND,
                    trigger_detail=f"Manual /{action} command from Telegram",
                )
            price_str = "Market" if price_type == "MARKET" else f"{price} SAR"
            return f"✅ {action} {qty} × {symbol} @ {price_str} — orderId={order_id}"
        else:
            err = resp.get("message") or resp.get("errorMessage") or str(resp)
            return f"❌ Order rejected: {err}"
    except Exception as e:
        log.error(f"place_order error: {e}")
        return f"❌ Order failed: {e}"

async def cancel_all_orders() -> str:
    """Cancel all open/pending orders via Derayah REST API."""
    try:
        orders = await derayah_api.get_orders()
        # Pending statuses: 1=pending, 2=partial — anything not terminal
        TERMINAL = {3, 4, 5, 6, 7, 8}   # filled, cancelled, rejected, expired, etc.
        pending  = [o for o in orders if o.get("orderStatusId") not in TERMINAL]
        if not pending:
            return "✅ No outstanding orders to cancel."

        cancelled, failed = 0, 0
        for o in pending:
            oid = o.get("orderId")
            try:
                resp = await derayah_api.cancel_order(oid)
                if resp.get("isSuccess"):
                    cancelled += 1
                else:
                    failed += 1
                    log.warning(f"cancel_all: orderId={oid} → {resp.get('message','')}")
            except Exception as e:
                failed += 1
                log.warning(f"cancel_all: orderId={oid} exception: {e}")

        parts = [f"✅ Cancelled {cancelled} order(s)."]
        if failed:
            parts.append(f"⚠️ {failed} failed.")
        return " ".join(parts)
    except Exception as e:
        log.error(f"cancel_all_orders: {e}")
        return f"❌ Cancel failed: {e}"


async def get_actual_balance_from_derayah():
    """Fetch actual available balance from Derayah by scraping dashboard with refresh."""
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
                return None
            
            # REFRESH the page first to get updated data
            try:
                await page.reload(wait_until='domcontentloaded', timeout=15000)
            except Exception as e:
                log.warning(f"Balance scrape: reload warning (continuing): {e}")
            await page.wait_for_timeout(3000)
            
            # Navigate to portfolio
            try:
                await page.goto('https://newonline.derayah.com/#/layout/trading-portfolio', wait_until='domcontentloaded', timeout=15000)
            except Exception as e:
                log.warning(f"Balance scrape: navigation warning (continuing): {e}")
            await page.wait_for_timeout(8000)  # Wait longer for SPA to render
            
            # Scrape the values using JavaScript (reliable for SPAs)
            try:
                js_result = await page.evaluate("""() => {
                    const allElements = Array.from(document.querySelectorAll('*'));
                    const result = {
                        grand_total: null,
                        money_transfer: null,
                        securities_value: null,
                        total_cash: null
                    };
                    
                    function findValueForLabel(labelText) {
                        for (const el of allElements) {
                            if (el.textContent.trim() === labelText) {
                                const parent = el.parentElement;
                                if (parent) {
                                    const text = parent.textContent;
                                    const match = text.match(/([0-9,.]+[.]?[0-9]*)[ \t\n\u000D]*SAR/);
                                    if (match) {
                                        return parseFloat(match[1].replace(',', ''));
                                    }
                                }
                            }
                        }
                        return null;
                    }
                    
                    result.grand_total = findValueForLabel('Grand Total');
                    result.money_transfer = findValueForLabel('Money Transfer');
                    result.securities_value = findValueForLabel('Securities Value');
                    result.total_cash = findValueForLabel('Total Cash');
                    
                    return result;
                }""")
                
                result = js_result
                
            except Exception as js_err:
                log.warning(f"Balance scrape: JS extraction failed: {js_err}, falling back to text parsing")
                
                # Fallback: use text parsing
                text = await page.inner_text('body')
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                
                result = {
                    'grand_total': None,
                    'money_transfer': None,
                    'securities_value': None,
                    'total_cash': None
                }
                
                skip_items = {'Beneficiaries', 'Transfer History', 'Fund Order History', 
                              'Cash Statement', 'Tadawal', 'New Option Request',
                              'Financial Derivatives', 'Main', 'Total Fund', 'Total Sukuk',
                              'Cash Accounts', '001LOC-SAR TDWL'}
                
                for i, line in enumerate(lines):
                    line_stripped = line.strip()
                    if line_stripped in skip_items:
                        continue
                    
                    if 'Grand Total' in line_stripped:
                        for offset in [1, 2, 3]:
                            if i+offset < len(lines):
                                check = lines[i+offset].strip()
                                if check in skip_items: continue
                                match = re.search(r'([\d,]+\.?\d*)\s*SAR', check)
                                if match:
                                    result['grand_total'] = float(match.group(1).replace(',', ''))
                                    break
                    
                    if 'Money Transfer' in line_stripped and 'Beneficiaries' not in line_stripped:
                        for offset in [1, 2, 3]:
                            if i+offset < len(lines):
                                check = lines[i+offset].strip()
                                if check in skip_items: continue
                                match = re.search(r'([\d,]+\.?\d*)\s*SAR', check)
                                if match:
                                    result['money_transfer'] = float(match.group(1).replace(',', ''))
                                    break
                    
                    if 'Securities Value' in line_stripped:
                        for offset in [1, 2, 3]:
                            if i+offset < len(lines):
                                check = lines[i+offset].strip()
                                if check in skip_items: continue
                                match = re.search(r'([\d,]+\.?\d*)\s*SAR', check)
                                if match:
                                    result['securities_value'] = float(match.group(1).replace(',', ''))
                                    break
                    
                    if 'Total Cash' in line_stripped:
                        for offset in [1, 2, 3]:
                            if i+offset < len(lines):
                                check = lines[i+offset].strip()
                                if check in skip_items: continue
                                match = re.search(r'([\d,]+\.?\d*)\s*SAR', check)
                                if match:
                                    result['total_cash'] = float(match.group(1).replace(',', ''))
                                    break
            
            await browser.close()
            
            # Log what we found
            log.info(f"Scraped Derayah: grand_total={result.get('grand_total')}, money_transfer={result.get('money_transfer')}, securities={result.get('securities_value')}, cash={result.get('total_cash')}")
            
            if result.get('money_transfer') is not None:
                return {
                    'total': result.get('grand_total') or 1000.66,
                    'available': result.get('money_transfer'),
                    'invested': result.get('securities_value') or 0,
                    'cash': result.get('total_cash') or 0
                }
            
            return None
    except Exception as e:
        log.error(f"get_actual_balance scrape failed: {e}")
        return None


async def get_status() -> str:
    try:
        # Check Derayah login via API token
        login_status = "❌"
        try:
            import derayah_api
            token = await derayah_api.get_token()
            if token and len(token.split('.')) == 3:
                import base64
                parts = token.split('.')
                pad = parts[1] + '=' * (-len(parts[1]) % 4)
                exp = json.loads(base64.urlsafe_b64decode(pad.encode())).get('exp', 0)
                if exp > time.time():
                    login_status = "✅"
        except:
            pass

        # Check TickerChart/CDP status
        tc_status = "❌"
        try:
            tabs = requests.get('http://127.0.0.1:18801/json', timeout=5).json()
            tc = next((t for t in tabs if 'tickerchart' in t.get('url', '').lower()), None)
            if tc:
                tc_status = "✅"
        except:
            pass

        # Check WebSocket status (live prices)
        ws_status = "❌"
        try:
            import glob
            ws_files = glob.glob(f"{BASE_DIR}/ws_prices_*.jsonl")
            if ws_files:
                ws_file = sorted(ws_files)[-1]
                # Check if file modified in last 2 minutes
                mtime = os.path.getmtime(ws_file)
                if time_mod.time() - mtime < 120:
                    ws_status = "✅ Live"
                else:
                    ws_status = "⚠️ Stale"
        except:
            pass

        # Load local positions
        pos = _load_positions()
        open_pos = {k: v for k, v in pos.items() if not v.get("closed")}

        # ─── Phase 1: Trigger bookkeeper sync for fresh data ──────────────
        sync_msg = ""
        try:
            if ORDER_HELPERS_AVAILABLE:
                from bookkeeper import quick_refresh
                quick_refresh()
                sync_msg = "(synced)"
        except Exception as e:
            log.warning(f"/Status: bookkeeper sync failed: {e}")
            sync_msg = "(sync failed)"

        # ─── Phase 2: Read from files (bookkeeper source of truth) ─────────
        actual = None
        try:
            with open(CAPITAL_FILE) as f:
                cap = json.load(f)
            actual = {
                'total': cap.get('grand_total', 0) or 0,
                'available': cap.get('available_capital', 0) or 0,
                'invested': cap.get('invested', 0) or 0,
                'cash': cap.get('available_capital', 0) or 0,
            }
        except Exception as e:
            log.warning(f"/Status: could not read capital.json: {e}")
            # Fallback to scrape only if files missing
            actual = await get_actual_balance_from_derayah()

        # Build clean status message - using plain text, no HTML tags for cleaner display
        lines = []
        lines.append("📊 TASI TRADING STATUS")
        lines.append("")
        lines.append("🔐 System Status")
        lines.append(f"  Derayah Login: {login_status}")
        lines.append(f"  TickerChart:   {tc_status}")
        lines.append("")
        
        # Positions section
        if open_pos:
            lines.append(f"📈 Open Positions ({len(open_pos)})")
            for sym, data in open_pos.items():
                # Support both entry_price and avg_price (poller uses both)
                entry = data.get("entry_price") or data.get("avg_price", 0)
                qty = data.get("qty", 0)
                cost = data.get("cost", entry * qty)
                lines.append(f"  • {sym}: {qty} shares @ {entry:.2f} SAR")
                lines.append(f"    Cost: {cost:.2f} SAR")
        else:
            lines.append("📈 Open Positions: None")
        
        lines.append("")
        
        # ─── Phase 4.4: Outstanding orders section ──────────────────────
        if ORDER_HELPERS_AVAILABLE:
            try:
                outstanding = get_outstanding_orders()
                if outstanding:
                    booked = get_booked_capital()
                    lines.append(f"📋 Outstanding Orders ({len(outstanding)}, {booked:,.2f} SAR booked)")
                    for oid, o in list(outstanding.items())[:10]:  # cap at 10 for readability
                        side = o.get("side", "?")
                        sym = o.get("symbol", "?")
                        qty = o.get("qty", 0)
                        price = o.get("price", 0)
                        otype = o.get("type", "?")
                        status_code = o.get("status", 0)
                        status_name = get_status_name(status_code)
                        price_str = "Market" if otype == "MARKET" else f"{price:.2f}"
                        lines.append(f"  • {oid}: {side} {qty}×{sym} @ {price_str} [{status_name}]")
                    if len(outstanding) > 10:
                        lines.append(f"  ... and {len(outstanding) - 10} more")
                else:
                    lines.append("📋 Outstanding Orders: None")
            except Exception as e:
                log.warning(f"Failed to load outstanding orders for /status: {e}")
                lines.append("📋 Outstanding Orders: (error reading)")
        else:
            lines.append("📋 Outstanding Orders: (order_helpers unavailable)")
        
        lines.append("")
        
        # ─── Phase 4: 3-bucket capital display (from files) ───────────────
        if ORDER_HELPERS_AVAILABLE and actual:
            try:
                equity = actual.get('invested', 0) or 0
                booked = get_booked_capital()
                grand_total = actual.get('total', 0) or 0
                cash = actual.get('available', 0) or 0
                total_3bucket = equity + booked + cash
                
                lines.append(f"💰 Capital")
                lines.append(f"  Equity:  {equity:>10,.2f} SAR")
                lines.append(f"  Booked:  {booked:>10,.2f} SAR")
                lines.append(f"  Cash:    {cash:>10,.2f} SAR")
                lines.append(f"  ────────")
                lines.append(f"  Total:   {total_3bucket:>10,.2f} SAR")
                
            except Exception as e:
                log.warning(f"3-bucket capital failed: {e}")
                lines.append("💰 Capital: error")
            lines.append("")
        elif not actual:
            lines.append("⚠️ Capital data unavailable")
            lines.append("")
        
        # Market status
        now = datetime.now(RIYADH)
        market_open = (10 <= now.hour < 15) or (now.hour == 15 and now.minute <= 10)
        market_status = "OPEN" if market_open else "CLOSED"
        market_emoji = "🟢" if market_open else "🔴"
        lines.append(f"{market_emoji} Market: {market_status} | Updated: {now.strftime('%H:%M')}")
        
        # Regime info
        try:
            from market_regime import get_current_regime
            regime = get_current_regime()
            regime_name = regime.get('regime', 'NEUTRAL')
            regime_reason = regime.get('reason', '')
            lines.append(f"📈 Regime: {regime_name} | {regime_reason[:60]}")
        except Exception as e:
            log.warning(f"Could not load regime: {e}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Status error: {e}"

        result_lines.append(f"\n<i>Updated: {datetime.now(RIYADH).strftime('%H:%M:%S')}</i>")
        return "\n".join(result_lines)
    except Exception as e:
        return f"❌ Status error: {e}"

# ─── Telegram Handler ─────────────────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    msg = update.message
    log.info(f"Received from chat={msg.chat_id} user={msg.from_user.id if msg.from_user else 'none'} text={msg.text!r}")

    if not is_authorized(update):
        log.info(f"Rejected: chat={msg.chat_id} expected={GROUP_CHAT_ID} user={msg.from_user.id if msg.from_user else 'none'}")
        return

    text = update.message.text.strip()
    log.info(f"Command received: {text}")

    cmd = parse_command(text)
    if cmd is None:
        await update.message.reply_text(
            "❓ Unknown command.\n"
            "Use: BUY/SELL SYMBOL QTY @ PRICE  or  BUY/SELL SYMBOL QTY MARKET\n"
            "Also: STATUS | CANCEL | CLOSE ALL"
        )
        return

    await update.message.reply_text("⏳ Processing...")

    action = cmd["action"]

    if action == "HELP":
        result = (
            "📋 Available Commands:\n\n"
            "Orders:\n"
            "  BUY \u003csymbol\u003e \u003cqty\u003e @ \u003cprice\u003e — Limit buy\n"
            "  BUY \u003csymbol\u003e \u003cqty\u003e MARKET — Market buy\n"
            "  SELL \u003csymbol\u003e \u003cqty\u003e @ \u003cprice\u003e — Limit sell\n"
            "  SELL \u003csymbol\u003e \u003cqty\u003e MARKET — Market sell\n\n"
            "Info:\n"
            "  STATUS — Open positions\n"
            "  PRICE \u003csymbol\u003e — Live price\n"
            "  P/L or PNL — Profit/loss summary (realized P\u0026L from trades)\n"
            "  PORTFOLIO — Full account view\n"
            "  HISTORY — Recent orders\n"
            "  REPORT — Weekly performance report\n"
            "  DAILY REPORT — Daily P\u0026L report with trade details\n\n"
            "Actions:\n"
            "  CANCEL — Cancel all pending orders\n"
            "  CLOSE ALL — Sell all positions\n"
            "  STAND DOWN — Stop buying (market close)\n"
            "  DRY RUN — Toggle simulation mode\n\n"
            "Slash Commands (with /):\n"
            "  /Login — Browser token capture\n"
            "  /SS — Session status\n"
            "  /Orders — Order lifecycle view\n"
            "  /All — All orders list\n"
            "  /Pnl — Daily P\u0026L summary\n"
            "  /History — Order history\n"
            "  /HisCap — Historical capital (10 days)\n\n"
            "Examples:\n"
            "  BUY 1010 100 @ 45.50\n"
            "  SELL 1010 50 MARKET\n"
            "  PRICE 1010\n"
            "  STATUS\n"
            "  PNL\n"
            "  DAILY REPORT"
        )
    elif action == "PRICE":
        result = await get_price(cmd["symbol"])
    elif action == "PNL":
        result = await get_pnl()
    elif action == "HISTORY":
        result = await get_history()
    elif action == "PORTFOLIO":
        result = await get_portfolio()
    elif action == "DRY_RUN":
        result = toggle_dry_run()
    elif action == "STATUS":
        result = await get_status()
    elif action == "CANCEL":
        result = await cancel_all_orders()
    elif action == "CLOSE_ALL":
        result = await close_all_positions()
    elif action == "STAND_DOWN":
        # Create stand_down file to block all buys
        try:
            stand_down_path = f"{BASE_DIR}/stand_down"
            if os.path.exists(stand_down_path):
                result = "🛑 STAND DOWN already active — no buys allowed.\nRemove with: rm /home/mino/tasi-exec/stand_down"
            else:
                with open(stand_down_path, "w") as f:
                    f.write(f"STAND DOWN activated by user at {datetime.now(RIYADH).isoformat()}\n")
                    f.write("No new buys allowed until removed\n")
                result = (
                    "🛑 STAND DOWN ACTIVATED\n"
                    "No new buys will be executed.\n"
                    "Existing positions still monitored (stops/targets).\n\n"
                    "To resume trading:\n"
                    "rm /home/mino/tasi-exec/stand_down"
                )
        except Exception as e:
            result = f"❌ Failed to create stand_down: {e}"
    elif action == "REPORT":
        result = await get_weekly_report()
    elif action == "DAILY_REPORT":
        result = await get_daily_report()
    elif action == "VISUALIZE":
        result = await visualize_image(cmd["prompt"])
    elif action in ("BUY", "SELL"):
        # ─── Phase 3: Validate session before executing trades ────────────
        if SESSION_ENABLED and validate_session:
            is_valid, session_msg = validate_session()
            if not is_valid:
                await update.message.reply_text(f"🚫 {session_msg}\nPlease run /Login first or wait for auto-refresh.")
                log.warning(f"Trade BLOCKED: {action} {cmd['symbol']} — {session_msg}")
                return

        result = await place_order(
            symbol=cmd["symbol"],
            action=action,
            qty=cmd["qty"],
            price_type=cmd["type"],
            price=cmd.get("price"),
        )
        if result.startswith("✅"):
            price = cmd.get("price", 0.0) or 0.0
            sym = cmd["symbol"]
            if action == "BUY":
                record_buy(sym, cmd["qty"], price)
            else:
                record_sell(sym, cmd["qty"], price)
    else:
        result = "❓ Unknown action."

    log.info(f"Result: {result}")
    await update.message.reply_text(result)


# ─── Session Command Handlers ───────────────────────────────────────────────

async def handle_login_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/Login — Phase 1: Capture tokens from browser."""
    log.info(f"handle_login_command called: chat={update.message.chat_id} user={update.message.from_user.id if update.message.from_user else 'none'}")
    if not is_authorized(update):
        log.info("handle_login_command: not authorized")
        return
    if not SESSION_ENABLED or not SessionCommands:
        log.info("handle_login_command: session not enabled")
        await update.message.reply_text("❌ Session management not available.")
        return
    log.info("handle_login_command: executing...")
    sc = SessionCommands()
    await sc.handle_login(update, ctx)
    log.info("handle_login_command: done")


async def handle_status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/SS — Session status check (all 3 phases)."""
    if not is_authorized(update):
        return
    if not SESSION_ENABLED or not SessionCommands:
        await update.message.reply_text("❌ Session management not available.")
        return
    sc = SessionCommands()
    await sc.handle_status(update, ctx)


async def handle_orders_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/ORDERS — Show all outstanding orders from orders.json (v4.4)."""
    if not is_authorized(update):
        return
    if not ORDER_HELPERS_AVAILABLE:
        await update.message.reply_text("❌ Order system not available (order_helpers not loaded).")
        return
    try:
        outstanding = get_outstanding_orders()
        if not outstanding:
            await update.message.reply_text("📋 No outstanding orders.")
            return

        # Sort by initiated_at (newest first)
        sorted_orders = sorted(
            outstanding.items(),
            key=lambda kv: kv[1].get("initiated_at", ""),
            reverse=True,
        )

        booked = get_booked_capital()
        lines = [f"📋 <b>Outstanding Orders ({len(outstanding)})</b>"]
        lines.append(f"   Booked: {booked:,.2f} SAR")
        lines.append("")

        for oid, o in sorted_orders:
            side = o.get("side", "?")
            sym = o.get("symbol", "?")
            qty = o.get("qty", 0)
            price = o.get("price", 0)
            otype = o.get("type", "?")
            status_code = o.get("status", 0)
            status_name = get_status_name(status_code)
            initiated_by = o.get("initiated_by", "?")
            price_str = "Market" if otype == "MARKET" else f"{price:.2f}"
            initiated_at = o.get("initiated_at", "?")
            # Trim ISO to HH:MM:SS
            time_part = initiated_at.split("T")[-1][:8] if "T" in initiated_at else "?"
            lines.append(
                f"  • <code>{oid}</code> {side} {qty}×{sym} @ {price_str} "
                f"[{status_name}] <i>({initiated_by}, {time_part})</i>"
            )

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        log.error(f"handle_orders_command error: {e}")
        await update.message.reply_text(f"❌ Error reading orders: {e}")


async def handle_all_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/ALL — Show orders across all statuses (recent terminal + outstanding)."""
    if not is_authorized(update):
        return
    if not ORDER_HELPERS_AVAILABLE:
        await update.message.reply_text("❌ Order system not available.")
        return
    try:
        all_orders = load_orders()
        if not all_orders:
            await update.message.reply_text("📋 No orders recorded.")
            return

        # Sort by initiated_at (newest first)
        sorted_orders = sorted(
            all_orders.items(),
            key=lambda kv: kv[1].get("initiated_at", ""),
            reverse=True,
        )

        # Last 15
        lines = [f"📋 <b>Recent Orders (last 15 of {len(all_orders)})</b>", ""]
        for oid, o in sorted_orders[:15]:
            side = o.get("side", "?")
            sym = o.get("symbol", "?")
            qty = o.get("qty", 0)
            price = o.get("price", 0)
            otype = o.get("type", "?")
            status_code = o.get("status", 0)
            status_name = get_status_name(status_code)
            price_str = "Market" if otype == "MARKET" else f"{price:.2f}"
            initiated_at = o.get("initiated_at", "?")
            time_part = initiated_at.split("T")[-1][:8] if "T" in initiated_at else "?"
            lines.append(
                f"  • <code>{oid}</code> {side} {qty}×{sym} @ {price_str} "
                f"[{status_name}] <i>({time_part})</i>"
            )

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        log.error(f"handle_all_command error: {e}")
        await update.message.reply_text(f"❌ Error reading orders: {e}")



async def handle_pnl_command(update, context):
    """/PNL — Show daily P&L summary (last 2 trading days). Phase 5."""
    try:
        sys.path.insert(0, BASE_DIR)
        from history_io import read_daily_pnl
        rows = read_daily_pnl(last_n=2)
        if not rows:
            await update.message.reply_text("📊 No P&L data yet. Run bookkeeper.record_daily_pnl() or wait for hard close.")
            return
        lines = ["📊 <b>Daily P&L (last 2 trading days)</b>", ""]
        total_pnl = 0.0
        for r in rows:
            date = r.get("date", "?")
            equity = float(r.get("equity", 0) or 0)
            booked = float(r.get("booked", 0) or 0)
            cash = float(r.get("cash", 0) or 0)
            total = float(r.get("total", 0) or 0)
            pnl = float(r.get("pnl", 0) or 0)
            trades = int(r.get("trades", 0) or 0)
            notes = r.get("notes", "")
            total_pnl += pnl
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"<b>{date}</b> {emoji} <b>P&L: {pnl:+,.2f} SAR</b>")
            lines.append(f"  Equity: {equity:,.2f} | Booked: {booked:,.2f} | Cash: {cash:,.2f}")
            lines.append(f"  Total: {total:,.2f} SAR | Trades: {trades}")
            if notes:
                lines.append(f"  Notes: <i>{notes}</i>")
            lines.append("")
        if len(rows) > 1:
            emoji = "🟢" if total_pnl >= 0 else "🔴"
            lines.append(f"{emoji} <b>2-day P&L: {total_pnl:+,.2f} SAR</b>")
        
        # Add full daily report
        try:
            import bookkeeper
            report = bookkeeper.generate_daily_report()
            lines.append("")
            lines.append("📋 <b>Full Daily Report</b>")
            lines.append("<pre>" + report[:1500] + "</pre>")
            if len(report) > 1500:
                lines.append("<i>... report truncated, check bookkeeper log for full details</i>")
        except Exception as e:
            lines.append(f"<i>Could not generate full report: {e}</i>")
        
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        log.error(f"handle_pnl_command error: {e}")
        await update.message.reply_text(f"❌ Error reading P&L: {e}")


async def handle_hiscap_command(update, context):
    """ /HisCap — Show historical capital table from daily_pnl.csv. """
    try:
        sys.path.insert(0, BASE_DIR)
        from history_io import read_daily_pnl
        rows = read_daily_pnl(last_n=10)
        if not rows:
            await update.message.reply_text("📊 No capital history found.")
            return
        
        lines = ["💰 <b>Historical Capital (Last 10 Trading Days)</b>", ""]
        lines.append("<code>Date       | Capital  | Trading  | +/-      | %      | Tx</code>")
        lines.append("<code>-----------+----------+----------+----------+--------+-------</code>")
        
        total_pnl = 0.0
        prev_total = None
        
        # daily_pnl rows are oldest first already
        for row in rows:
            date_str = row.get("date", "?")
            total = float(row.get("total", 0) or 0)
            pnl = float(row.get("pnl", 0) or 0)  # Trading PnL only
            trades = row.get("trades", 0)
            deposits = float(row.get("deposits", 0) or 0)
            withdrawals = float(row.get("withdrawals", 0) or 0)
            
            # Calculate account value change (includes deposits/withdrawals)
            if prev_total is not None:
                account_change = round(total - prev_total, 2)
                start_str = f"{prev_total:.2f}"
                # PnL% based on TRADING PnL only, not account change
                pnl_pct = (pnl / prev_total) * 100 if prev_total != 0 else 0
            else:
                account_change = pnl
                start_str = "N/A"
                pnl_pct = (pnl / 1000) * 100 if pnl != 0 else 0
            
            total_pnl += pnl  # Sum trading PnL, not account change
            
            emoji = "🟢" if pnl >= 0 else "🔴"
            
            # Build +/- column: trading PnL with fund/withdraw indicators
            plus_minus = ""
            if deposits > 0:
                plus_minus += f" +{deposits:.0f}💰"
            if withdrawals > 0:
                plus_minus += f" -{withdrawals:.0f}🏧"
            
            lines.append(f"<code>{date_str} | {total:>8.2f} | {pnl:>+8.2f} | {plus_minus:<8} | {pnl_pct:>+5.2f}% | {trades:>5}</code> {emoji}")
            
            prev_total = total
        
        lines.append("<code>-----------+----------+----------+----------+--------+-------</code>")
        
        if len(rows) > 1:
            emoji = "🟢" if total_pnl >= 0 else "🔴"
            # Calculate total PnL% based on first day's starting capital
            first_total = float(rows[0].get("total", 0) or 0)
            if first_total > 0:
                total_pnl_pct = (total_pnl / first_total) * 100
            else:
                total_pnl_pct = 0
            lines.append(f"<code>           |          | {total_pnl:>+8.2f} |          | {total_pnl_pct:>+5.2f}% |       </code> {emoji} <b>Total Trading PnL</b>")
        
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        log.error(f"handle_hiscap_command error: {e}")
        await update.message.reply_text(f"❌ Error reading capital history: {e}")


async def handle_history_command(update, context):
    """/HISTORY — Show today's orders as a table."""
    try:
        sys.path.insert(0, BASE_DIR)
        from history_io import read_order_history
        from datetime import datetime
        
        # Get today's date in Riyadh timezone
        today = datetime.now(RIYADH).strftime("%Y-%m-%d")
        today_short = datetime.now(RIYADH).strftime("%m-%d")
        
        orders = read_order_history(last_n_orders=100, days=1)
        if not orders:
            await update.message.reply_text("📜 No order history yet.")
            return
        
        # Filter: today's real FILLED orders only
        real_orders = [o for o in orders if 
                       not o.get("order_id", "").startswith("TEST") and 
                       o.get("status") == "FILLED" and
                       (o.get("date", "") == today or o.get("date", "") == today_short)]
        
        if not real_orders:
            await update.message.reply_text(f"📜 No orders today ({today}).")
            return
        
        # Sort ascending (oldest first) by time
        real_orders.sort(key=lambda o: o.get("time", ""))
        
        # Build table with PnL calculation
        lines = [f"📜 <b>Today's Orders — {len(real_orders)} orders</b>", ""]
        lines.append("<code>Time | ID  | Side | Qty | Symbol | Price  | Total   | Fees | Trigger       | PnL</code>")
        lines.append("<code>-----+-----+------+-----+--------+--------+---------+------+---------------+------</code>")
        
        # Build lookup for buy orders (to calculate PnL for sells)
        buy_orders = {}
        for o in real_orders:
            if o.get("side") == "BUY":
                key = (o.get("symbol"), o.get("qty"))
                buy_orders[key] = float(o.get("price", 0) or 0)
        
        for o in real_orders:
            time = o.get("time", "")[:5] or "--:--"
            oid = o.get("order_id", "?")[:4]
            side = o.get("side", "?")[:4]
            qty = str(o.get("qty", 0))[:3]
            sym = o.get("symbol", "?")[:6]
            price = float(o.get("price", 0) or 0)
            total = o.get("total", "")[:7]
            fees = o.get("fees", "")[:5]
            trigger = o.get("trigger_basis", "")[:15]
            pnl = o.get("pnl", "")[:6]
            
            # Calculate actual PnL for SELL orders
            if not pnl and side == "SELL":
                buy_key = (o.get("symbol"), o.get("qty"))
                if buy_key in buy_orders:
                    buy_price = buy_orders[buy_key]
                    sell_price = price
                    qty_num = int(o.get("qty", 0) or 0)
                    pnl_value = (sell_price - buy_price) * qty_num
                    pnl = f"{pnl_value:+.2f}"
                else:
                    pnl = "~"
            elif side == "BUY":
                pnl = "-"
            
            price_str = f"{price:.2f}" if price else "MKT"
            
            lines.append(f"<code>{time:<5}| {oid:<4}| {side:<5}| {qty:<4}| {sym:<7}| {price_str:<7}| {total:<8}| {fees:<5}| {trigger:<15}| {pnl:<6}</code>")
        
        lines.append("")
        lines.append(f"<i>Full CSV: history/order_history.csv</i>")
        
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        
    except Exception as e:
        log.error(f"handle_history_command error: {e}")
        await update.message.reply_text(f"❌ Error reading history: {e}")


# ─── Fund / Withdraw Commands ─────────────────────────────────────────────

async def handle_fund_command(update, context):
    """ /Fund — Record a manual deposit to daily_pnl.csv. """
    try:
        if not context.args:
            await update.message.reply_text("❌ Usage: /Fund <amount> [notes]\nExample: /Fund 500 \"Saudi Riyal Deposit\"")
            return
        
        amount = float(context.args[0])
        notes = " ".join(context.args[1:]) if len(context.args) > 1 else "Manual deposit"
        
        if amount < 100:
            await update.message.reply_text(f"⚠️ Minimum deposit is 100 SAR. You entered: {amount:.2f}")
            return
        
        sys.path.insert(0, BASE_DIR)
        from history_io import append_daily_pnl
        from datetime import datetime
        
        today = datetime.now(RIYADH).strftime("%Y-%m-%d")
        
        # Read current capital
        with open(CAPITAL_FILE) as f:
            cap = json.load(f)
        
        append_daily_pnl(
            date=today,
            equity=cap.get("equity", 0),
            booked=cap.get("booked", 0),
            cash=cap.get("cash_3bucket", 0),
            total=cap.get("total_3bucket", 0),
            pnl=0,
            trades=0,
            deposits=amount,
            withdrawals=0,
            notes=notes,
        )
        
        await update.message.reply_text(f"✅ Deposit recorded: +{amount:.2f} SAR\nNotes: {notes}")
        
    except Exception as e:
        log.error(f"handle_fund_command error: {e}")
        await update.message.reply_text(f"❌ Error recording deposit: {e}")

async def handle_withdraw_command(update, context):
    """ /Withdraw — Record a manual withdrawal to daily_pnl.csv. """
    try:
        if not context.args:
            await update.message.reply_text("❌ Usage: /Withdraw <amount> [notes]\nExample: /Withdraw 300 \"Expense withdrawal\"")
            return
        
        amount = float(context.args[0])
        notes = " ".join(context.args[1:]) if len(context.args) > 1 else "Manual withdrawal"
        
        if amount < 100:
            await update.message.reply_text(f"⚠️ Minimum withdrawal is 100 SAR. You entered: {amount:.2f}")
            return
        
        sys.path.insert(0, BASE_DIR)
        from history_io import append_daily_pnl
        from datetime import datetime
        
        today = datetime.now(RIYADH).strftime("%Y-%m-%d")
        
        # Read current capital
        with open(CAPITAL_FILE) as f:
            cap = json.load(f)
        
        append_daily_pnl(
            date=today,
            equity=cap.get("equity", 0),
            booked=cap.get("booked", 0),
            cash=cap.get("cash_3bucket", 0),
            total=cap.get("total_3bucket", 0),
            pnl=0,
            trades=0,
            deposits=0,
            withdrawals=amount,
            notes=notes,
        )
        
        await update.message.reply_text(f"✅ Withdrawal recorded: -{amount:.2f} SAR\nNotes: {notes}")
        
    except Exception as e:
        log.error(f"handle_withdraw_command error: {e}")
        await update.message.reply_text(f"❌ Error recording withdrawal: {e}")


# ─── New Commands ───────────────────────────────────────────────────────────

async def get_price(symbol: str) -> str:
    """Get live price for a symbol. Priority: WebSocket → TickerChart → Derayah → yfinance."""
    
    # 1. Try WebSocket FIRST (most reliable real-time)
    try:
        base = symbol.replace(".SR", "")
        import glob
        ws_files = glob.glob(f"{BASE_DIR}/ws_prices_*.jsonl")
        if ws_files:
            ws_file = sorted(ws_files)[-1]  # Latest file
            with open(ws_file, 'r') as f:
                lines = f.readlines()
                for line in reversed(lines[-5000:]):  # Last 5000 lines for speed
                    try:
                        d = json.loads(line.strip())
                        if d.get("symbol") == base:
                            price = float(d["price"])
                            return f"💰 {symbol}: {price:.2f} SAR (WebSocket live)"
                    except:
                        continue
    except Exception as e:
        log.debug(f"WebSocket price lookup failed: {e}")
    
    # 2. Try TickerChart (CDP live)
    try:
        price = await _tc_price(symbol)
        if price:
            return f"💰 {symbol}: {price:.2f} SAR (TickerChart)"
    except Exception as e:
        log.debug(f"TC price failed: {e}")
    
    # 3. Try fetch_last_price (Derayah/yfinance)
    try:
        price = fetch_last_price(symbol)
        if price:
            return f"💰 {symbol}: {price:.2f} SAR (delayed)"
    except Exception as e:
        log.debug(f"fetch_last_price failed: {e}")
        
    return f"❌ Could not read price for {symbol}"

async def get_pnl() -> str:
    """Show profit/loss summary for open positions."""
    try:
        pos = _load_positions()
        open_pos = {k: v for k, v in pos.items() if not v.get("closed")}
        if not open_pos:
            return "📊 No open positions."
        
        lines = ["📈 P/L Summary:"]
        total_pnl = 0.0
        for sym, data in open_pos.items():
            entry = data.get("entry_price", 0)
            qty = data.get("qty", 0)
            current = await _tc_price(sym)
            if current and entry:
                pnl = (current - entry) * qty
                pct = ((current - entry) / entry) * 100
                emoji = "🟢" if pnl >= 0 else "🔴"
                lines.append(f"  {emoji} {sym}: {pnl:+.2f} SAR ({pct:+.2f}%) — {qty} shares @ {entry:.2f} → {current:.2f}")
                total_pnl += pnl
            else:
                lines.append(f"  ⚪ {sym}: {qty} shares @ {entry:.2f} (price unavailable)")
        
        emoji = "🟢" if total_pnl >= 0 else "🔴"
        lines.append(f"\n{emoji} Total P/L: {total_pnl:+.2f} SAR")
        return "\n".join(lines)
    except Exception as e:
        log.error(f"get_pnl error: {e}")
        return f"❌ P/L error: {e}"

async def get_history() -> str:
    """Show recent order history from Derayah API."""
    try:
        orders = await derayah_api.get_orders()
        if not orders:
            return "📭 No recent orders."
        
        lines = ["📜 Recent Orders (last 10):"]
        for o in orders[:10]:
            status_code = o.get("status") or o.get("orderStatusId")
            status_map = {12: "FILLED", 1: "PENDING", 2: "PARTIAL", 4: "CANCELLED", 5: "REJECTED"}
            status = status_map.get(status_code, "?")
            side = "BUY" if o.get("side") == 1 else "SELL"
            sym = o.get("symbol", "?")
            qty = o.get("quantity", 0)
            price = o.get("price", 0)
            order_id = o.get("orderId", "?")
            date = o.get("orderDate", "?")
            lines.append(f"  {side} {qty}×{sym} @ {price:.2f} — {status} (ID: {order_id}) {date}")
        
        return "\n".join(lines)
    except Exception as e:
        log.error(f"get_history error: {e}")
        return f"❌ History error: {e}"

async def get_portfolio() -> str:
    """Show full portfolio summary."""
    try:
        p = await ensure_page()
        await p.bring_to_front()
        await p.goto("https://newonline.derayah.com/#/layout/trading-portfolio", wait_until="domcontentloaded", timeout=8000)
        await p.wait_for_timeout(2500)
        
        # Also get positions from file
        pos = _load_positions()
        open_pos = {k: v for k, v in pos.items() if not v.get("closed")}
        
        lines = ["📊 Portfolio Summary:"]
        lines.append(f"Open positions: {len(open_pos)}")
        
        total_value = 0.0
        for sym, data in open_pos.items():
            qty = data.get("qty", 0)
            entry = data.get("entry_price", 0)
            current = await _tc_price(sym)
            if current:
                value = current * qty
                total_value += value
                pnl = (current - entry) * qty
                lines.append(f"  {sym}: {qty} shares @ {entry:.2f} → {current:.2f} = {value:.2f} SAR (P/L: {pnl:+.2f})")
            else:
                lines.append(f"  {sym}: {qty} shares @ {entry:.2f}")
        
        lines.append(f"\n💰 Total position value: {total_value:.2f} SAR")
        lines.append(f"Session: {'LOGGED IN ✅' if 'newonline.derayah.com' in p.url else 'LOGGED OUT ❌'}")
        
        return "\n".join(lines)
    except Exception as e:
        log.error(f"get_portfolio error: {e}")
        return f"❌ Portfolio error: {e}"

DRY_RUN_MODE = False

async def get_weekly_report() -> str:
    """Get the latest weekly report from relearning folder."""
    try:
        import glob
        reports = glob.glob(str(BASE_DIR / "relearning" / "report_*.html"))
        if not reports:
            return "📊 No weekly reports available yet. First report will be generated Friday."
        
        latest = max(reports, key=os.path.getmtime)
        week = os.path.basename(latest).replace("report_", "").replace(".html", "")
        
        json_file = latest.replace(".html", ".json")
        if os.path.exists(json_file):
            with open(json_file) as f:
                data = json.load(f)
            
            actual_pnl = data['summary']['actual_total_pnl']
            trades = data['summary']['actual_trades']
            
            msg = f"""📊 Weekly Report: {week}

Actual Trades: {trades}
Actual PnL: {actual_pnl:+.2f}%

Approaches Compared:
"""
            for app in data['approaches']:
                msg += f"  {app['name']}: {app['total_pnl']:+.2f}%\n"
            
            if data['recommendations']:
                msg += "\nTop Recommendations:\n"
                for rec in data['recommendations'][:3]:
                    msg += f"  • {rec['issue']}\n"
            
            return msg
        
        return f"📊 Latest report: {week}\nFile: {latest}"
    except Exception as e:
        log.error(f"get_weekly_report error: {e}")
        return f"❌ Report error: {e}"

async def get_daily_report() -> str:
    """Get today's trading summary."""
    try:
        today = datetime.now(RIYADH).strftime("%Y-%m-%d")
        pos_file = BASE_DIR / f"positions_{today}.json"
        
        if not pos_file.exists():
            return f"📊 No trading activity today ({today})."
        
        with open(pos_file) as f:
            positions = json.load(f)
        
        open_pos = {k: v for k, v in positions.items() if not v.get("closed")}
        closed_pos = {k: v for k, v in positions.items() if v.get("closed")}
        
        msg = f"📊 Daily Report: {today}\n\n"
        msg += f"Open positions: {len(open_pos)}\n"
        msg += f"Closed trades: {len(closed_pos)}\n"
        
        if closed_pos:
            total_pnl = sum(v['pnl_pct'] for v in closed_pos.values() if 'pnl_pct' in v)
            msg += f"Total PnL: {total_pnl:+.2f}%\n"
            for sym, data in closed_pos.items():
                pnl = data.get('pnl_pct', 0)
                emoji = "🟢" if pnl >= 0 else "🔴"
                msg += f"  {emoji} {sym}: {pnl:+.2f}%\n"
        
        return msg
    except Exception as e:
        log.error(f"get_daily_report error: {e}")
        return f"❌ Daily report error: {e}"

def toggle_dry_run() -> str:
    """Toggle dry run (simulation) mode."""
    global DRY_RUN_MODE
    DRY_RUN_MODE = not DRY_RUN_MODE
    status = "ON ✅" if DRY_RUN_MODE else "OFF ❌"
    return f"🧪 Dry run mode: {status}\nOrders will be {'SIMULATED' if DRY_RUN_MODE else 'REAL'}"

async def visualize_image(prompt: str) -> str:
    """Generate image via ComfyUI on Amin-PC."""
    try:
        import urllib.request
        import urllib.parse
        
        PC_HOST = "192.168.1.228"
        COMFYUI_URL = f"http://{PC_HOST}:8188"
        
        # Check if ComfyUI is running
        try:
            req = urllib.request.Request(f"{COMFYUI_URL}/system_stats", method='GET')
            urllib.request.urlopen(req, timeout=5)
        except:
            return "❌ ComfyUI not running on Amin-PC.\nStart it first: run_comfyui.bat"
        
        # Build workflow for text2img
        workflow = {
            "3": {
                "inputs": {
                    "seed": int(time.time()),
                    "steps": 25,
                    "cfg": 7.5,
                    "sampler_name": "euler_ancestral",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0]
                },
                "class_type": "KSampler"
            },
            "4": {
                "inputs": {"ckpt_name": "v1-5-pruned.safetensors"},
                "class_type": "CheckpointLoaderSimple"
            },
            "5": {
                "inputs": {"width": 512, "height": 512, "batch_size": 1},
                "class_type": "EmptyLatentImage"
            },
            "6": {
                "inputs": {"text": prompt},
                "class_type": "CLIPTextEncode"
            },
            "7": {
                "inputs": {"text": "bad quality, blurry, low resolution, watermark"},
                "class_type": "CLIPTextEncode"
            },
            "8": {
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
                "class_type": "VAEDecode"
            },
            "9": {
                "inputs": {"filename_prefix": "ComfyUI", "images": ["8", 0]},
                "class_type": "SaveImage"
            }
        }
        
        # Send prompt to ComfyUI
        data = json.dumps({"prompt": workflow}).encode()
        req = urllib.request.Request(
            f"{COMFYUI_URL}/prompt",
            data=data,
            headers={"Content-Type": "application/json"},
            method='POST'
        )
        
        response = urllib.request.urlopen(req, timeout=30)
        result = json.loads(response.read())
        
        if "prompt_id" in result:
            prompt_id = result["prompt_id"]
            return f"🎨 Generating: \"{prompt}\"\n⏳ Prompt ID: {prompt_id}\nImage will appear in ComfyUI output folder."
        else:
            return f"⚠️ Unexpected response: {result}"
            
    except Exception as e:
        log.error(f"visualize_image error: {e}")
        return f"❌ Image generation failed: {e}"

    # Forward order results to Amin's DM when command came from EXEC group.
    # Always forward so Amin sees confirmation/failure without watching EXEC group.
    if action in ("BUY", "SELL") and msg.chat_id == GROUP_CHAT_ID:
        try:
            bot_inst = ctx.bot
            asyncio.create_task(bot_inst.send_message(chat_id=OWNER_ID, text=result))
        except Exception as e:
            log.warning(f"DM forward failed: {e}")

# ─── Derayah session keepalive ───────────────────────────────────────────────

KEEPALIVE_INTERVAL = 15 * 60   # 15 min — Derayah sessions time out at ~30 min idle
DERAYAH_DASHBOARD  = "https://newonline.derayah.com/#/layout/dashboard"
OWNER_CHAT_ID      = GROUP_CHAT_ID  # alerts go to exec group (bot can't DM users directly)

CREDS_FILE = "/home/mino/tasi-exec/derayah_creds.json"

def _load_creds() -> dict | None:
    try:
        if os.path.exists(CREDS_FILE):
            with open(CREDS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return None

async def _derayah_login(page) -> bool:
    """Attempt auto-login using stored credentials. Returns True if successful."""
    creds = _load_creds()
    if not creds:
        log.warning("Auto-login: no creds file found at derayah_creds.json")
        return False
    try:
        login_url = "https://onboarding.derayah.com/#/signin"
        await page.goto(login_url, wait_until="domcontentloaded", timeout=10000)
        await page.wait_for_timeout(2000)

        # National ID field
        nid = page.locator("input[placeholder*='National'], input[formcontrolname*='national'], input[formcontrolname*='username'], input[type='text']").first
        await nid.wait_for(state="visible", timeout=5000)
        await nid.fill(str(creds["national_id"]))
        await page.wait_for_timeout(400)

        # Password field
        pwd = page.locator("input[type='password']").first
        await pwd.fill(str(creds["password"]))
        await page.wait_for_timeout(400)

        # Submit
        btn = page.locator("button[type='submit'], button:has-text('Login'), button:has-text('Sign In'), button:has-text('تسجيل')").first
        await btn.click(timeout=4000)
        await page.wait_for_timeout(4000)

        # Check if we landed on the app (not still on signin)
        if "onboarding.derayah.com" in page.url and "signin" in page.url:
            # Might need OTP — alert owner
            log.warning("Auto-login: stopped at OTP/2FA step — manual intervention needed")
            bot_inst = Bot(token=BOT_TOKEN)
            await bot_inst.send_message(
                chat_id=OWNER_CHAT_ID,
                text="⚠️ <b>Derayah session expired</b>\nAuto-login started but stopped at OTP/2FA.\nPlease complete login manually at https://newonline.derayah.com",
                parse_mode="HTML",
            )
            return False

        log.info("Auto-login: success — navigating to trading portfolio")
        await page.goto("https://newonline.derayah.com/#/layout/trading-portfolio",
                        wait_until="domcontentloaded", timeout=10000)
        await page.wait_for_timeout(5000)   # let TC iframe load and open its tab
        return True
    except Exception as e:
        log.warning(f"Auto-login failed: {e}")
        return False

async def _keepalive_once():
    """Keep Derayah sessions alive. Navigates newonline tab + pings REST API."""
    pw = None
    try:
        pw      = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=4000)
        ctx     = browser.contexts[0]
        pages   = ctx.pages

        # ── 1. newonline.derayah.com — navigate to dashboard (reliable refresh) ──
        # Silent fetch/HEAD is not enough; server needs a real page request to reset
        # the cookie session timer. We no longer do DOM automation here so navigation
        # is safe.
        derayah = next((p for p in pages if "newonline.derayah.com" in p.url), None)
        if derayah:
            if "onboarding.derayah.com" in derayah.url or "signin" in derayah.url:
                log.warning("Derayah online: session expired — attempting auto-login")
                ok = await _derayah_login(derayah)
                if not ok:
                    bot_inst = Bot(token=BOT_TOKEN)
                    await bot_inst.send_message(
                        chat_id=OWNER_CHAT_ID,
                        text="⚠️ <b>Derayah online session expired</b>\nPlease log in again:\nhttps://newonline.derayah.com",
                        parse_mode="HTML",
                    )
            else:
                await derayah.goto(DERAYAH_DASHBOARD, wait_until="domcontentloaded", timeout=10000)
                log.info("Derayah online keepalive: dashboard navigation OK")
        else:
            log.warning("Derayah online: no tab open — skipping (will NOT open new tabs)")

        # ── 2. TickerChart tab — check session is live ───────────────────────
        # Do NOT reload — that kills the WebSocket price stream used by poller.py
        tc = next((p for p in pages if TC_URL in p.url), None)
        if tc:
            try:
                logged_out = await tc.evaluate("""() => {
                    const b = document.body.innerText.substring(0, 200).toLowerCase();
                    return b.includes('login') || b.includes('sign in') || b.includes('username');
                }""")
                if logged_out:
                    log.warning("TC: trading session expired — manual login needed")
                    bot_inst = Bot(token=BOT_TOKEN)
                    await bot_inst.send_message(
                        chat_id=OWNER_CHAT_ID,
                        text="⚠️ <b>Derayah Trade session expired</b>\nPlease log in on the Trade tab.",
                        parse_mode="HTML",
                    )
                else:
                    # Minimal scroll to keep WebSocket alive (no visual disturbance)
                    await tc.evaluate("""() => {
                        window.scrollBy(0, 1);
                        window.scrollBy(0, -1);
                    }""")
                    log.info("TC keepalive: session OK + micro-scroll to keep WS alive")
            except Exception as e:
                log.warning(f"TC keepalive check error: {e}")
        else:
            log.info("TC keepalive: tab not open (skipped)")

        # ── 3. TC tab JWT — re-open via 'Derayah Trade' click if expiring ───
        tc = next((p for p in ctx.pages if TC_URL in p.url), None)
        needs_refresh = False
        if tc:
            try:
                token = await tc.evaluate(
                    "() => JSON.parse(localStorage.getItem('TC_DERAYAH')||'{}').token || ''"
                )
                if token:
                    parts = token.split(".")
                    if len(parts) == 3:
                        pad = parts[1] + "=" * (-len(parts[1]) % 4)
                        exp = json.loads(base64.urlsafe_b64decode(pad.encode())).get("exp", 0)
                        if exp - time.time() < 300:
                            needs_refresh = True
                else:
                    needs_refresh = True  # no token at all
            except Exception as e:
                log.warning(f"TC JWT expiry check: {e}")
        else:
            needs_refresh = True  # TC tab not open

        if needs_refresh:
            try:
                log.info("TC tab: JWT expiring/missing — fetching fresh TickerChartUrl")
                derayah_pg = next((p for p in ctx.pages if "newonline.derayah.com" in p.url), None)
                if derayah_pg:
                    # 1. Get current access token for the API call
                    access_token = await derayah_pg.evaluate(
                        "() => localStorage.getItem('Derayah_accesstoken') || ''"
                    )
                    
                    # 2. Call TickerChartUrl endpoint (works even with expired token)
                    import requests as _requests
                    headers = {
                        'Authorization': f'Bearer {access_token}',
                        'Content-Type': 'application/json',
                        'Origin': 'https://newonline.derayah.com',
                        'Referer': 'https://newonline.derayah.com/',
                    }
                    
                    resp = _requests.get(
                        'https://api.derayah.com/apispark/trade/TickerChartUrl',
                        headers=headers,
                        timeout=10
                    )
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        tc_url = data.get('data', '')
                        
                        if tc_url:
                            log.info(f"TickerChartUrl: got fresh SSO URL ({tc_url[:60]}...)")
                            
                            # 3. Open new tab with the SSO URL
                            new_page = await ctx.new_page()
                            await new_page.goto(tc_url, wait_until='domcontentloaded', timeout=15000)
                            await asyncio.sleep(5)
                            
                            # 4. Verify new token
                            new_tok = await new_page.evaluate(
                                "() => JSON.parse(localStorage.getItem('TC_DERAYAH')||'{}').token || ''"
                            )
                            
                            if new_tok:
                                # Decode expiry
                                parts = new_tok.split('.')
                                pad = parts[1] + '=' * (-len(parts[1]) % 4)
                                payload = json.loads(base64.urlsafe_b64decode(pad.encode()))
                                exp = payload.get('exp', 0)
                                log.info(f"TC tab: opened with fresh JWT (expires in {(exp-time.time())/60:.0f} min)")
                                
                                # 5. Close OLD TC tabs to avoid buildup
                                old_tc_pages = [p for p in ctx.pages if p != new_page and TC_URL in p.url]
                                for old_tc in old_tc_pages:
                                    try:
                                        await old_tc.close()
                                        log.info("Closed old TC tab")
                                    except Exception:
                                        pass
                                
                                derayah_api.invalidate_token()
                            else:
                                log.warning("New TC tab opened but no TC_DERAYAH token found")
                                await new_page.close()
                        else:
                            log.warning("TickerChartUrl returned empty data")
                    else:
                        log.warning(f"TickerChartUrl API failed: {resp.status_code} {resp.text[:100]}")
                        
                        # FALLBACK: try the old "Derayah Trade" click
                        log.info("Falling back to Derayah Trade click...")
                        await derayah_pg.evaluate("""() => {
                            const link = Array.from(document.querySelectorAll('a.nav-link'))
                                .find(e => e.textContent.trim() === 'Derayah Trade');
                            if (link) link.click();
                        }""")
                        await derayah_pg.wait_for_timeout(5000)
                        
                        tc_pages = [p for p in ctx.pages if TC_URL in p.url]
                        if tc_pages:
                            new_tok = await tc_pages[0].evaluate(
                                "() => JSON.parse(localStorage.getItem('TC_DERAYAH')||'{}').token || ''"
                            )
                            if new_tok:
                                log.info("TC tab: re-opened via Derayah Trade click (fallback)")
                                derayah_api.invalidate_token()
            except Exception as e:
                log.warning(f"TC tab refresh error: {e}")

        # ── 4. REST API ping — keeps the JWT session alive ───────────────────
        try:
            await derayah_api.get_orders()
            log.info("REST API keepalive: JWT valid")
        except Exception as e:
            log.warning(f"REST API keepalive: JWT ping failed: {e}")

    except Exception as e:
        log.warning(f"Keepalive error: {e}")
    finally:
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass


def _keepalive_thread():
    """Background thread — refreshes Derayah every 15 min forever."""
    time.sleep(10)   # wait for bot to fully start first
    while True:
        try:
            asyncio.run(_keepalive_once())
        except Exception as e:
            log.warning(f"Keepalive thread error: {e}")
        time.sleep(KEEPALIVE_INTERVAL)


def _capital_refresh_thread():
    """Background thread — scrapes actual capital from Derayah every 30 min during market hours."""
    time.sleep(60)   # wait for bot to fully start first
    while True:
        try:
            now = datetime.now(RIYADH)
            market_open = (10 <= now.hour < 15) or (now.hour == 15 and now.minute <= 10)
            
            if market_open:
                log.info("Capital refresh: scraping actual balance from Derayah...")
                actual = asyncio.run(get_actual_balance_from_derayah())
                if actual:
                    try:
                        with open(CAPITAL_FILE, 'r') as f:
                            cap = json.load(f)
                        
                        # Update ALL fields
                        cap['available_capital'] = actual.get('available', cap.get('available_capital', 0))
                        cap['grand_total'] = actual.get('total', cap.get('grand_total', 1000.66))
                        cap['invested'] = actual.get('invested', cap.get('invested', 0))
                        cap['updated_at'] = datetime.now(RIYADH).isoformat()
                        cap['source'] = 'derayah-30min-refresh'
                        
                        with open(CAPITAL_FILE, 'w') as f:
                            json.dump(cap, f, indent=2)
                        
                        log.info(f"Capital refresh OK: available={cap['available_capital']:.2f}, grand_total={cap['grand_total']:.2f}")
                    except Exception as e:
                        log.warning(f"Capital refresh update failed: {e}")
                else:
                    log.warning("Capital refresh: scrape returned None")
            else:
                log.info("Capital refresh: market closed, skipping")
                
        except Exception as e:
            log.warning(f"Capital refresh thread error: {e}")
        
        # Sleep 30 minutes
        time.sleep(30 * 60)

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    log.info("TASI Execution Bot starting...")

    # Start Derayah session keepalive in background
    t = threading.Thread(target=_keepalive_thread, daemon=True)
    t.start()
    log.info(f"Session keepalive started — refreshing Derayah every {KEEPALIVE_INTERVAL//60} min")
    
    # Start capital refresh thread
    t2 = threading.Thread(target=_capital_refresh_thread, daemon=True)
    t2.start()
    log.info("Capital refresh started — scraping every 30 min during market hours")

    app = Application.builder().token(BOT_TOKEN).build()
    # Add session management command handlers FIRST (before MessageHandler)
    if SESSION_ENABLED:
        app.add_handler(CommandHandler("Login", handle_login_command))
        app.add_handler(CommandHandler("SS", handle_status_command))
        log.info("Session commands enabled: /Login, /SS")
    # Phase 4.4: Order lifecycle commands
    if ORDER_HELPERS_AVAILABLE:
        app.add_handler(CommandHandler("Orders", handle_orders_command))
        app.add_handler(CommandHandler("ORDERS", handle_orders_command))
        app.add_handler(CommandHandler("All", handle_all_command))
        log.info("Order commands enabled: /Orders, /All")
    # Phase 5: Daily P&L + Order history
    app.add_handler(CommandHandler("Pnl", handle_pnl_command))
    app.add_handler(CommandHandler("PNL", handle_pnl_command))
    app.add_handler(CommandHandler("History", handle_history_command))
    app.add_handler(CommandHandler("history", handle_history_command))
    app.add_handler(CommandHandler("HISTORY", handle_history_command))
    app.add_handler(CommandHandler("HisCap", handle_hiscap_command))
    app.add_handler(CommandHandler("HISCAP", handle_hiscap_command))
    log.info("HisCap commands enabled: /HisCap")

    log.info("History commands enabled: /Pnl, /History, /HisCap")
    # Phase 5.5: Fund / Withdraw commands
    app.add_handler(CommandHandler("Fund", handle_fund_command))
    app.add_handler(CommandHandler("FUND", handle_fund_command))
    app.add_handler(CommandHandler("Withdraw", handle_withdraw_command))
    app.add_handler(CommandHandler("WITHDRAW", handle_withdraw_command))
    log.info("Fund commands enabled: /Fund, /Withdraw")
    # Then add message handler for regular commands
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info(f"Listening on group {GROUP_CHAT_ID}")

    app.run_polling(allowed_updates=["message"])

if __name__ == "__main__":
    main()

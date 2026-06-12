#!/usr/bin/env python3
"""
Derayah REST API client.

Reads the Bearer token from the live TickerChart session (TC_DERAYAH in
localStorage), then calls api.derayah.com directly — no DOM interaction.

Token is re-read from the TC page before every call so it's always fresh
(TC page auto-refreshes it). If the TC tab is not open, falls back to the
cached token (valid ~60 min after login).

Usage (async):
    from derayah_api import place_order, cancel_order, get_orders, get_positions

Usage (sync wrapper):
    import asyncio
    result = asyncio.run(place_order("2050", side=1, qty=7))
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.async_api import async_playwright

# ─── Config ──────────────────────────────────────────────────────────────────

CDP_URL       = "http://127.0.0.1:18801"
TC_URL        = "tickerchart"  # Matches both old derayah.tickerchart.net and new RealPrices URLs
API_BASE      = "https://api.derayah.com/trading"
PORTFOLIO     = 2063853
EXCHANGES     = [98, 99]    # TASI/TDWL exchange codes
EXCHANGE_CODE = 99          # TDWL — used for single-stock orders

SIDE_BUY      = 1
SIDE_SELL     = 2
TYPE_MARKET   = 7
TYPE_LIMIT    = 5

log = logging.getLogger(__name__)

# ─── Token cache ─────────────────────────────────────────────────────────────

_token_cache: str = ""
_token_ts:    float = 0.0
_TOKEN_TTL    = 1200   # seconds — re-read TC localStorage every 20 min


async def _read_token_from_file() -> str:
    """Read token from derayah_tokens.json (updated by refresh cron)."""
    try:
        with open("/home/mino/tasi-exec/derayah_tokens.json") as f:
            tokens = json.load(f)
        # TC_DERAYAH is the API token (not Derayah_accesstoken)
        token = tokens.get('TC_DERAYAH', '') or tokens.get('Derayah_accesstoken', '')
        return token
    except Exception as e:
        log.warning(f"derayah_api: token file read failed: {e}")
        return _token_cache


async def get_token() -> str:
    """Return fresh Bearer token, reading from file if cache is stale."""
    global _token_cache, _token_ts
    if not _token_cache or (time.time() - _token_ts) > _TOKEN_TTL:
        t = await _read_token_from_file()
        if t:
            _token_cache = t
            _token_ts    = time.time()
    return _token_cache


def invalidate_token() -> None:
    """Force a fresh token read on the next API call (call after TC tab reload)."""
    global _token_cache, _token_ts
    _token_cache = ""
    _token_ts    = 0.0


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json, text/plain, */*",
        "Origin":        "https://derayah.tickerchart.net",
        "Referer":       "https://derayah.tickerchart.net/",
    }


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%dT00:00:00")


# ─── Core API helpers ─────────────────────────────────────────────────────────

async def _get(path: str) -> dict:
    token = await get_token()
    r = requests.get(f"{API_BASE}/{path}", headers=_headers(token), timeout=10)
    if r.status_code == 401:
        invalidate_token()
    r.raise_for_status()
    return r.json()


async def _post(path: str, body: dict) -> dict:
    token = await get_token()
    r = requests.post(f"{API_BASE}/{path}", headers=_headers(token), json=body, timeout=10)
    if r.status_code == 401:
        invalidate_token()
    r.raise_for_status()
    return r.json()


# ─── Public API ───────────────────────────────────────────────────────────────

async def get_portfolio() -> list:
    """Return list of portfolios."""
    data = await _get("Portfolio/List")
    return data.get("data", []) or []


async def get_orders(intraday: bool = True) -> list:
    """Return today's orders as a list of dicts."""
    data = await _post("Order/List", {
        "portfolio":        PORTFOLIO,
        "orderStatusGroup": 0,
        "isIntraDay":       intraday,
        "exchanges":        EXCHANGES,
    })
    d = data.get("data") or {}
    return d.get("orders", []) or []


async def get_positions() -> list:
    """Return open positions."""
    data = await _post("UserPosition/ListPositions", {
        "currencyCode":  1,
        "exchangeCodes": EXCHANGES,
        "portfolio":     PORTFOLIO,
    })
    d = data.get("data") or {}
    return d.get("tradingAccountPositionInfoList", []) or []


async def preconfirm_order(symbol: str, side: int, qty: int,
                            order_type: int = TYPE_MARKET,
                            price: float = 0.0) -> dict:
    """Validate order and return {amount, fees, total} without placing it."""
    body = {
        "portfolio":        PORTFOLIO,
        "exchangeCode":     EXCHANGE_CODE,
        "symbol":           symbol,
        "orderSide":        str(side),
        "executionType":    str(order_type),
        "quantity":         str(qty),
        "fillType":         "1",
        "minQuantity":      None,
        "discloseQuantity": 0,
        "price":            str(price) if order_type == TYPE_LIMIT else "0",
        "validTill":        "1",
        "validTillDate":    _today(),
    }
    return await _post("Order/preconfirmPlace", body)


async def place_order(symbol: str, side: int, qty: int,
                       order_type: int = TYPE_MARKET,
                       price: float = 0.0) -> dict:
    """
    Place an order. Returns the API response dict.
    side: SIDE_BUY (1) or SIDE_SELL (2)
    order_type: TYPE_MARKET (7) or TYPE_LIMIT (5)
    """
    body = {
        "portfolio":        PORTFOLIO,
        "exchangeCode":     EXCHANGE_CODE,
        "symbol":           symbol,
        "orderSide":        str(side),
        "executionType":    str(order_type),
        "quantity":         str(qty),
        "fillType":         "1",
        "minQuantity":      None,
        "discloseQuantity": 0,
        "price":            str(price) if order_type == TYPE_LIMIT else "0",
        "validTill":        "1",
        "validTillDate":    _today(),
    }
    resp = await _post("Order/Place", body)
    action = "BUY" if side == SIDE_BUY else "SELL"
    order_type_str = "MKT" if order_type == TYPE_MARKET else f"LMT@{price}"
    if resp.get("isSuccess"):
        order_id = (resp.get("data") or {}).get("orderId", "?")
        log.info(f"place_order: {action} {qty}×{symbol} {order_type_str} → orderId={order_id}")
    else:
        log.error(f"place_order failed: {resp.get('message','')}")
    return resp


async def cancel_order(order_id: int) -> dict:
    """Cancel an order by its orderId."""
    body = {
        "orderId":      order_id,
        "portfolio":    PORTFOLIO,
        "exchangeCode": EXCHANGE_CODE,
    }
    resp = await _post("Order/Cancel", body)
    if resp.get("isSuccess"):
        log.info(f"cancel_order: orderId={order_id} cancelled")
    else:
        log.error(f"cancel_order failed: orderId={order_id} → {resp.get('message','')}")
    return resp


# ─── Sync wrappers (for use in synchronous code) ─────────────────────────────

def place_order_sync(symbol: str, side: int, qty: int,
                      order_type: int = TYPE_MARKET,
                      price: float = 0.0) -> dict:
    return asyncio.run(place_order(symbol, side, qty, order_type, price))


def cancel_order_sync(order_id: int) -> dict:
    return asyncio.run(cancel_order(order_id))


def get_orders_sync() -> list:
    return asyncio.run(get_orders())


def get_positions_sync() -> list:
    return asyncio.run(get_positions())

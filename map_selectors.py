#!/usr/bin/env python3
"""
Derayah selector mapper — auto-discovers live price selector and API endpoint.
Run during market hours (10:00–15:00 Riyadh):

    python3 /home/mino/tasi-exec/map_selectors.py 1010

Saves confirmed results to:  /home/mino/tasi-exec/selector_map.json
Full log at:                 /home/mino/tasi-exec/map_selectors.log
Sends Telegram summary when done.

After a successful run, poller.py and bot.py automatically load the selector
from selector_map.json — no manual edits needed.
"""

import asyncio
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.async_api import async_playwright

# ─── Config ──────────────────────────────────────────────────────────────────

CDP_URL        = "http://127.0.0.1:18801"
BOT_TOKEN      = "8989533040:AAFWzP_lYL3g_w4eXGxrvwdo-tBNdPxVYQU"
CHAT_ID        = 5529987063       # DM — diagnostic tool
BASE_DIR       = Path("/home/mino/tasi-exec")
SELECTOR_FILE  = BASE_DIR / "selector_map.json"
LOG_FILE       = BASE_DIR / "map_selectors.log"

TEST_SYM       = sys.argv[1] if len(sys.argv) > 1 else "1010"

# CSS selectors to probe (tried in order — first match that returns a sane price wins)
CANDIDATE_SELECTORS = [
    "[class*='last-price']",
    "[class*='lastPrice']",
    "[class*='LastPrice']",
    "[class*='current-price']",
    "[class*='currentPrice']",
    "[class*='CurrentPrice']",
    "[class*='trade-price']",
    "[class*='tradePrice']",
    "[class*='market-price']",
    "[data-field='lastPrice']",
    "[data-field='last']",
    "[data-field='tradePrice']",
    ".price-value",
    ".price",
    "span.price",
    "div.price",
    ".last-price",
    ".trade-price",
    ".ag-cell-value",   # ag-grid fallback (portfolio page)
]

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.DEBUG,
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ─── Telegram ─────────────────────────────────────────────────────────────────

def tg(text: str):
    import html as _html
    safe = _html.escape(text) if "<" in text or "&" in text else text
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": safe, "parse_mode": "HTML"},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        log.error(f"tg_send failed: {e}")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_plausible_price(text: str) -> float | None:
    """Return float if text looks like a TASI stock price (5–5000), else None."""
    try:
        val = float(text.strip().replace(",", ""))
        if 5.0 < val < 5000.0:
            return val
    except (ValueError, AttributeError):
        pass
    return None

def save_selector_map(data: dict):
    data["mapped_at"] = datetime.now().isoformat()
    data["symbol"]    = TEST_SYM
    with open(SELECTOR_FILE, "w") as f:
        json.dump(data, f, indent=2)
    log.info(f"Saved selector map → {SELECTOR_FILE}")

# ─── Main mapper ──────────────────────────────────────────────────────────────

async def run():
    log.info(f"=== Derayah selector mapper started (symbol={TEST_SYM}) ===")
    tg(f"🔍 <b>Selector mapper started</b> for symbol {TEST_SYM}\nLog: {LOG_FILE}")

    intercepted_api: list[dict] = []   # XHR/fetch calls that contain price data
    ws_messages: list[str]      = []   # WebSocket frames that contain price data

    pw = await async_playwright().start()

    try:
        browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=4000)
    except Exception as e:
        msg = f"❌ CDP connection failed: {e}\nIs Chromium running? Check: ps aux | grep chromium"
        log.error(msg)
        tg(msg)
        await pw.stop()
        return

    ctx    = browser.contexts[0]
    pages  = ctx.pages
    log.info(f"Open tabs: {[p.url[:80] for p in pages]}")

    derayah = next((p for p in pages if "derayah.com" in p.url), None)
    if not derayah:
        msg = "❌ No Derayah tab found. Open https://newonline.derayah.com and log in first."
        log.error(msg)
        tg(msg)
        await pw.stop()
        return

    log.info(f"Found Derayah tab: {derayah.url}")

    # ── Set up network interception before navigating ─────────────────────────

    async def on_response(response):
        url = response.url
        if "derayah.com" not in url and "derayah" not in url:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct and "javascript" not in ct:
            return
        try:
            body = await response.text()
            val = is_plausible_price(None)  # placeholder — we parse below
            # Look for numeric values that look like TASI prices in the JSON body
            matches = re.findall(r'(?:last[Pp]rice|price|lastTrade|close|current)["\s:]+([0-9]+(?:\.[0-9]+)?)', body)
            for m in matches:
                if is_plausible_price(m):
                    entry = {"url": url[:120], "price_hint": m, "ct": ct}
                    intercepted_api.append(entry)
                    log.debug(f"API hit: {url[:80]} → price_hint={m}")
                    break
        except Exception:
            pass

    derayah.on("response", on_response)

    # ── WebSocket listener ────────────────────────────────────────────────────

    async def on_websocket(ws):
        log.info(f"WebSocket opened: {ws.url[:80]}")

        async def on_frame(data):
            try:
                text = data if isinstance(data, str) else data.decode("utf-8", errors="ignore")
                if any(k in text for k in ("lastPrice", "last_price", "price", "close")):
                    matches = re.findall(r'[0-9]{2,4}\.[0-9]{1,4}', text)
                    for m in matches:
                        if is_plausible_price(m):
                            ws_messages.append(f"{ws.url[:60]} → {m}")
                            log.debug(f"WS price: {ws.url[:50]} value={m}")
                            break
            except Exception:
                pass

        ws.on("framereceived", on_frame)

    derayah.on("websocket", on_websocket)

    # ── Navigate to market watch for the symbol ───────────────────────────────

    target = f"https://newonline.derayah.com/#/layout/market-watch?symbol={TEST_SYM}"
    log.info(f"Navigating to {target}")
    try:
        await derayah.goto(target, wait_until="domcontentloaded", timeout=5000)
    except Exception as e:
        log.warning(f"Navigation warning (continuing): {e}")

    log.info("Waiting 2s for page to settle…")
    await derayah.wait_for_timeout(2000)

    # ── DOM scan: leaf elements with price-like text ──────────────────────────

    log.info("=== DOM scan: all visible leaf elements with price-like values ===")
    dom_hits = await derayah.evaluate("""() => {
        const hits = [];
        const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
        while (walk.nextNode()) {
            const el = walk.currentNode;
            if (el.children.length > 0) continue;
            const text = (el.innerText || el.textContent || '').trim().replace(/,/g, '');
            const val = parseFloat(text);
            if (isNaN(val) || val < 5 || val > 5000) continue;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const cls = (el.className || '').toString().substring(0, 120);
            const id  = el.id || '';
            hits.push({ tag: el.tagName, cls, id, text, x: Math.round(rect.x), y: Math.round(rect.y) });
        }
        return hits;
    }""")

    log.info(f"DOM scan found {len(dom_hits)} price-like leaf elements:")
    for h in dom_hits:
        log.info(f"  val={h['text']:>10}  tag={h['tag']:<6}  pos=({h['x']},{h['y']})  class={h['cls'][:80]}  id={h['id']}")

    # ── Try each candidate selector ───────────────────────────────────────────

    log.info("=== Testing candidate CSS selectors ===")
    confirmed_selector = None
    confirmed_price    = None

    for sel in CANDIDATE_SELECTORS:
        try:
            els = await derayah.query_selector_all(sel)
            for el in els:
                txt = await el.inner_text()
                val = is_plausible_price(txt)
                if val is not None:
                    log.info(f"  ✅ MATCH  selector={sel!r:50}  price={val}")
                    if confirmed_selector is None:
                        confirmed_selector = sel
                        confirmed_price    = val
                else:
                    log.debug(f"  ✗        selector={sel!r:50}  text={repr(txt[:30])}")
        except Exception as e:
            log.debug(f"  ✗        selector={sel!r:50}  err={e}")

    # ── Try to build a precise selector from DOM hits ─────────────────────────

    precise_selector = None
    if dom_hits:
        # Pick the hit most likely to be the last-trade price (highest y, price in 10–1000 range)
        def _to_float(s):
            try: return float(s)
            except ValueError: return None
        candidates = [h for h in dom_hits if _to_float(h["text"]) is not None and 10 < _to_float(h["text"]) < 1000]
        if candidates:
            best = sorted(candidates, key=lambda h: (len(h["cls"]), h["y"]))[0]
            if best["cls"]:
                first_cls = best["cls"].split()[0]
                precise_selector = f".{first_cls}" if first_cls else None
                if precise_selector:
                    try:
                        test_els = await derayah.query_selector_all(precise_selector)
                        for el in test_els:
                            txt = await el.inner_text()
                            val = is_plausible_price(txt)
                            if val:
                                log.info(f"  ✅ PRECISE  selector={precise_selector!r}  price={val}")
                                if confirmed_selector is None:
                                    confirmed_selector = precise_selector
                                    confirmed_price    = val
                                break
                    except Exception as e:
                        log.debug(f"  Precise selector test failed: {e}")

    # ── Page text dump ────────────────────────────────────────────────────────

    log.info("=== Page text (first 3000 chars) ===")
    body_text = await derayah.evaluate("() => document.body.innerText.substring(0, 3000)")
    log.info(body_text)

    # ── Network summary ───────────────────────────────────────────────────────

    log.info(f"=== API responses with price hints ({len(intercepted_api)} found) ===")
    for a in intercepted_api[:20]:
        log.info(f"  {a['url']}  → price_hint={a['price_hint']}")

    log.info(f"=== WebSocket price frames ({len(ws_messages)} found) ===")
    for m in ws_messages[:20]:
        log.info(f"  {m}")

    # ── Resource URLs (XHR/fetch to Derayah APIs) ─────────────────────────────

    log.info("=== Network resource URLs (Derayah APIs) ===")
    resources = await derayah.evaluate("""() =>
        performance.getEntriesByType('resource')
            .map(e => e.name)
            .filter(u => u.includes('derayah.com') && !u.includes('/connect/token'))
    """)
    api_endpoints = []
    for u in resources:
        log.info(f"  {u[:140]}")
        if any(k in u.lower() for k in ("price", "quote", "market", "stock", "symbol")):
            api_endpoints.append(u)

    # ── Save results ──────────────────────────────────────────────────────────

    result = {
        "selector":           confirmed_selector,
        "price_at_mapping":   confirmed_price,
        "precise_selector":   precise_selector,
        "dom_hits":           dom_hits[:10],
        "api_endpoints":      api_endpoints[:5],
        "intercepted_api":    intercepted_api[:5],
        "ws_messages":        ws_messages[:5],
        "candidate_tested":   CANDIDATE_SELECTORS,
    }
    save_selector_map(result)

    # ── Telegram summary ──────────────────────────────────────────────────────

    if confirmed_selector:
        msg = (
            f"✅ <b>Selector mapped for {TEST_SYM}</b>\n"
            f"Selector: <code>{confirmed_selector}</code>\n"
            f"Price at mapping: {confirmed_price}\n"
            f"DOM hits: {len(dom_hits)} | API hits: {len(intercepted_api)} | WS: {len(ws_messages)}\n"
            f"Saved → selector_map.json\n"
            f"poller.py will use this selector automatically next restart."
        )
    else:
        dom_summary = "\n".join(
            f"  {h['text']} ({h['tag']} .{h['cls'].split()[0] if h['cls'] else '?'})"
            for h in dom_hits[:5]
        ) or "  (none)"
        api_summary = "\n".join(f"  {a['url'][:80]}" for a in intercepted_api[:3]) or "  (none)"
        msg = (
            f"⚠️ <b>No selector auto-confirmed for {TEST_SYM}</b>\n"
            f"Market may be closed or prices not on screen.\n\n"
            f"DOM price-like elements:\n{dom_summary}\n\n"
            f"API hints:\n{api_summary}\n\n"
            f"Full log: {LOG_FILE}\n"
            f"Re-run at 10:05 once prices are live."
        )

    log.info(msg.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))
    tg(msg)

    await pw.stop()
    log.info("=== Selector mapper done ===")


asyncio.run(run())

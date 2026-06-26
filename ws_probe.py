#!/usr/bin/env python3
"""
TickerChart WebSocket frame probe.
Run during market hours to discover the live price message format.

    python3 /home/mino/tasi-exec/ws_probe.py [duration_seconds]

Connects to the open TickerChart tab via CDP, enables Network-level WS
interception (catches existing connections, not just new ones), collects
frames for <duration> seconds, saves unique topic/structure samples to
ws_frames.json, and sends a Telegram summary.

Once we know the frame format, this output drives the implementation of
the live price cache in bot.py and poller.py.
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

# ─── Config ───────────────────────────────────────────────────────────────────

CDP_URL        = "http://127.0.0.1:18801"
BOT_TOKEN = "8989533040:AAFWzP_lYL3g_w4eXGxrvwdo-tBNdPxVYQU"
CHAT_ID   = 5529987063   # DM — diagnostic tool
TC_URL    = "tickerchart"  # Match any tickerchart URL pattern
TC_FALLBACK_URLS = ["derayah.tickerchart.net", "newonline.derayah.com"]
BASE_DIR  = Path("/home/mino/tasi-exec")
OUT_FILE  = BASE_DIR / "ws_frames.json"
LOG_FILE  = BASE_DIR / "ws_probe.log"

DURATION  = int(sys.argv[1]) if len(sys.argv) > 1 else 90   # seconds to listen

# ─── Logging ──────────────────────────────────────────────────────────────────

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
    # DISABLED: No longer sending to Telegram to avoid spam
    # import html as _html
    # safe = _html.escape(text) if "<" in text or "&" in text else text
    # try:
    #     requests.post(
    #         f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
    #         json={"chat_id": CHAT_ID, "text": safe, "parse_mode": "HTML"},
    #         timeout=10,
    #     ).raise_for_status()
    # except Exception as e:
    #     log.error(f"tg failed: {e}")
    log.info(f"[TELEGRAM-DISABLED] {text}")

# ─── Main ─────────────────────────────────────────────────────────────────────

async def run():
    log.info(f"=== ws_probe started — listening {DURATION}s ===")
    tg(f"🔬 <b>WS probe started</b> on TickerChart tab — listening {DURATION}s for price frames.")

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=4000)
    except Exception as e:
        msg = f"❌ CDP connect failed: {e}"
        log.error(msg); tg(msg)
        await pw.stop()
        return

    ctx   = browser.contexts[0]
    pages = ctx.pages
    log.info(f"Open tabs ({len(pages)}): {[p.url[:70] for p in pages]}")

    # PRIORITIZE actual TickerChart tabs over dashboard tabs
    # Search for derayah.tickerchart.net FIRST, then fallback to newonline.derayah.com
    tc_page = None
    # First priority: actual TickerChart domain
    tc_page = next((p for p in pages if "derayah.tickerchart.net" in p.url), None)
    # Second priority: RealPrices page
    if not tc_page:
        tc_page = next((p for p in pages if "RealPrices" in p.url), None)
    # Third priority: any other tickerchart match
    if not tc_page:
        tc_page = next((p for p in pages if any(pat in p.url for pat in [TC_URL] + TC_FALLBACK_URLS)), None)
    
    if not tc_page:
        msg = f"❌ No TickerChart tab found. Open TickerChart in Chromium first."
        log.error(msg); tg(msg)
        await pw.stop()
        return

    log.info(f"Found TC tab: {tc_page.url[:80]}")

    # ── CDP session — captures frames from EXISTING WebSocket connections ──────
    # page.on("websocket") only fires for new connections opened after binding.
    # CDP Network.webSocketFrameReceived fires for all active connections.

    cdp = await tc_page.context.new_cdp_session(tc_page)
    await cdp.send("Network.enable")
    log.info("CDP Network domain enabled — intercepting WS frames")

    # Storage for frame analysis
    raw_frames: list[dict]        = []   # every frame, uncapped
    heartbeats: int               = 0
    topic_samples: dict[str, str] = {}   # {topic: first_payload}
    no_topic_samples: list[str]   = []   # frames with no "topic" field
    ws_urls: set[str]             = set()
    frame_counter: int            = 0

    # Separate log for raw frame dump (every single frame, full payload)
    raw_log = logging.getLogger("ws_raw")
    raw_log.setLevel(logging.DEBUG)
    raw_handler = logging.FileHandler(BASE_DIR / "ws_frames_raw.log")
    raw_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    raw_log.addHandler(raw_handler)
    raw_log.propagate = False

    # ── TASI daily tracker ──────────────────────────────────────────────────────
    tasi_tracker = {"high": None, "low": None, "open": None, "close": None, "date": datetime.now().strftime("%Y-%m-%d")}
    TASI_TOPIC = "QO.TASI.TAD"
    tasi_file = BASE_DIR / "tasi_daily.json"

    # ── Per-stock daily tracker ─────────────────────────────────────────────────
    stock_tracker = {}  # {symbol: {"high": ..., "low": ..., "open": ..., "close": ...}}
    stock_file = BASE_DIR / "stock_daily.json"

    def on_ws_frame(params):
        nonlocal heartbeats, frame_counter
        frame_counter += 1
        ts      = datetime.now().isoformat(timespec="milliseconds")
        data    = params.get("response", {})
        payload = data.get("payloadData", "")
        req_id  = params.get("requestId", "?")

        if not payload:
            return

        # Log every raw frame unconditionally (full payload, no truncation)
        raw_log.debug(f"[#{frame_counter} req={req_id}] {payload}")

        # Try JSON parse
        try:
            d = json.loads(payload)
        except Exception:
            raw_frames.append({
                "n": frame_counter, "ts": ts, "type": "binary/non-json",
                "req": req_id, "raw": payload,
            })
            log.debug(f"#{frame_counter} non-JSON: {payload[:120]}")
            return

        topic = d.get("topic", "")

        # Count heartbeats but still log them to raw
        if "HB" in topic:
            heartbeats += 1
            raw_frames.append({"n": frame_counter, "ts": ts, "type": "HB", "req": req_id, "data": d})
            return

        # Track TASI high/low
        if topic == TASI_TOPIC:
            try:
                last_price = float(d.get("last", 0))
                if last_price > 0:
                    if tasi_tracker["high"] is None or last_price > tasi_tracker["high"]:
                        tasi_tracker["high"] = last_price
                    if tasi_tracker["low"] is None or last_price < tasi_tracker["low"]:
                        tasi_tracker["low"] = last_price
                    # Set open on first trade
                    if tasi_tracker["open"] is None:
                        tasi_tracker["open"] = last_price
                    # Update close (last seen price)
                    tasi_tracker["close"] = last_price
                    log.debug(f"TASI tracker: high={tasi_tracker['high']}, low={tasi_tracker['low']}, last={last_price}")
            except Exception as e:
                log.debug(f"TASI tracking error: {e}")

        # Track per-stock high/low
        if topic.startswith("QO.") and topic != TASI_TOPIC:
            try:
                symbol = topic.split(".")[1]  # QO.2190.TAD → 2190
                last_price = float(d.get("last", 0))
                if last_price > 0 and symbol.isdigit():
                    if symbol not in stock_tracker:
                        stock_tracker[symbol] = {
                            "high": None, "low": None, "open": None, "close": None,
                            "date": datetime.now().strftime("%Y-%m-%d")
                        }
                    st = stock_tracker[symbol]
                    if st["high"] is None or last_price > st["high"]:
                        st["high"] = last_price
                    if st["low"] is None or last_price < st["low"]:
                        st["low"] = last_price
                    if st["open"] is None:
                        st["open"] = last_price
                    st["close"] = last_price
            except Exception as e:
                log.debug(f"Stock tracking error: {e}")

        # Record WS URL
        url = params.get("response", {}).get("url", "")
        if url:
            ws_urls.add(url[:120])

        entry = {"n": frame_counter, "ts": ts, "type": "data", "req": req_id, "data": d}
        raw_frames.append(entry)

        if topic:
            if topic not in topic_samples:
                topic_samples[topic] = json.dumps(d)
                log.info(f"  NEW TOPIC #{frame_counter}: {topic}  →  {json.dumps(d)[:300]}")
            else:
                log.debug(f"  #{frame_counter} {topic}: {json.dumps(d)[:200]}")
        else:
            no_topic_samples.append(json.dumps(d))
            log.info(f"  #{frame_counter} NO-TOPIC: {json.dumps(d)[:300]}")

    cdp.on("Network.webSocketFrameReceived", on_ws_frame)

    # ── Also catch new WS connections opened during probe ─────────────────────
    def on_new_ws(ws):
        log.info(f"  New WS opened during probe: {ws.url[:80]}")
        ws_urls.add(ws.url[:100])

    tc_page.on("websocket", on_new_ws)

    # ── Listen for DURATION seconds ───────────────────────────────────────────
    log.info(f"Listening for {DURATION}s… (market must be open for price frames)")
    await asyncio.sleep(DURATION)

    await cdp.detach()
    await pw.stop()

    # ── Summarise ─────────────────────────────────────────────────────────────

    log.info(f"=== Probe complete ===")
    log.info(f"Total frames captured: {len(raw_frames)}")
    log.info(f"Heartbeat frames skipped: {heartbeats}")
    log.info(f"Unique topics: {list(topic_samples.keys())}")
    log.info(f"No-topic frames: {len(no_topic_samples)}")
    log.info(f"WS URLs seen: {list(ws_urls)}")

    log.info("=== Topic samples ===")
    for topic, sample in topic_samples.items():
        log.info(f"  {topic}:\n    {sample}\n")

    log.info("=== No-topic samples ===")
    for s in no_topic_samples:
        log.info(f"  {s}")

    # ── Save to ws_frames.json (complete — every frame) ──────────────────────

    result = {
        "probed_at":          datetime.now().isoformat(),
        "duration_s":         DURATION,
        "total_frames":       frame_counter,
        "data_frames":        len([f for f in raw_frames if f.get("type") == "data"]),
        "heartbeats":         heartbeats,
        "ws_urls":            list(ws_urls),
        "topics":             list(topic_samples.keys()),
        "topic_samples":      topic_samples,
        "no_topic_samples":   no_topic_samples,
        "all_frames":         raw_frames,   # complete, no cap
    }
    with open(OUT_FILE, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    log.info(f"Saved {len(raw_frames)} frames → {OUT_FILE}")
    log.info(f"Raw frame log (every payload) → {BASE_DIR / 'ws_frames_raw.log'}")

    # ── Save TASI daily tracker ─────────────────────────────────────────────────
    if tasi_tracker["high"] is not None and tasi_tracker["low"] is not None:
        try:
            tasi_history = {}
            if tasi_file.exists():
                with open(tasi_file) as f:
                    tasi_history = json.load(f)
            
            # Save today's data
            tasi_history[tasi_tracker["date"]] = {
                "open": tasi_tracker["open"],
                "high": tasi_tracker["high"],
                "low": tasi_tracker["low"],
                "close": tasi_tracker["close"],
                "date": tasi_tracker["date"]
            }
            
            # Keep only last 30 days
            dates = sorted(tasi_history.keys())
            for old_date in dates[:-30]:
                del tasi_history[old_date]
            
            with open(tasi_file, "w") as f:
                json.dump(tasi_history, f, indent=2)
            
            log.info(f"TASI daily data saved: {tasi_tracker}")
        except Exception as e:
            log.error(f"Failed to save TASI tracker: {e}")

    # ── Save per-stock daily tracker ────────────────────────────────────────────
    if stock_tracker:
        try:
            stock_history = {}
            if stock_file.exists():
                with open(stock_file) as f:
                    stock_history = json.load(f)
            
            today = datetime.now().strftime("%Y-%m-%d")
            if today not in stock_history:
                stock_history[today] = {}
            
            for symbol, st in stock_tracker.items():
                if st["high"] is not None and st["low"] is not None:
                    stock_history[today][symbol] = {
                        "open": st["open"],
                        "high": st["high"],
                        "low": st["low"],
                        "close": st["close"]
                    }
            
            # Keep only last 30 days
            dates = sorted(stock_history.keys())
            for old_date in dates[:-30]:
                del stock_history[old_date]
            
            with open(stock_file, "w") as f:
                json.dump(stock_history, f, indent=2)
            
            log.info(f"Stock daily data saved: {len(stock_tracker)} stocks")
        except Exception as e:
            log.error(f"Failed to save stock tracker: {e}")

    # ── Telegram summary ───────────────────────────────────────────────────────

    data_count = len([f for f in raw_frames if f.get("type") == "data"])

    if topic_samples:
        topics_str = "\n".join(
            f"  <code>{t}</code>"
            for t in list(topic_samples.keys())[:12]
        )
        # Show one full sample frame for the first price-looking topic
        first_price_topic = next(
            (t for t in topic_samples if "HB" not in t), None
        )
        sample_str = ""
        if first_price_topic:
            sample_str = f"\nSample ({first_price_topic}):\n<code>{topic_samples[first_price_topic][:300]}</code>"

        msg = (
            f"✅ <b>WS probe done</b>\n"
            f"Total: {frame_counter} frames | Data: {data_count} | HB: {heartbeats}\n"
            f"Topics ({len(topic_samples)}):\n{topics_str}"
            f"{sample_str}\n\n"
            f"Logs: ws_probe.log | ws_frames_raw.log\n"
            f"Data: ws_frames.json"
        )
    else:
        msg = (
            f"⚠️ <b>WS probe: 0 data frames in {DURATION}s</b>\n"
            f"Total frames: {frame_counter} | HB: {heartbeats}\n"
            f"WS URLs seen: {list(ws_urls) or 'none'}\n"
            f"Market may be closed or TC not streaming.\n"
            f"Log: ws_probe.log"
        )

    log.info(msg.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))
    tg(msg)
    log.info("=== ws_probe done ===")


asyncio.run(run())

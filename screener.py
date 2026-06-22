#!/usr/bin/env python3
"""
TASI Pre-Market Screener
Runs before market open (09:50 Riyadh). Scans TASI stocks, scores by momentum
+ volume + proximity to S/R, sends top 1-2 picks with entry zones to Telegram.
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
import os

# ─── Config ──────────────────────────────────────────────────────────────────

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "8989533040:AAFWzP_lYL3g_w4eXGxrvwdo-tBNdPxVYQU")
CHAT_ID     = -5235925419  # Execution group (bot cannot DM users directly)

LOG_FILE      = "/home/mino/tasi-exec/screener.log"
PICKS_FILE    = "/home/mino/tasi-exec/picks.json"
BLOCKED_FILE  = "/home/mino/tasi-exec/blocked_stocks.json"
SHARIA_FILE   = "/home/mino/tasi-exec/sharia_list.json"
EXCHANGE_URL  = "https://www.saudiexchange.sa"
CDP_URL       = "http://127.0.0.1:18801"
LOCK_FILE     = "/home/mino/tasi-exec/screener.lock"

def load_sharia_universe() -> list[str]:
    """Load Sharia-compliant main-market tickers from sharia_list.json."""
    if os.path.exists(SHARIA_FILE):
        try:
            with open(SHARIA_FILE) as f:
                data = json.load(f)
            tickers = data.get("main_market_yahoo_tickers", [])
            if tickers:
                # Filter out known delisted tickers
                filtered = [t for t in tickers if t not in DELISTED_TICKERS]
                skipped = len(tickers) - len(filtered)
                if skipped:
                    log.info(f"Filtered out {skipped} known delisted ticker(s): {set(tickers) & DELISTED_TICKERS}")
                log.info(f"Loaded {len(filtered)} Sharia tickers from {SHARIA_FILE} (fetched {data.get('fetched','?')[:10]})")
                return filtered
        except json.JSONDecodeError as e:
            log.error(f"Failed to parse {SHARIA_FILE}: {e}")
            # Try to load from backup or cache if available
            # For now, return empty list and let the system handle the refresh
        except Exception as e:
            log.error(f"Error loading {SHARIA_FILE}: {e}")
    log.warning(f"{SHARIA_FILE} missing or empty — run refresh_sharia_list() or check the file")
    return []

async def _refresh_sharia_list_async():
    """Scrape the Saudi Exchange Sharia-compliant page and update sharia_list.json."""
    import re
    from datetime import datetime as dt
    log.info("Refreshing Sharia list from Saudi Exchange...")
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=5000)
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        await page.goto(
            f"{EXCHANGE_URL}/wps/portal/saudiexchange/trading/participants-and-securities/securities/sharia-compliant-securities",
            wait_until="domcontentloaded", timeout=12000
        )
        await page.wait_for_timeout(6000)
        company_links = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('a[href*="company-profile"]'))
                .map(el => ({name: el.innerText.trim(), href: el.href}))
                .filter(l => l.name.length > 1)
        """)
        await page.close()

        # Fetch full stock list to filter to main market only
        headers = {"User-Agent": "Mozilla/5.0", "Referer": EXCHANGE_URL}
        all_stocks = requests.get(
            f"{EXCHANGE_URL}/tadawul.eportal.theme.helper/ThemeSearchUtilityServlet",
            headers=headers, timeout=10
        ).json()
        main_market_codes = {s["symbol"] for s in all_stocks if s.get("market_type") == "M"}

        stocks = []
        for l in company_links:
            m = re.search(r'[?&]companySymbol=(\d+)', l["href"])
            if m:
                code = m.group(1)
                stocks.append({"symbol": code, "name": l["name"], "yahoo": f"{code}.SR"})

        main_tickers = sorted([s["yahoo"] for s in stocks if s["symbol"] in main_market_codes])
        output = {
            "source": "Saudi Exchange Sharia-Compliant Securities",
            "fetched": dt.now().isoformat(),
            "total": len(stocks),
            "main_market_count": len(main_tickers),
            "stocks": sorted(stocks, key=lambda x: x["symbol"]),
            "main_market_yahoo_tickers": main_tickers,
        }
        with open(SHARIA_FILE, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        log.info(f"Sharia list refreshed: {len(main_tickers)} main-market stocks saved to {SHARIA_FILE}")
        return main_tickers
    except Exception as e:
        log.error(f"Sharia list refresh failed: {e}")
        return []

def refresh_sharia_list() -> list[str]:
    return asyncio.run(_refresh_sharia_list_async())

MIN_AVG_VOLUME       = 500_000   # minimum 20-day avg volume (shares)
MIN_PRICE            = 5.0       # SAR (v4.1: lowered from 10.0)
MAX_PRICE            = 500.0     # SAR
MIN_VOLUME_EXCEPTION = 50_000    # v4.1: for score >= 80
HIGH_SCORE_THRESHOLD = 80      # v4.1: volume exception threshold
TOP_N                = 5         # Output top 5 picks (primary #1-2, fallback #3-5)

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

# ─── Telegram helpers ─────────────────────────────────────────────────────────

def tg_send(text: str):
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        log.warning("Telegram bot token missing — message not sent.")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        if resp.status_code != 200:
            log.warning(f"Telegram sendMessage failed ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        log.warning(f"Telegram sendMessage exception: {e}")


def _to_native(obj):
    """
    Recursively convert numpy/pandas scalar types to native Python types.

    Bug context: screener crashed silently for 16+ days with
    `TypeError: Object of type bool is not JSON serializable` because
    some pick fields (e.g. rsi thresholds, near_breakout flags) were
    numpy.bool_ / numpy.int64 / numpy.float64 instead of Python types.
    Fixing at the source so the JSON writer never sees non-serializable
    values. Added 2026-06-10.
    """
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(i) for i in obj]
    # numpy scalar (bool_, int64, float64, etc.) has .item() returning the python type
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            return obj.item()
        except (ValueError, AttributeError):
            return obj
    return obj


def _safe_json_dump(data, fp, indent=2):
    """
    json.dump with structured error reporting. If a TypeError still leaks
    through, log the offending key path and re-raise so the parent
    try/except can decide whether to fall back to default=str.
    """
    try:
        json.dump(data, fp, indent=indent)
    except TypeError as e:
        # Walk the dict to find the first non-serializable leaf
        def _find_bad(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield from _find_bad(v, f"{path}.{k}" if path else str(k))
            elif isinstance(obj, (list, tuple)):
                for i, v in enumerate(obj):
                    yield from _find_bad(v, f"{path}[{i}]")
            else:
                try:
                    json.dumps(obj)
                except (TypeError, ValueError):
                    yield (path, type(obj).__name__, repr(obj)[:80])
        first_bad = next(_find_bad(data), None)
        if first_bad:
            path, tname, preview = first_bad
            log.error(
                f"JSON serialization failed at key path '{path}' "
                f"(type={tname}, value={preview}). Falling back to default=str."
            )
        raise


def tg_photo(buf: io.BytesIO, caption: str):
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        log.warning("Telegram bot token missing — photo not sent.")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        buf.seek(0)
        resp = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                      files={"photo": ("chart.png", buf, "image/png")}, timeout=15)
        if resp.status_code != 200:
            log.warning(f"Telegram sendPhoto failed ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        log.warning(f"Telegram sendPhoto exception: {e}")

# ─── Pre-Market Momentum Filter ─────────────────────────────────────────────

def check_premarket_momentum(ticker: str, mode: str = "premarket") -> dict | None:
    """
    Check momentum based on mode:
    - premarket: yesterday's full session (before market open)
    - midscreen1: today's 10:00-10:30 session (early momentum)
    - midscreen2: today's 11:00-12:00 session (last hour momentum)
    - rescreen: today's 12:00-13:30 session (pre-cutoff check)
    """
    if ticker in DELISTED_TICKERS:
        log.debug(f"{ticker}: skipped — known delisted")
        return None

    try:
        # Try intraday first; fallback to daily if Yahoo doesn't serve TADAWUL intraday
        df = yf.download(ticker, period="7d", interval="1m", progress=False, auto_adjust=True)
        use_intraday = True
        if df is None or len(df) < 30:
            # Fallback to daily
            df = yf.download(ticker, period="7d", progress=False, auto_adjust=True)
            use_intraday = False
        if df is None or len(df) < 2:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna()

        today = datetime.now().date()
        unique_dates = sorted(set(df.index.date))

        if mode == "premarket":
            # Use yesterday's full session
            if len(unique_dates) < 2:
                return None
            target_day = unique_dates[-2]
            df_target = df[df.index.date == target_day]
            if use_intraday and len(df_target) < 50:
                return None
            elif not use_intraday:
                # Daily data: just use the single row for target day
                if len(df_target) < 1:
                    return None
        elif mode in ("midscreen1", "midscreen2", "rescreen"):
            # Use today's session (must have data)
            df_today = df[df.index.date == pd.Timestamp(today).date()]
            if use_intraday and len(df_today) < 20:
                return None
            elif not use_intraday:
                if len(df_today) < 1:
                    return None

            if not use_intraday:
                # Daily fallback: can't slice by time, use full day
                df_target = df_today
            elif mode == "midscreen1":
                # First 30 min of session (10:00-10:30)
                df_target = df_today.iloc[:30]
            elif mode == "midscreen2":
                # Last hour before 12:00 (11:00-12:00)
                # Find bars from 11:00 onwards
                df_target = df_today[df_today.index.time >= pd.Timestamp("11:00").time()]
                if len(df_target) < 20:
                    return None
            elif mode == "rescreen":
                # Last 90 min before 13:30 (12:00-13:30)
                df_target = df_today[df_today.index.time >= pd.Timestamp("12:00").time()]
                if len(df_target) < 30:
                    return None
        else:
            return None

        # Session metrics
        session_open = float(df_target['Open'].iloc[0])
        session_close = float(df_target['Close'].iloc[-1])
        session_high = float(df_target['High'].max())
        session_low = float(df_target['Low'].min())

        # 1. Session change %
        session_change = (session_close - session_open) / session_open * 100

        # 2. Max intraday move %
        max_intraday = (session_high - session_open) / session_open * 100

        # 3. ATR(10) — skip if using daily fallback (not enough bars)
        if use_intraday and len(df_target) >= 10:
            df_target['tr1'] = df_target['High'] - df_target['Low']
            df_target['tr2'] = abs(df_target['High'] - df_target['Close'].shift(1))
            df_target['tr3'] = abs(df_target['Low'] - df_target['Close'].shift(1))
            df_target['tr'] = df_target[['tr1','tr2','tr3']].max(axis=1)
            df_target['atr_10'] = df_target['tr'].rolling(10).mean()
            atr = float(df_target['atr_10'].iloc[-1])
        else:
            atr = session_high - session_low  # Use range as proxy for daily fallback

        # 4. Volume ratio vs 20-day average
        try:
            daily_df = yf.download(ticker, period="30d", interval="1d", progress=False, auto_adjust=True)
            if daily_df is not None and len(daily_df) >= 20:
                daily_df.columns = [c[0] if isinstance(c, tuple) else c for c in daily_df.columns]
                vol_20d = daily_df['Volume'].rolling(20).mean().iloc[-2]
                vol_target = daily_df['Volume'].iloc[-2] if mode == "premarket" else daily_df['Volume'].iloc[-1]
                vol_ratio = vol_target / vol_20d if vol_20d > 0 else 0
            else:
                vol_ratio = 0
        except:
            vol_ratio = 0

        # 5. Range ratio
        try:
            daily_df['range'] = daily_df['High'] - daily_df['Low']
            avg_range_10d = daily_df['range'].rolling(10).mean().iloc[-2]
            target_range = daily_df['range'].iloc[-2] if mode == "premarket" else daily_df['range'].iloc[-1]
            range_ratio = target_range / avg_range_10d if avg_range_10d > 0 else 0
        except:
            range_ratio = 0

        # Thresholds by mode
        if mode == "premarket":
            ATR_MIN, VOL_MIN, MOVE_MIN, RANGE_MIN = 0.01, 0.3, 0.5, 0.5
        elif mode == "midscreen1":
            ATR_MIN, VOL_MIN, MOVE_MIN, RANGE_MIN = 0.01, 0.5, 0.3, 0.3
        elif mode == "midscreen2":
            ATR_MIN, VOL_MIN, MOVE_MIN, RANGE_MIN = 0.02, 0.8, 1.0, 0.5
        elif mode == "rescreen":
            ATR_MIN, VOL_MIN, MOVE_MIN, RANGE_MIN = 0.02, 0.5, 0.5, 0.5
        else:
            ATR_MIN, VOL_MIN, MOVE_MIN, RANGE_MIN = 0.01, 0.3, 0.5, 0.5

        passed = True
        fail_reasons = []
        if atr < ATR_MIN:
            passed = False
            fail_reasons.append(f"ATR={atr:.3f}<{ATR_MIN}")
        if vol_ratio < VOL_MIN:
            passed = False
            fail_reasons.append(f"VOL={vol_ratio:.1f}<{VOL_MIN}")
        if max_intraday < MOVE_MIN:
            passed = False
            fail_reasons.append(f"MOVE={max_intraday:.2f}%<{MOVE_MIN}%")
        if range_ratio < RANGE_MIN:
            passed = False
            fail_reasons.append(f"RANGE={range_ratio:.2f}<{RANGE_MIN}")

        metrics = {
            "atr": round(atr, 3),
            "vol_ratio": round(vol_ratio, 1),
            "session_change": round(session_change, 2),
            "max_intraday": round(max_intraday, 2),
            "range_ratio": round(range_ratio, 2),
            "passed": passed,
            "fail_reasons": fail_reasons,
            "mode": mode,
            "date_checked": str(target_day if mode == "premarket" else today),
        }

        return metrics
    except Exception as e:
        log.debug(f"{ticker} momentum check error [{mode}]: {e}")
        return None


# ─── Analysis ────────────────────────────────────────────────────────────────

def score_stock(ticker: str, mode: str = "premarket") -> dict | None:
    """
    Score a stock for picking. Mode affects momentum filter time window.
    """
    if ticker in DELISTED_TICKERS:
        log.debug(f"{ticker}: skipped — known delisted")
        return None

    try:
        df = yf.download(ticker, period="30d", interval="1d", progress=False, auto_adjust=True)
        if df is None or len(df) < 10:
            return None

        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna()

        close  = df["Close"].iloc[-1]
        vol20  = df["Volume"].rolling(20).mean().iloc[-1]
        vol1   = df["Volume"].iloc[-1]

        # v4.1: Price filter (lowered to 5 SAR)
        if close < MIN_PRICE or close > MAX_PRICE:
            return None
        
        # v4.1: Volume filter with exception for high scores
        # (will check after scoring)

        # RSI filter — skip overbought
        rsi = ta.rsi(df["Close"], length=14)
        if rsi is None or rsi.iloc[-1] > 70:
            return None
        rsi_val = rsi.iloc[-1]

        # Momentum: close vs 10-day SMA
        sma10 = df["Close"].rolling(10).mean().iloc[-1]
        momentum = (close - sma10) / sma10 * 100

        # Volume surge vs 20-day avg
        vol_ratio = vol1 / vol20 if vol20 > 0 else 0

        # S/R proximity: prior day high is resistance, prior day low is support
        prev_high = df["High"].iloc[-2]
        prev_low  = df["Low"].iloc[-2]

        # 10-day resistance (excluding today)
        resistance_10d = df["High"].iloc[-11:-1].max()
        dist_to_high = (resistance_10d - close) / close * 100

        dist_to_low  = (close - prev_low) / close * 100

        # 20-day high breakout candidate
        high20 = df["High"].rolling(20).max().iloc[-2]
        near_breakout = close > high20 * 0.98

        # Trend
        closes_10 = df["Close"].iloc[-10:].values
        x = np.arange(len(closes_10))
        slope = np.polyfit(x, closes_10, 1)[0]
        trend_pct = slope / closes_10[0] * 100

        # Higher lows
        lows_3 = df["Low"].iloc[-3:].values
        higher_lows = bool(lows_3[1] > lows_3[0] and lows_3[2] > lows_3[1])

        # Composite score
        score = 0
        score += min(momentum, 5) * 10
        score += min(vol_ratio, 3) * 15
        score += (5 - min(dist_to_high, 5)) * 5
        score += 20 if near_breakout else 0
        score -= max(rsi_val - 60, 0) * 2
        score += min(max(trend_pct * 5, -20), 15)
        score += 10 if higher_lows else 0

        # v4.1: Volume check with exception for high scores
        if vol20 < MIN_AVG_VOLUME:
            if score < HIGH_SCORE_THRESHOLD or vol20 < MIN_VOLUME_EXCEPTION:
                return None

        # v4.1: Calculate premarket gap from yesterday's close to today's open
        premarket_change = None
        if len(df) >= 2 and 'Open' in df.columns:
            yesterday_close = float(df["Close"].iloc[-2])
            today_open = float(df["Open"].iloc[-1])
            if today_open > 0 and yesterday_close > 0:
                premarket_change = round((today_open - yesterday_close) / yesterday_close * 100, 2)
                log.info(f"{ticker}: premarket gap {premarket_change:.1f}%")

        # v4.1: Entry zone with gap detection and market order support
        yesterday_range = prev_high - prev_low
        
        # Check for premarket/expected gap
        gap_pct = premarket_change  # Now properly calculated above
        
        if gap_pct is not None:
            if gap_pct > 2.0 and score >= 120:
                # v4.1: Market order for high-confidence gap-up
                entry_low = round(close, 2)
                entry_high = round(close, 2)
                stop_loss = round(close * 0.93, 2)
                order_note = f"MARKET ORDER: Gap-up {gap_pct:.1f}%, score {score}"
            elif gap_pct < -2.0:
                if gap_pct < -3.0 and score < 120:
                    return {"blocked": True, "reason": f"Gap-down {gap_pct:.1f}% too deep, score {score}"}
                # v4.1: Lower entry for gap-down
                entry_low = round(close * 0.95, 2)
                entry_high = round(close * 0.98, 2)
                stop_loss = round(close * 0.93, 2)
                order_note = f"Adjusted entry: Gap-down {gap_pct:.1f}%"
            else:
                # Normal (wider than v4.0)
                if close >= prev_high * 0.99:
                    entry_low = round(min(prev_high * 0.995, close * 0.98), 2)
                    entry_high = round(close * 1.01, 2)
                else:
                    entry_low = round(prev_low * 0.998, 2)
                    entry_high = round(prev_high * 1.002, 2)
                stop_loss = round(close * 0.93, 2)
                order_note = None
        else:
            # Normal logic (wider than v4.0)
            if close >= prev_high * 0.99:
                entry_low = round(min(prev_high * 0.995, close * 0.98), 2)
                entry_high = round(close * 1.01, 2)
            else:
                entry_low = round(prev_low * 0.998, 2)
                entry_high = round(prev_high * 1.002, 2)
            stop_loss = round(close * 0.93, 2)
            order_note = None

        # v4.11: WebSocket metrics validation (pre-market only if WS data available)
        ws_metrics = get_ws_metrics(ticker.replace(".SR", ""))
        if ws_metrics:
            trades_5min = ws_metrics.get("trades_5min", 999)
            liquidity_ratio = ws_metrics.get("liquidity_ratio", 1.0)
            spread_pct = ws_metrics.get("spread_pct", 0.0)
            
            if trades_5min < 5:
                log.info(f"{ticker} REJECTED - insufficient market activity (trades_5min={trades_5min})")
                return {"blocked": True, "reason": f"Insufficient trades: {trades_5min} in 5min"}
            
            if liquidity_ratio < 0.8:
                log.info(f"{ticker} REJECTED - selling pressure dominant (liq_ratio={liquidity_ratio:.2f})")
                return {"blocked": True, "reason": f"Selling pressure: liq={liquidity_ratio:.2f}"}
            
            if spread_pct > 2.0:
                log.info(f"{ticker} REJECTED - poor execution quality (spread={spread_pct:.2f}%)")
                return {"blocked": True, "reason": f"Wide spread: {spread_pct:.2f}%"}
            
            log.info(f"{ticker} WS validated: trades={trades_5min}, liq={liquidity_ratio:.2f}, spread={spread_pct:.2f}%")

        # Pre-market momentum filter (mode-aware)
        pm = check_premarket_momentum(ticker, mode=mode)
        if pm is not None and not pm.get("passed", False):
            reasons = ", ".join(pm.get("fail_reasons", []))
            log.info(f"{ticker} BLOCKED by momentum filter [{mode}]: {reasons}")
            return {"blocked": True, "pm_metrics": pm}

        return {
            "ticker":        ticker,
            "close":         round(close, 2),
            "rsi":           round(rsi_val, 1),
            "momentum":      round(momentum, 2),
            "vol_ratio":     round(vol_ratio, 2),
            "near_breakout": near_breakout,
            "entry_low":     entry_low,
            "entry_high":    entry_high,
            "stop_loss":     stop_loss,
            "score":         round(score, 1),
            "trend_pct":     round(trend_pct, 2),
            "higher_lows":   higher_lows,
            "resistance_10d": round(resistance_10d, 2),
            "df":            df,
            "pm_metrics":    pm if pm else {},
        }
    except Exception as e:
        log.debug(f"{ticker} error: {e}")
        return None


def make_chart(result: dict) -> io.BytesIO:
    # Defensive: validate required keys before charting
    if not isinstance(result, dict) or result.get("df") is None:
        raise ValueError("make_chart requires a result dict with 'df' key")
    df = result["df"].tail(15).copy()
    df.index = pd.DatetimeIndex(df.index)

    buf = io.BytesIO()
    mpf.plot(
        df,
        type="candle",
        style="charles",
        title=f"{result.get('ticker', '???')}  RSI={result.get('rsi', 'N/A')}  Vol×{result.get('vol_ratio', 'N/A')}",
        volume=True,
        savefig=dict(fname=buf, dpi=120, bbox_inches="tight"),
    )
    return buf


def format_pick(rank: int, r: dict) -> str:
    # Defensive: skip malformed results that lack ticker
    if not isinstance(r, dict) or r.get("ticker") is None:
        log.warning(f"format_pick received malformed pick (no ticker): {r}")
        return f"<b>#{rank} ???</b>\n⚠️ Malformed pick — missing ticker"

    def _v(key, default="N/A"):
        val = r.get(key)
        return default if val is None else val

    breakout_tag = "🚀 near 20d breakout" if r.get("near_breakout") else ""
    trend_val = r.get("trend_pct", 0) or 0
    trend_sign = "+" if trend_val >= 0 else ""
    hl_icon = "✅" if r.get("higher_lows") else "❌"
    prob_line = ""
    if r.get("win_rate") is not None:
        ev_val = r.get("ev_pct", 0) or 0
        ev_sign = "+" if ev_val >= 0 else ""
        prob_line = (
            f"\n🎲 P(win): {r.get('win_rate', 0):.0f}% (n={r.get('n_samples', 0)}) | "
            f"EV: {ev_sign}{ev_val:.1f}%"
        )
    return (
        f"<b>#{rank} {_v('ticker', '???')}</b> {breakout_tag}\n"
        f"Close: {_v('close')} SAR  |  RSI: {_v('rsi')}  |  Vol: ×{_v('vol_ratio')}\n"
        f"📈 Trend: {trend_sign}{trend_val}%/day | Higher lows: {hl_icon}\n"
        f"📍 Entry: {_v('entry_low')}–{_v('entry_high')} SAR  |  🛑 Stop: {_v('stop_loss')} SAR (−7%)"
        f"{prob_line}\n"
        f"Score: {_v('score')}"
    )

# ─── Main ─────────────────────────────────────────────────────────────────────

def get_ws_metrics(symbol):
    """
    v4.11: Read WebSocket metrics from ws_prices jsonl file.
    Returns dict with trades_5min, liquidity_ratio, spread_pct.
    """
    from datetime import date
    date_str = date.today().isoformat()
    ws_file = Path("/home/mino/tasi-exec") / f"ws_prices_{date_str}.jsonl"
    
    if not ws_file.exists():
        return {}
    
    metrics = {
        "trades_5min": 0,
        "liquidity_ratio": 1.0,
        "spread_pct": 0.0,
        "net_flow": 0.0,
    }
    
    cutoff = datetime.now().timestamp() - 300  # Last 5 minutes
    
    try:
        with open(ws_file) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("symbol") == symbol and entry.get("ts", 0) > cutoff:
                        metrics["trades_5min"] += 1
                        metrics["liquidity_ratio"] = entry.get("liquidity_ratio", 1.0)
                        metrics["spread_pct"] = entry.get("spread_pct", 0.0)
                        metrics["net_flow"] = entry.get("net_flow", 0.0)
                except:
                    continue
    except Exception as e:
        log.debug(f"get_ws_metrics {symbol}: {e}")
    
    return metrics


# ─── Main ─────────────────────────────────────────────────────────────────────

def acquire_lock() -> bool:
    """Try to acquire a file lock to prevent duplicate cron runs."""
    import fcntl
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep fd open for duration of process — lock released on exit
        return True
    except (IOError, OSError):
        return False

def run_screener(mode: str = "premarket", picks_file: str = PICKS_FILE, limit: int = 50) -> dict:
    """
    Run screener in different modes:
    - premarket: 09:50 — yesterday momentum filter + full scoring
    - midscreen1: 10:30 — today's early momentum scan
    - midscreen2: 12:00 — last hour momentum scan
    - rescreen: 13:30 — quick rescreen on picks only
    Returns picks_data dict.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    log.info(f"Screener [{mode}] started — {now}")

    universe = load_sharia_universe()
    if not universe:
        tg_send("⚠️ Screener: Sharia list empty — run refresh or check sharia_list.json")
        return None

    tg_send(f"🔍 <b>{mode.upper()} scan</b> — {now}\nScanning {len(universe)} stocks…")

    results = []
    blocked_stocks = []
    scanned = 0

    for ticker in universe[:limit]:
        scanned += 1
        r = score_stock(ticker, mode=mode)
        if r and not r.get("blocked"):
            results.append(r)
        elif r and r.get("blocked"):
            # Blocked by momentum filter — log it
            pm = r.get("pm_metrics", {})
            if pm and not pm.get("passed", False):
                blocked_stocks.append({
                    "symbol": ticker,
                    "pm_metrics": pm,
                    "timestamp": datetime.now().isoformat()
                })
        else:
            # score_stock returned None (not blocked, just didn't qualify)
            pass

        # ── Early Abort: Detect yfinance data quality issue fast ──
        # If first 30 stocks all blocked with VOL=0, abort and use WebSocket fallback
        if scanned >= 30 and len(blocked_stocks) == scanned:
            zero_vol_early = sum(1 for b in blocked_stocks if b.get("pm_metrics", {}).get("vol", 0) == 0)
            if zero_vol_early >= scanned * 0.9:
                log.error(f"[{mode}] EARLY ABORT: {zero_vol_early}/{scanned} stocks blocked with VOL=0 — yfinance stale data")
                tg_send(
                    f"⚠️ <b>Data Quality Alert</b>\n"
                    f"Screener blocked {zero_vol_early}/{scanned} stocks with zero volume.\n"
                    f"yfinance data stale — switching to WebSocket fallback..."
                )
                # Try WebSocket fallback for picks
                ws_picks = _websocket_fallback_picks()
                if ws_picks:
                    log.info(f"[{mode}] WebSocket fallback generated {len(ws_picks)} picks")
                    return ws_picks
                log.error(f"[{mode}] WebSocket fallback also failed — no picks generated")
                return None

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:TOP_N]

    blocked_count = len(blocked_stocks)
    if blocked_count > 0:
        log.info(f"[{mode}] Momentum filter blocked {blocked_count}/{scanned} stocks")

    # ── Data Quality Check (full scan complete) ─────────────────────────────
    # Detect if yfinance returned bad data (common on Sunday 09:50 Riyadh)
    zero_vol_count = sum(1 for b in blocked_stocks if b.get("pm_metrics", {}).get("vol", 0) == 0)
    if blocked_count > scanned * 0.9 and zero_vol_count > blocked_count * 0.8:
        log.error(f"[{mode}] DATA QUALITY ISSUE: {zero_vol_count}/{blocked_count} blocked with VOL=0")
        tg_send(
            f"⚠️ <b>Data Quality Alert</b>\n"
            f"Screener blocked {blocked_count}/{scanned} stocks with zero volume.\n"
            f"Likely yfinance delay — switching to WebSocket fallback..."
        )
        # Try WebSocket fallback for picks
        ws_picks = _websocket_fallback_picks()
        if ws_picks:
            log.info(f"[{mode}] WebSocket fallback generated {len(ws_picks)} picks")
            return ws_picks
        log.error(f"[{mode}] WebSocket fallback also failed")
        return None

    try:
        from screener_prob import run_prob_scan
        top = run_prob_scan(top)
    except Exception as e:
        log.warning(f"[{mode}] Prob scan skipped: {e}")

    if not top:
        msg = f"⚠️ {mode.upper()} screener found no qualifying stocks."
        if blocked_count > 0:
            msg += f"\nMomentum filter blocked {blocked_count} stocks."
        tg_send(msg)
        return None

    picks_data = {
        "date": datetime.now().date().isoformat(),
        "mode": mode,
        "picks": [
            {
                "ticker":        r.get("ticker", "???"),
                "entry_low":     r.get("entry_low"),
                "entry_high":    r.get("entry_high"),
                "stop_loss":     r.get("stop_loss"),
                "score":         r.get("score"),
                "rsi":           r.get("rsi"),
                "vol_ratio":     r.get("vol_ratio"),
                "close":         r.get("close"),
                "trend_pct":     r.get("trend_pct"),
                "higher_lows":   r.get("higher_lows"),
                "near_breakout": r.get("near_breakout"),
                "win_rate":      r.get("win_rate"),
                "ev_pct":        r.get("ev_pct"),
                "n_samples":     r.get("n_samples"),
                "pm_metrics":    r.get("pm_metrics", {}),
            }
            for r in top
        ],
    }

    # Archive picks with timestamp for historical backtesting
    import shutil
    archive_dir = "/home/mino/tasi-exec/archive/picks"
    os.makedirs(archive_dir, exist_ok=True)
    date_str = datetime.now().date().isoformat()
    time_str = datetime.now().strftime("%H%M")
    archive_file = f"{archive_dir}/picks_{date_str}_{time_str}_{mode}.json"

    # v4.1: Atomic write for archive (prevents corruption)
    # Bug fix 2026-06-10: cast numpy types to native python BEFORE json.dump,
    # and fall back to default=str + tg_send alert if serialization still fails
    # (was silently crashing here for 16+ days)
    import tempfile
    picks_data = _to_native(picks_data)
    try:
        tmp = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json', dir=archive_dir)
        _safe_json_dump(picks_data, tmp)
        tmp.close()
        os.rename(tmp.name, archive_file)
        log.info(f"[{mode}] Archived picks to {archive_file}")
    except TypeError as e:
        log.error(f"[{mode}] Archive write still failed after _to_native: {e}")
        try:
            tmp = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json', dir=archive_dir)
            json.dump(picks_data, tmp, indent=2, default=str)
            tmp.close()
            os.rename(tmp.name, archive_file)
            log.warning(f"[{mode}] Archived picks via default=str fallback")
        except Exception as e2:
            tg_send(f"⚠️ Screener [{mode}] failed to write archive: {e2}")
            raise

    # v4.1: Atomic write for picks file
    try:
        tmp2 = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json', dir=os.path.dirname(picks_file))
        _safe_json_dump(picks_data, tmp2)
        tmp2.close()
        os.rename(tmp2.name, picks_file)
        log.info(f"[{mode}] Wrote {len(top)} picks to {picks_file}")
    except TypeError as e:
        log.error(f"[{mode}] picks.json write still failed after _to_native: {e}")
        try:
            tmp2 = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json', dir=os.path.dirname(picks_file))
            json.dump(picks_data, tmp2, indent=2, default=str)
            tmp2.close()
            os.rename(tmp2.name, picks_file)
            log.warning(f"[{mode}] Wrote picks via default=str fallback")
        except Exception as e2:
            tg_send(f"⚠️ Screener [{mode}] CRITICAL: failed to write {picks_file}: {e2}")
            raise

    return picks_data


def _websocket_fallback_picks():
    """
    Generate picks from live WebSocket data when yfinance returns stale/zero data.
    Parses ws_frames_raw.log for stocks with the biggest moves.
    """
    import time
    ws_log = "/home/mino/tasi-exec/ws_frames_raw.log"
    if not os.path.exists(ws_log):
        log.error("WebSocket fallback: ws_frames_raw.log not found")
        return None
    
    # Check if log has recent data (within last 5 minutes)
    try:
        log_mtime = os.path.getmtime(ws_log)
        mins_ago = (time.time() - log_mtime) / 60
        if mins_ago > 10:
            log.error(f"WebSocket fallback: log is {mins_ago:.0f} min old — too stale")
            return None
    except Exception as e:
        log.error(f"WebSocket fallback: {e}")
        return None
    
    stocks = defaultdict(lambda: {"prices": [], "changes": []})
    
    try:
        with open(ws_log) as f:
            for line in f:
                if "lasttradeprice" not in line or "QO." not in line:
                    continue
                try:
                    json_str = line.split("] ", 1)[1] if "] " in line else line
                    data = json.loads(json_str)
                    topic = data.get("topic", "")
                    if topic.startswith("QO.") and topic.endswith(".TAD"):
                        code = topic.replace("QO.", "").replace(".TAD", "")
                        # Only individual stocks (4 digits)
                        if len(code) == 4 and code.isdigit() and code[0] in "123456789":
                            symbol = code + ".SR"
                            price = float(data.get("lasttradeprice", 0))
                            change = float(data.get("change", 0))
                            if price > 0:
                                stocks[symbol]["prices"].append(price)
                                stocks[symbol]["changes"].append(change)
                except:
                    pass
    except Exception as e:
        log.error(f"WebSocket fallback parse error: {e}")
        return None
    
    if not stocks:
        log.error("WebSocket fallback: no stocks parsed from log")
        return None
    
    results = []
    for symbol, data in stocks.items():
        if len(data["prices"]) >= 5:
            avg_change = sum(data["changes"]) / len(data["changes"])
            max_price = max(data["prices"])
            min_price = min(data["prices"])
            range_pct = ((max_price - min_price) / min_price) * 100 if min_price > 0 else 0
            score = abs(avg_change) * 10 + range_pct
            results.append({
                "ticker": symbol,
                "close": data["prices"][-1],
                "entry_low": min_price,
                "entry_high": max_price,
                "score": round(score, 1),
                "rsi": 50.0,  # Unknown from WS data
                "vol_ratio": 1.0,  # Unknown from WS data
                "momentum": avg_change,
                "trend_pct": avg_change,
                "higher_lows": False,
                "near_breakout": False,
                "pm_metrics": {
                    "change_pct": round(avg_change, 2),
                    "range_pct": round(range_pct, 2),
                    "ticks": len(data["prices"])
                }
            })
    
    if not results:
        log.error("WebSocket fallback: no stocks with enough ticks")
        return None
    
    results.sort(key=lambda x: x["score"], reverse=True)
    top5 = results[:5]
    
    picks_data = {
        "date": datetime.now().date().isoformat(),
        "mode": "emergency_ws",
        "window": datetime.now().strftime("%H:%M") + " live",
        "picks": [
            {
                "ticker": r["ticker"],
                "close": r["close"],
                "entry_low": r["entry_low"],
                "entry_high": r["entry_high"],
                "score": r["score"],
                "rsi": r["rsi"],
                "vol_ratio": r["vol_ratio"],
                "trend_pct": r["trend_pct"],
                "higher_lows": r["higher_lows"],
                "near_breakout": r["near_breakout"],
                "pm_metrics": r["pm_metrics"],
            }
            for r in top5
        ],
    }
    
    # Save to picks.json
    with open(PICKS_FILE, "w") as f:
        json.dump(picks_data, f, indent=2)
    log.info(f"WebSocket fallback: wrote {len(top5)} picks to {PICKS_FILE}")
    
    return picks_data


def main():
    """Entry point for premarket screener cron."""
    # Prevent duplicate cron runs
    if not acquire_lock():
        log.warning("Screener already running — skipping duplicate invocation")
        return

    picks_data = run_screener(mode="premarket", picks_file=PICKS_FILE, limit=269)
    if not picks_data:
        return

    top = picks_data["picks"]
    blocked_count = 0

    tg_send(f"✅ Scan complete — showing top {len(top)}:\n(Momentum filter blocked {blocked_count} stocks)")

    for i, r in enumerate(top, 1):
        # Defensive: skip any pick that lost its ticker key between save and send
        if not isinstance(r, dict) or r.get("ticker") is None:
            log.warning(f"Skipping malformed pick #{i}: {r}")
            continue
        caption = format_pick(i, r)
        # Skip chart for saved picks (no df); just send text
        tg_send(caption)

    tg_send(
        "⏰ <b>Reminders:</b>\n"
        "• Verify Sharia compliance on Argaam before buying\n"
        "• Hard close at 14:45 Riyadh — no exceptions\n"
        "• Max 40% per position (400 SAR at 1k capital)\n"
        "• Stop loss: −7% from entry, no averaging down"
    )
    try:
        from market_regime import classify_premarket
        regime = classify_premarket()
        params = regime["params"]
        icon = {"TRENDING": "🟢", "NEUTRAL": "🟡", "DEFENSIVE": "🔴"}.get(regime["regime"], "⚪")
        msg = (
            f"📊 <b>Market Regime: {regime['regime']} {icon}</b>\n"
            f"Strategy: {params['strategy']} | Cycles: {params['max_cycles']} | Size: {int(params['position_pct']*100)}%\n"
            f"{regime['reason']}"
        )
        tg_send(msg)
    except Exception as e:
        log.warning(f"Regime classification failed: {e}")

    log.info("Screener done.")


if __name__ == "__main__":
    main()

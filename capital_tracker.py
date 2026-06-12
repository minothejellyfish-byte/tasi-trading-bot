#!/usr/bin/env python3
"""
Capital Tracker for TASI Trading System

Updates and tracks available trading capital from Derayah.
Called by post-market analysis to update capital after trading day.
"""

import json
import os
import asyncio
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

BASE_DIR = Path("/home/mino/tasi-exec")
CAPITAL_FILE = BASE_DIR / "capital.json"
POSITIONS_FILE = BASE_DIR / "positions.json"

# Config
CDP_URL = "http://127.0.0.1:18801"
DERAYAH_TRADE = "https://newonline.derayah.com/#/layout/trading-portfolio"

# Fallback default capital
DEFAULT_CAPITAL = 1000.0


def load_capital() -> float:
    """Load current available capital."""
    if CAPITAL_FILE.exists():
        try:
            with open(CAPITAL_FILE) as f:
                data = json.load(f)
            return float(data.get("available_capital", DEFAULT_CAPITAL))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            print(f"[WARNING] Failed to load capital.json: {e}")
    return DEFAULT_CAPITAL


def save_capital(capital: float, source: str = "manual"):
    """Save capital with metadata."""
    data = {
        "available_capital": float(capital),
        "updated_at": datetime.now().isoformat(),
        "source": source,
    }
    with open(CAPITAL_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[INFO] Capital updated: {capital:.2f} SAR ({source})")


def save_capital_full(available: float, grand_total: float = None,
                      securities_value: float = None, money_transfer: float = None,
                      fees: float = None, source: str = "manual"):
    """Save capital with ALL fields preserved/updated."""
    # Load existing to preserve fields we don't update
    existing = {}
    if CAPITAL_FILE.exists():
        try:
            with open(CAPITAL_FILE) as f:
                existing = json.load(f)
        except Exception:
            pass

    data = {
        "available_capital": float(available),
        "updated_at": datetime.now().isoformat(),
        "source": source,
        # Preserve or update other fields
        "initial_capital": existing.get("initial_capital", 1000.66),
        "grand_total": float(grand_total) if grand_total is not None else existing.get("grand_total", 1000.66),
        "securities_value": float(securities_value) if securities_value is not None else existing.get("securities_value", 0),
        "money_transfer": float(money_transfer) if money_transfer is not None else existing.get("money_transfer", available),
        "total_fees": float(fees) if fees is not None else existing.get("total_fees", 0),
        "account": existing.get("account", "001LOC-SAR TDWL"),
    }
    with open(CAPITAL_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[INFO] Capital full update: available={available:.2f}, grand_total={data['grand_total']:.2f}, securities={data['securities_value']:.2f} ({source})")


def calculate_capital_from_positions() -> float:
    """
    Calculate available capital from closed positions P&L.
    This is a fallback when Derayah API is unavailable.
    """
    if not POSITIONS_FILE.exists():
        return DEFAULT_CAPITAL
    
    try:
        with open(POSITIONS_FILE) as f:
            positions = json.load(f)
        
        total_pnl = 0.0
        for symbol, pos in positions.items():
            if pos.get("closed"):
                entry = pos.get("entry_price", 0)
                close = pos.get("close_price", 0)
                qty = pos.get("qty", 0)
                if entry and close and qty:
                    pnl = (close - entry) * qty
                    total_pnl += pnl
        
        # Capital = default + total P&L
        return DEFAULT_CAPITAL + total_pnl
    except Exception as e:
        print(f"[WARNING] Failed to calculate from positions: {e}")
        return DEFAULT_CAPITAL


async def _scrape_derayah_cash() -> float | None:
    """
    Scrape cash balance from Derayah trading-portfolio dashboard via CDP.
    Returns available cash in SAR, or None if scraping fails.
    """
    pw = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=5000)
        ctx = browser.contexts[0]
        
        # Find Derayah online tab
        page = None
        for p in ctx.pages:
            if "newonline.derayah.com" in p.url:
                page = p
                break
        
        if not page:
            print("[WARNING] No Derayah tab found for cash scrape")
            return None
        
        await page.bring_to_front()
        await page.goto(DERAYAH_TRADE, wait_until="domcontentloaded", timeout=8000)
        await page.wait_for_timeout(3000)
        
        # Try multiple selectors to find cash balance
        selectors = [
            'text=/Cash/i',
            '[class*=cash]',
            'text=/1,?[0-9,.]+.*SAR/',
        ]
        
        for sel in selectors:
            try:
                els = await page.query_selector_all(sel)
                for el in els:
                    text = await el.text_content()
                    if text:
                        # Look for pattern like "Cash 1,000.66 SAR" or "1,000.66 SAR"
                        match = re.search(r"[Cc]ash\s*([0-9,]+\.?\d*)", text)
                        if match:
                            cash_str = match.group(1).replace(",", "")
                            return float(cash_str)
                        # Also try generic SAR pattern
                        match = re.search(r"([0-9,]+\.?\d*)\s*SAR", text)
                        if match:
                            cash_str = match.group(1).replace(",", "")
                            val = float(cash_str)
                            if val > 0:  # Filter out 0.00 entries
                                return val
            except Exception:
                pass
        
        print("[WARNING] Could not find cash balance on Derayah dashboard")
        return None
        
    except Exception as e:
        print(f"[WARNING] Derayah cash scrape failed: {e}")
        return None
    finally:
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass


def update_capital_from_derayah():
    """
    Fetch available cash from Derayah dashboard.
    Falls back to position calculation if scraping fails.
    """
    # Try scraping from Derayah dashboard first
    try:
        cash = asyncio.run(_scrape_derayah_cash())
        if cash is not None:
            save_capital(cash, source="derayah_dashboard")
            return cash
    except Exception as e:
        print(f"[WARNING] Derayah scrape error: {e}")
    
    # Fallback: calculate from positions
    print("[INFO] Falling back to position-based calculation")
    capital = calculate_capital_from_positions()
    save_capital(capital, source="positions_calc")
    return capital


def update_capital_full_from_derayah():
    """
    Fetch FULL balance (grand_total, securities, available) from Derayah.
    Updates capital.json with all fields.
    """
    try:
        result = asyncio.run(_scrape_derayah_full())
        if result:
            save_capital_full(
                available=result.get("money_transfer", result.get("available", 0)),
                grand_total=result.get("grand_total"),
                securities_value=result.get("securities_value"),
                money_transfer=result.get("money_transfer"),
                fees=result.get("total_fees"),
                source="derayah-full-scrape"
            )
            return result
    except Exception as e:
        print(f"[WARNING] Full Derayah scrape error: {e}")
    return None


async def _scrape_derayah_full() -> dict | None:
    """
    Scrape FULL balance from Derayah trading-portfolio dashboard via CDP.
    Returns dict with grand_total, money_transfer, securities_value, total_cash, total_fees
    """
    pw = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=5000)
        ctx = browser.contexts[0]

        page = None
        for p in ctx.pages:
            if "newonline.derayah.com" in p.url:
                page = p
                break

        if not page:
            print("[WARNING] Derayah page not found for full scrape")
            return None

        # Navigate to portfolio
        try:
            await page.goto(DERAYAH_TRADE, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            print(f"[WARNING] Navigation error: {e}")
        await page.wait_for_timeout(5000)

        # Scrape all values
        text = await page.inner_text("body")
        lines = text.split("\n")

        result = {
            "grand_total": None,
            "money_transfer": None,
            "securities_value": None,
            "total_cash": None,
            "total_fees": None,
        }

        for i, line in enumerate(lines):
            if "Grand Total" in line and i + 1 < len(lines):
                match = re.search(r"([\d,]+\.?\d*)\s*SAR", lines[i + 1])
                if match:
                    result["grand_total"] = float(match.group(1).replace(",", ""))

            if "Money Transfer" in line and i + 1 < len(lines):
                match = re.search(r"([\d,]+\.?\d*)\s*SAR", lines[i + 1])
                if match:
                    result["money_transfer"] = float(match.group(1).replace(",", ""))

            if "Securities Value" in line:
                for offset in [2, 1, -1, -2, 3]:
                    check_idx = i - offset
                    if 0 <= check_idx < len(lines):
                        match = re.search(r"([\d,]+\.?\d*)\s*SAR", lines[check_idx])
                        if match:
                            val = float(match.group(1).replace(",", ""))
                            if val >= 0:
                                result["securities_value"] = val
                                break
                if result["securities_value"] is None and i + 1 < len(lines):
                    match = re.search(r"([\d,]+\.?\d*)\s*SAR", lines[i + 1])
                    if match:
                        val = float(match.group(1).replace(",", ""))
                        if val >= 0:
                            result["securities_value"] = val

            if "Total Cash" in line and i + 1 < len(lines):
                match = re.search(r"([\d,]+\.?\d*)\s*SAR", lines[i + 1])
                if match:
                    result["total_cash"] = float(match.group(1).replace(",", ""))

            if "Fees" in line and i + 1 < len(lines):
                match = re.search(r"([\d,]+\.?\d*)\s*SAR", lines[i + 1])
                if match:
                    result["total_fees"] = float(match.group(1).replace(",", ""))

        await browser.close()

        # Log what we found
        print(f"[INFO] Full scrape: grand_total={result['grand_total']}, money_transfer={result['money_transfer']}, securities={result['securities_value']}, fees={result['total_fees']}")

        if result["money_transfer"] is not None:
            return result
        return None

    except Exception as e:
        print(f"[WARNING] Full scrape error: {e}")
        return None
    finally:
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass


def main():
    """CLI entry point for post-market update."""
    import sys
    
    if len(sys.argv) > 1:
        # Manual update: python3 capital_tracker.py 1500.50
        try:
            new_capital = float(sys.argv[1])
            save_capital(new_capital, source="manual_entry")
            print(f"Capital manually set to: {new_capital:.2f} SAR")
        except ValueError:
            print("Usage: python3 capital_tracker.py [AMOUNT]")
            sys.exit(1)
    else:
        # Auto-update from Derayah/positions
        capital = update_capital_from_derayah()
        print(f"Capital auto-updated: {capital:.2f} SAR")


if __name__ == "__main__":
    main()

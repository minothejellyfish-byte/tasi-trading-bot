#!/usr/bin/env python3
"""
Market Calendar — TASI (Tadawul) + US (NYSE/Nasdaq) trading days/holidays.
Used by cron guard scripts to skip execution on market holidays.
"""

from datetime import datetime, date
import pytz

RIYADH = pytz.timezone("Asia/Riyadh")
ET = pytz.timezone("America/New_York")

# ─── TASI (Tadawul) Saudi Exchange ─────────────────────────────────────────
# Trading days: Sunday–Thursday
# Holidays: https://www.tadawul.com.sa/wps/portal/tadawul/about-us/faqs
TASI_HOLIDAYS_2026 = {
    date(2026, 2, 17),   # Founding Day (approximate)
    date(2026, 2, 18),   # Founding Day holiday
    date(2026, 3, 22),   # Eid al-Fitr (approximate)
    date(2026, 3, 23),   # Eid al-Fitr holiday
    date(2026, 3, 24),   # Eid al-Fitr holiday
    date(2026, 3, 25),   # Eid al-Fitr holiday
    date(2026, 5, 29),   # Eid al-Adha (approximate)
    date(2026, 5, 30),   # Eid al-Adha holiday
    date(2026, 5, 31),   # Eid al-Adha holiday
    date(2026, 6, 1),    # Eid al-Adha holiday
    date(2026, 9, 23),   # Saudi National Day
}

# Known 2026 half-days (TASI, if any)
TASI_HALF_DAYS_2026 = set()

# ─── US (NYSE/Nasdaq) ──────────────────────────────────────────────────────
# Trading days: Monday–Friday
US_HOLIDAYS_2026 = {
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # Martin Luther King Jr. Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas Day
}

# Known 2026 half-days (US market early closures)
US_HALF_DAYS_2026 = {
    date(2026, 11, 27),  # Day after Thanksgiving
}


def is_tasi_trading_day(dt: datetime = None) -> bool:
    """Return True if dt is a TASI trading day (Sun-Thu, not holiday).
    
    TASI trading week: Sunday=0, Monday=1, Tuesday=2, Wednesday=3, Thursday=4
    Weekend: Friday=5, Saturday=6
    """
    if dt is None:
        dt = datetime.now(RIYADH)
    d = dt.date()
    weekday = d.weekday()
    # Python weekday(): Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    # TASI weekend: Friday (4), Saturday (5) — wait, that's wrong too
    # Actually in Python: Monday=0, Sunday=6
    # TASI trades Sun(6), Mon(0), Tue(1), Wed(2), Thu(3)
    # TASI weekend: Fri(4), Sat(5)
    if weekday in (4, 5):  # Friday, Saturday
        return False
    if d in TASI_HOLIDAYS_2026:
        return False
    return True


def is_us_trading_day(dt: datetime = None) -> bool:
    """Return True if dt is a US trading day (not weekend, not holiday)."""
    if dt is None:
        dt = datetime.now(ET)
    d = dt.date()
    weekday = d.weekday()
    if weekday in (5, 6):  # Saturday, Sunday
        return False
    if d in US_HOLIDAYS_2026:
        return False
    return True


def tasi_market_hours(dt: datetime = None) -> dict:
    """Return TASI market open/close times for the given date."""
    if dt is None:
        dt = datetime.now(RIYADH)
    d = dt.date()
    open_time = RIYADH.localize(datetime.combine(d, __import__('datetime').time(10, 0)))
    close_time = RIYADH.localize(datetime.combine(d, __import__('datetime').time(15, 10)))
    hard_close = RIYADH.localize(datetime.combine(d, __import__('datetime').time(14, 45)))
    entry_cutoff = RIYADH.localize(datetime.combine(d, __import__('datetime').time(13, 30)))
    return {
        "market_open": open_time,
        "market_close": close_time,
        "hard_close": hard_close,
        "entry_cutoff": entry_cutoff,
        "is_trading": is_tasi_trading_day(dt),
    }


def us_market_hours(dt: datetime = None) -> dict:
    """Return US market open/close times for the given date."""
    if dt is None:
        dt = datetime.now(ET)
    d = dt.date()
    open_time = ET.localize(datetime.combine(d, __import__('datetime').time(9, 30)))
    close_time = ET.localize(datetime.combine(d, __import__('datetime').time(16, 0)))
    return {
        "market_open": open_time,
        "market_close": close_time,
        "is_trading": is_us_trading_day(dt),
    }


def today_guard(market: str = "tasi") -> bool:
    """
    CLI helper: print status and exit with code 0 if trading day, 1 if not.
    Usage in cron wrapper:
        python3 /home/mino/tasi-exec/market_calendar.py tasi || exit 0
    """
    now_riyadh = datetime.now(RIYADH)
    now_et = datetime.now(ET)
    if market.lower() == "tasi":
        ok = is_tasi_trading_day(now_riyadh)
        print(f"TASI {now_riyadh.date()}: {'TRADING DAY' if ok else 'HOLIDAY/WEEKEND'}")
        return ok
    elif market.lower() == "us":
        ok = is_us_trading_day(now_et)
        print(f"US {now_et.date()}: {'TRADING DAY' if ok else 'HOLIDAY/WEEKEND'}")
        return ok
    else:
        print(f"Unknown market: {market}. Use 'tasi' or 'us'.")
        return False


if __name__ == "__main__":
    import sys
    market = sys.argv[1] if len(sys.argv) > 1 else "tasi"
    ok = today_guard(market)
    sys.exit(0 if ok else 1)

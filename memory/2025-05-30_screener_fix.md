# TASI Screener Fix — 2025-05-30

## Issue
`AttributeError` in `screener.py` — `Pick` dataclass expected `ticker`, but `picks.json` had stale `symbol` keys.

## Root Cause
Old `.pyc` cache wrote `symbol` instead of `ticker` into `picks.json`. Python loaded stale `.pyc` and crashed on dataclass mismatch.

## Fix Applied
1. Migrated `symbol` → `ticker` in `picks.json`.
2. Refreshed `.pyc` cache at 22:53.
3. Future runs will write `ticker` natively.

## Status
✅ RESOLVED — screener will run clean tomorrow at 09:50.

## Note
Telegram alert failed due to missing bot token in cron session. Expected behavior. If Amin wants cron alerts, add webhook/ping or route via main gateway session.

# Change Proposal: 1-Minute Candle Recovery Score

**Date:** 2026-06-18
**Requester:** A A
**File:** `poller.py`
**Type:** ASK — Performance improvement

---

## 1. Problem Statement

**Current:** Recovery score uses 5-minute candles (`df_pos.tail(5)`)
- **Window:** 5 candles × 5 minutes = **25 minutes**
- **Issue:** Too slow, misses rapid reversals
- **Evidence:** June 14 — 5110 entry at 10:20, 5-min candles missed reversal

**Proposed:** Switch to 1-minute candles
- **Window:** 15 candles × 1 minute = **15 minutes**
- **Benefit:** Faster detection, more accurate in volatile markets

---

## 2. Solution

### A. Build 1-Minute Candles from WebSocket Data

**Source:** `ws_frames_raw.log` (raw tick data)
- Most accurate (actual ticks)
- Real-time (no delay)
- Already available

**Function:** `build_1min_candles(symbol, date_str)`
```python
# Parse ws_frames_raw.log
# Group ticks by minute
# Calculate OHLCV per minute
# Return DataFrame with columns: Open, High, Low, Close, Volume
```

### B. Update Recovery Score Logic

**Current:**
```python
recent_candles = df_pos.tail(5)  # 25-minute window
```

**New:**
```python
# Build 1-min candles from websocket data
candles_1m = build_1min_candles(symbol, date_str)
recent_candles = candles_1m.tail(15)  # 15-minute window
```

### C. Adjust Thresholds

| Parameter | 5-Min (Old) | 1-Min (New) |
|-----------|-------------|-------------|
| Window | 25 min | 15 min |
| Candles | 5 | 15 |
| Recovery threshold | >0.66 | >0.60 (adjusted) |
| Min hold time | 15 min | 10 min (adjusted) |

---

## 3. Implementation Plan

| Step | Action | File |
|------|--------|------|
| 1 | Add `build_1min_candles()` | `poller.py` |
| 2 | Update recovery score to use 1-min | `poller.py` |
| 3 | Test with historical data | `backtest_1min_recovery.py` |
| 4 | Update changelog | `TASI_Changelog.md` |

---

## 4. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Performance impact | Cache candles, rebuild every 5 min |
| False signals | Adjust threshold from 0.66 to 0.60 |
| Missing data | Fallback to 5-min if 1-min insufficient |

---

## 5. Git Commit

```
[FEATURE] 1-minute candle recovery score

- Add build_1min_candles() from ws_frames_raw.log
- Replace 5-min recovery with 15×1-min candles
- Faster reversal detection (15min vs 25min window)
- Adjust recovery threshold: 0.66 → 0.60
```

**Approval required before implementation.**

---

*Prepared by Mino 🪼 | 2026-06-18*

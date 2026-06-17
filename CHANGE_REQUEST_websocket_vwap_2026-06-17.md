# Change Request: WebSocket Incremental VWAP System

**Date:** 2026-06-17  
**Requester:** A A  
**File:** `poller.py`, `ws_logger.py`  
**Type:** ASK — Critical feature implementation

---

## 1. Problem Statement

The poller's VWAP calculation uses yfinance 5m data which is **15 minutes delayed**. This causes:
1. Stale VWAP signals during fast-moving periods
2. Bad entry/exit timing
3. Positions left open due to hard close crashes

The websocket-based adaptive VWAP was implemented on June 16 but **lost due to uncommitted changes**.

---

## 2. Solution: Incremental WebSocket VWAP

### 2.1 Core Concept
Calculate VWAP incrementally from every websocket tick:
- `cum_pv` += `typical_price` × `weight`
- `cum_weight` += `weight`
- `vwap = cum_pv / cum_weight`

**Weight formula:** `real(2x) * change(1+change*30)`
- `real=true` → weight ×2 (actual market data)
- `real=false` → weight ×1 (interpolated/filled)
- Larger price changes → higher weight

### 2.2 Data Flow
1. WebSocket listener processes every tick
2. Calculates incremental VWAP in real-time
3. Stores in `_ws_price_cache` per symbol
4. `fetch_data()` returns `ws_vwap` as 4th value
5. All entry/exit/hard_close use `ws_vwap` first
6. yfinance NEVER used for VWAP (only as price fallback)

### 2.3 Fallback Layers
1. **Primary:** WebSocket incremental VWAP
2. **Secondary:** Tick-based 1-min candle VWAP (from ws_prices.jsonl)
3. **Tertiary:** Targets and time constraints (no VWAP)

---

## 3. Implementation Plan

### Phase 1: Incremental VWAP Calculator (poller.py)
- Add `IncrementalVWAP` class or module-level state
- `update_vwap(symbol, price, change, real)` — called on every tick
- Store: `{symbol: {"cum_pv": float, "cum_weight": float, "last_vwap": float, "tick_count": int}}`

### Phase 2: WebSocket Listener Integration (poller.py)
- Modify `on_frame()` to call `update_vwap()` after price extraction
- Store calculated VWAP in `_ws_price_cache`
- Format: `_ws_price_cache[sym] = {..., "vwap": float, "vwap_ts": float}`

### Phase 3: fetch_data() Enhancement (poller.py)
- Return 4 values: `(price, df, source, ws_vwap)`
- `ws_vwap` from cache if < 300s old, else `None`
- yfinance data used for `df` (OHLCV) but NOT for VWAP

### Phase 4: Entry/Exit Logic Updates (poller.py)
- All VWAP decisions use `ws_vwap` first
- Fallback to `calc_vwap(df)` only if `ws_vwap` is None
- Hard close, stop/target, trailing all use real-time VWAP

### Phase 5: ws_logger Enhancement (ws_logger.py)
- Add `vwap` field to logged data
- Add `volume` field (from websocket `tv` field)
- Update `log_price()` signature to accept vwap and volume

---

## 4. Files to Modify

| File | Changes | Lines |
|------|---------|-------|
| `poller.py` | Add incremental VWAP state | ~104 (module level) |
| `poller.py` | `update_ws_vwap()` function | New, ~30 lines |
| `poller.py` | Modify `on_frame()` to update VWAP | ~1157 |
| `poller.py` | `fetch_data()` return 4 values | ~1237-1265 |
| `poller.py` | Update all VWAP callers | ~1349, 1529, 1594, etc. |
| `ws_logger.py` | Add vwap and volume to log | ~16-30 |
| `poller.py` | Pass vwap/volume to logger | ~1169 |

---

## 5. Testing Plan

1. Start websocket listener
2. Verify VWAP updates on each tick
3. Check VWAP vs yfinance VWAP (should track within 0.33%)
4. Test hard close with real-time VWAP
5. Test entry signals with rising/falling VWAP
6. Verify ws_prices.jsonl has vwap and volume fields

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| VWAP drifts from true value | Reset at market open (10:00) |
| Memory leak from cumulative state | Clear cache at session end |
| Performance impact | State is per-symbol dict, minimal overhead |
| Backward compatibility | `fetch_data()` can return None for vwap if disabled |

---

**Approval required before implementation.**

## Changes Summary

### ADDED
- `incremental_vwap_state` — per-symbol cumulative VWAP state
- `update_ws_vwap()` — updates VWAP on every tick
- `get_ws_vwap()` — retrieves current VWAP from cache
- `vwap` and `volume` fields to ws_prices.jsonl

### MODIFIED
- `fetch_data()` — returns `(price, df, source, ws_vwap)`
- `on_frame()` — calls `update_ws_vwap()` after price extraction
- All VWAP decision points — use `ws_vwap` first, fallback to yfinance
- `log_price()` — accepts vwap and volume parameters

### DELETED
- yfinance as primary VWAP source (now only price fallback)

---

*Prepared by Mino 🪼 | 2026-06-17 20:00 KSA*

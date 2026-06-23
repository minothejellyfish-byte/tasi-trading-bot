# 1-Minute Recovery Score Implementation
## Technical Specification
## Date: 2026-06-15
## Status: ASK

---

## Problem

**Current (v4.4):** Uses last 5 × 5-minute candles = 25-minute window
- Too slow to detect rapid reversals
- Misses short-term momentum shifts
- June 14: 5110 entry at 10:20 showed recovery on 1-min but not 5-min

**Solution (v4.5):** Use last 15 × 1-minute candles = 15-minute window
- Faster detection of reversals
- More accurate recovery probability
- Better suited for VWAP breakdown decisions

---

## Current Implementation (v4.4)

**Location:** poller.py line ~1725 (VWAP breakdown exit)

```python
# CURRENT (v4.4):
recent_candles = df_pos.tail(5)  # 5 × 5-min candles = 25 min
if len(recent_candles) >= 3:
    closes = [float(c) for c in recent_candles["Close"]]
    rising = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
    falling = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i-1])
    total = rising + falling
    recovery_prob = rising / total if total > 0 else 0.5
```

**Issues:**
1. Uses 5-min candles from `df_pos` (5-min dataframe)
2. 25-minute window too slow
3. Misses rapid 1-2 minute reversals
4. Volume calculation also uses 5-min averages

---

## Proposed Implementation (v4.5)

### Step 1: Create 1-Minute Candle Function

**Add to poller.py (after imports):**

```python
import yfinance as yf  # Already imported

def get_1min_recovery(symbol, entry_time_str, window=15):
    """
    Calculate recovery score using 1-minute candles.
    
    Args:
        symbol: Stock symbol (e.g., "1320")
        entry_time_str: ISO format entry time (e.g., "2026-06-14T10:20:00+03:00")
        window: Number of 1-minute candles to analyze (default: 15)
    
    Returns:
        float: Recovery probability 0.0-1.0 (0.5 = neutral)
    """
    try:
        # Fetch 1-minute data for today
        ticker = yf.Ticker(f"{symbol}.SR")
        df_1min = ticker.history(period="1d", interval="1m")
        
        if df_1min.empty:
            log.warning(f"No 1-min data for {symbol}, using default 0.5")
            return 0.5
        
        # Convert to Riyadh timezone
        df_1min = df_1min.tz_convert("Asia/Riyadh")
        
        # Parse entry time
        entry_dt = datetime.fromisoformat(entry_time_str)
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=RIYADH)
        
        # Get candles after entry
        df_after = df_1min[df_1min.index > entry_dt].tail(window)
        
        if len(df_after) < 3:
            log.warning(f"Only {len(df_after)} 1-min candles after entry, using default")
            return 0.5
        
        # Calculate rising vs falling candles
        closes = [float(c) for c in df_after["Close"]]
        rising = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
        total = len(closes) - 1
        
        recovery_prob = rising / total if total > 0 else 0.5
        
        # Volume strength (optional)
        if "Volume" in df_after.columns:
            recent_vol = float(df_after["Volume"].mean())
            # Compare to pre-entry volume if available
            df_before = df_1min[df_1min.index <= entry_dt].tail(window)
            if not df_before.empty and "Volume" in df_before.columns:
                avg_vol = float(df_before["Volume"].mean())
                vol_ratio = min(recent_vol / avg_vol, 1.5) if avg_vol > 0 else 1.0
            else:
                vol_ratio = 1.0
        else:
            vol_ratio = 1.0
        
        # Weighted recovery score
        recovery_score = recovery_prob * vol_ratio
        
        log.info(f"1-min recovery: {symbol} prob={recovery_prob:.2f} vol_ratio={vol_ratio:.2f} score={recovery_score:.2f} candles={len(df_after)}")
        
        return min(recovery_score, 1.0)  # Cap at 1.0
        
    except Exception as e:
        log.error(f"1-min recovery error for {symbol}: {e}")
        return 0.5  # Default on error
```

---

### Step 2: Modify VWAP Breakdown Logic

**Location:** poller.py line ~1725

**Replace existing recovery calculation:**

```python
# OLD (v4.4):
# recent_candles = df_pos.tail(5)
# if len(recent_candles) >= 3:
#     closes = [float(c) for c in recent_candles["Close"]]
#     rising = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
#     falling = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i-1])
#     total = rising + falling
#     recovery_prob = rising / total if total > 0 else 0.5
#     
#     # Volume strength
#     recent_vol = float(recent_candles["Volume"].mean()) if "Volume" in recent_candles else 0
#     avg_vol = float(df_pos["Volume"].mean()) if "Volume" in df_pos else 1
#     vol_strength = min(recent_vol / avg_vol, 1.5) if avg_vol > 0 else 1.0
#     
#     recovery_score = recovery_prob * vol_strength

# NEW (v4.5):
entry_time_str = pos.get("entry_time", "")
if entry_time_str:
    recovery_score = get_1min_recovery(symbol, entry_time_str, window=15)
    recovery_prob = recovery_score  # Already includes volume weighting
else:
    recovery_score = 0.5
    recovery_prob = 0.5
```

---

### Step 3: Update Breakeven Hold Logic

**Current breakeven check (v4.4):**
```python
# Step 3: Breakeven hold
loss_pct = abs(gain_pct) if gain_pct < 0 else 0
is_recovering = recovery_score > 0.66
is_small_loss = loss_pct < 0.03

if is_small_loss and is_recovering:
    continue  # Hold for recovery
```

**No change needed** — uses same `recovery_score` variable.

---

## Data Flow Comparison

### Current (v4.4):
```
ws_prices.jsonl → 5-min candles (df_pos) → tail(5) → recovery calc
                                      ↓
                                25-minute window
```

### Proposed (v4.5):
```
ws_prices.jsonl → 5-min candles (df_pos) → VWAP calc
                            ↓
                    yfinance 1-min fetch → tail(15) → recovery calc
                                      ↓
                                15-minute window
```

---

## Performance Considerations

| Aspect | Current (v4.4) | Proposed (v4.5) |
|--------|---------------|-----------------|
| Data source | `df_pos` (in-memory) | yfinance API call |
| Latency | Instant | ~1-2 seconds |
| Window | 25 min | 15 min |
| Accuracy | Medium | High |
| API calls | 0 | 1 per VWAP check |
| Fallback | None | Default 0.5 on error |

**Mitigation:**
- Cache 1-min data for 1 minute (don't refetch within 60s)
- Use fallback to 5-min if yfinance fails
- Only fetch when VWAP breakdown detected (not every cycle)

---

## Testing Plan

### Unit Test:
```python
def test_1min_recovery():
    # Test with known data
    symbol = "5110"
    entry_time = "2026-06-14T10:20:00+03:00"
    score = get_1min_recovery(symbol, entry_time, window=15)
    
    # June 14: 5110 at 10:20 had 9 rising, 6 falling = 0.60
    assert 0.4 <= score <= 0.8
```

### Backtest:
1. Run on June 14 data
2. Compare 1-min vs 5-min recovery scores
3. Verify decisions would have been better

### Integration:
1. Deploy with logging (no action)
2. Compare 1-min vs 5-min scores side-by-side
3. Enable after 3 days of matching results

---

## Rollback Plan

```python
# Feature flag
ENABLE_1MIN_RECOVERY = True  # Set False to rollback

# In VWAP breakdown logic:
if ENABLE_1MIN_RECOVERY:
    recovery_score = get_1min_recovery(symbol, entry_time_str)
else:
    # Old logic
    recent_candles = df_pos.tail(5)
    # ... existing code ...
```

---

## Example: June 14 5110 at 10:20

**Entry:** 17.01 at 10:20

**1-minute candles after entry:**
| Time | Close | Rising? |
|------|-------|---------|
| 10:21 | 17.01 | — |
| 10:22 | 17.00 | 📉 |
| 10:23 | 17.00 | — |
| 10:24 | 17.01 | 📈 |
| 10:25 | 17.00 | 📉 |
| 10:26 | 17.01 | 📈 |
| 10:27 | 17.00 | 📉 |
| 10:28 | 17.01 | 📈 |
| 10:29 | 17.01 | — |
| 10:30 | 17.01 | — |
| 10:31 | 17.01 | — |
| 10:32 | 17.01 | — |
| 10:33 | 17.01 | — |
| 10:34 | 17.00 | 📉 |
| 10:35 | 16.99 | 📉 |

**Results:**
- Rising: 3
- Falling: 5
- Flat: 6
- **Recovery prob: 3/8 = 0.375** (weak recovery)

**Decision:** Would have triggered SELL (correct!)

**vs 5-minute version:**
- 10:20-10:25 candle: Mixed
- 10:25-10:30 candle: Flat
- Would have shown recovery_score ≈ 0.50 (false hold)

---

*Prepared by Mino 🪼 | 2026-06-15*

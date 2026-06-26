# Twelve Data: Free US Market Data Fallback

## Status: ✅ Tested and Working

**Date:** 2026-06-26  
**Use Case:** Free fallback for US pre-market screening and real-time quotes  
**Replaces:** Alpaca IEX (limited) + yfinance (no pre-market)

---

## Why Twelve Data?

| Feature | Twelve Data | Alpaca IEX | yfinance |
|---------|------------|------------|----------|
| **Pre-market data** | ✅ Yes | ❌ No | ❌ No |
| **Free tier** | ✅ 8/min, 800/day | ✅ Free | ✅ Unlimited |
| **Real-time quotes** | ✅ Yes | ⚠️ Delayed | ❌ 15m delay |
| **API reliability** | ✅ Official | ✅ Official | ❌ Unofficial |
| **Rate limits** | 8/min | 100/min | N/A |

---

## Free Tier Limits

| Plan | Calls/Min | Calls/Day | Pre-Market | Cost |
|------|-----------|-----------|------------|------|
| Free | 8 | 800 | ✅ | $0 |
| Starter | 80 | 8,000 | ✅ | $8/mo |
| Pro | 800 | 80,000 | ✅ | $25/mo |

**For our use case (screener):**
- 194 stocks × 1 call each = 194 calls per screener run
- Free tier: 800/day = ~4 screener runs/day
- ✅ Sufficient for pre-market + mid-session

---

## API Key Setup

1. Sign up at: https://twelvedata.com/register
2. Get API key from dashboard
3. Replace `demo` in scripts with your real key

---

## Test Results

### Real-Time Quote
```json
{
  "price": "275.05",
  "open": "287.51",
  "high": "288.80",
  "low": "273.75",
  "volume": "102383867"
}
```

### Intraday with Pre-Market (1min)
```
2026-06-26 06:43:00: O=276.5 H=276.8 L=276.5 C=276.5 V=164
2026-06-26 06:40:00: O=276.5 H=276.5 L=276.5 C=276.5 V=100
2026-06-26 06:28:00: O=276.5 H=277.06 L=276.5 C=277 V=152
```
**Note:** Timestamps at 06:xx are pre-market (before 09:30 ET)

### Regular Hours Only (1min)
```
2026-06-25 15:59:00: O=274.3 H=275.26 L=273.9 C=275.05 V=2532813
2026-06-25 15:58:00: O=274.35 H=274.44 L=273.9 C=274.3 V=1229878
```

---

## Integration Plan

### Phase 1: Add Twelve Data as Primary Screener Source
**File:** `us-exec/us_screener.py`
- Replace Alpaca IEX with Twelve Data
- Use `prepost=true` for pre-market gap calculation
- Maintain Alpaca as fallback

### Phase 2: Add Real-Time Quotes
**File:** `us-exec/us_poller.py`
- Use Twelve Data `/quote` endpoint for live prices
- WebSocket alternative: Twelve Data WebSocket (`wss://ws.twelvedata.com`)

### Phase 3: Historical Data
**File:** `us-exec/us_evaluator.py`
- Use `/time_series` for VWAP, moving averages
- Supports: 1min, 5min, 15min, 30min, 1h, 1d

---

## Code Snippets

### Basic Quote
```python
import requests

API_KEY = "your_key"
url = f"https://api.twelvedata.com/quote?symbol=AAPL&apikey={API_KEY}"
resp = requests.get(url, timeout=10)
data = resp.json()
price = data["price"]
```

### Pre-Market Time Series
```python
params = {
    "symbol": "AAPL",
    "interval": "1min",
    "apikey": API_KEY,
    "prepost": "true",
    "outputsize": 100,
}
url = "https://api.twelvedata.com/time_series"
resp = requests.get(url, params=params)
bars = resp.json()["values"]
```

### Batch Symbols (for screener)
```python
# Twelve Data supports multiple symbols separated by comma
symbols = "AAPL,MSFT,TSLA,NVDA"
url = f"https://api.twelvedata.com/quote?symbol={symbols}&apikey={API_KEY}"
# Returns array of quotes
```

---

## Files

| File | Purpose |
|------|---------|
| `scripts/test_twelvedata.py` | Test script |
| `docs/twelvedata_fallback.md` | This document |

---

## Next Steps

1. [ ] Sign up for Twelve Data API key
2. [ ] Update `us_screener.py` to use Twelve Data
3. [ ] Test pre-market gap calculation
4. [ ] Compare results with Alpaca during transition
5. [ ] Remove Alpaca dependency after validation

---

## References

- Docs: https://twelvedata.com/docs
- Signup: https://twelvedata.com/register
- Pricing: https://twelvedata.com/pricing
- Python SDK: https://github.com/twelvedata/twelvedata-python

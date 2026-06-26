# Derayah Global Migration Plan

## Overview
**Current:** US trading via Alpaca API (paper trading)
**Target:** US trading via Derayah Global + IB Gateway
**Timeline:** ~1 week from now (early July 2026)

## Why Migrate?
1. **Single broker** — Same broker (Derayah) for TASI + US markets
2. **Regulatory** — Saudi-compliant US trading via local broker
3. **Cost** — Potentially lower fees vs Alpaca
4. **Integration** — Unified capital/position tracking

## Current State (2026-06-26)

### Alpaca (Current)
| Component | Status |
|-----------|--------|
| API | ✅ Working (but limited pre-market data) |
| Screener | ⚠️ Partial (IEX only, no SIP) |
| Order execution | ✅ Paper trading |
| WebSocket | ✅ Real-time quotes |

### Derayah Global + IB (Future)
| Component | Status |
|-----------|--------|
| Browser login | ✅ Working (Chrome/CDP) |
| IB Gateway | ⏳ Installed, needs config |
| API access | ⏳ Pending IB Gateway setup |
| Order execution | ❌ Not tested |

## Migration Steps

### Phase 1: IB Gateway Setup (This Week)
- [ ] Configure IB Gateway with Derayah credentials
- [ ] Test API connectivity (localhost:5000)
- [ ] Verify market data retrieval (quotes, history)
- [ ] Test order placement (paper account first)

### Phase 2: Data Source Transition (Next Week)
- [ ] Implement IB market data in `us_screener.py`
- [ ] Add IB price feed to `us_poller.py`
- [ ] Update entry/exit logic for IB data format
- [ ] Maintain Alpaca as fallback during transition

### Phase 3: Order Execution Transition
- [ ] Implement IB order placement in `us_poller.py`
- [ ] Test buy/sell orders on paper account
- [ ] Migrate position tracking to IB
- [ ] Update bookkeeper for IB positions

### Phase 4: Cleanup
- [ ] Remove Alpaca dependencies
- [ ] Update cron jobs for IB-only operation
- [ ] Archive Alpaca-specific code
- [ ] Update documentation

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| IB API issues | Keep Alpaca fallback during transition |
| Data quality differences | Compare IB vs Alpaca for 1 week |
| Order execution differences | Test extensively on paper account |
| Session management | IB Gateway runs 24/7 (more stable than browser) |

## Technical Details

### IB Gateway API Format
```python
# Current Alpaca
alpaca.get_bars('AAPL', timeframe='1Min')

# Future IB
requests.get('http://localhost:5000/v1/api/iserver/marketdata/history?conid=265598&period=1d&bar=1min')
```

### Key Differences
| Feature | Alpaca | IB |
|---------|--------|-----|
| Symbol format | `AAPL` | `conid` (numeric ID) |
| Pre-market data | Limited (SIP paid) | Available (with subscription) |
| Real-time quotes | WebSocket | WebSocket |
| Order types | Market, Limit, Stop | Market, Limit, Stop, Trail, etc. |

## Files to Modify
- `us-exec/us_poller.py` — Replace Alpaca API calls with IB
- `us-exec/us_screener.py` — Replace Alpaca data with IB
- `us-exec/us_bookkeeper.py` — Update position sync
- `us-exec/config.py` — Add IB credentials

## Testing Checklist
- [ ] AAPL quote retrieval
- [ ] Historical data (1m, 5m, 1d)
- [ ] Market order placement
- [ ] Limit order placement
- [ ] Order status tracking
- [ ] Position sync
- [ ] PnL calculation

## Rollback Plan
If IB migration fails:
1. Revert to Alpaca in `us_poller.py`
2. Update config to use Alpaca credentials
3. Restart services
4. No data loss (positions tracked separately)

## Timeline
| Week | Task |
|------|------|
| Jun 30 - Jul 4 | IB Gateway setup + testing |
| Jul 7 - Jul 11 | Data source transition |
| Jul 14 - Jul 18 | Order execution migration |
| Jul 21+ | Full IB operation, Alpaca archived |

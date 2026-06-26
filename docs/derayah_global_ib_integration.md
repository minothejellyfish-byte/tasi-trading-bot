# Derayah Global + Interactive Brokers Integration

## Overview
**Date:** 2026-06-26
**Purpose:** Future US trading via Derayah Global (Saudi broker) using IB backend
**Current Status:** Browser-based Client Portal logged in, exploring data access

## Architecture
```
Derayah Global (Saudi Broker)
    ↓ SSO Login (partnerID=Derayah)
IB Client Portal (clientam.com)
    ↓ Proxy API
IB Backend (Market Data + Execution)
```

## Authentication
- **Login URL:** `https://www.clientam.com/sso/Login?partnerID=Derayah`
- **Cookies Required:**
  - `cp.lb` — Load balancer (required for API routing)
  - `ibcust` — IB customer session
  - `JSESSIONID` — Portal session
  - Multiple `x-sess-uuid` — Session tracking (many instances)
  - `cp` — Client portal state

## API Endpoints Discovered

### Market Data
```
GET /portal.proxy/v1/portal/iserver/marketdata/history?conid={id}&period={period}&bar={bar_size}
GET /portal.proxy/v1/portal/iserver/marketdata/snapshot?conids={ids}&fields={fields}
```

### Contract Info
```
GET /portal.proxy/v1/portal/iserver/contract/{conid}/info
GET /portal.proxy/v1/portal/trsrv/secdef
```

### Portfolio
```
GET /portal.proxy/v1/portal/portfolio2/positions/{account_id}
GET /portal.proxy/v1/portal/iserver/account/{account_id}/orders
```

### Fundamentals
```
GET /portal.proxy/v1/portal/iserver/fundamentals/{conid}/summary
```

## Data Access Methods

### Method 1: Browser Automation (Current)
**Pros:** Works with existing Chrome session
**Cons:** Fragile, requires UI interaction

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp('http://127.0.0.1:18801')
    page = browser.contexts[0].pages[0]
    page.goto('https://www.clientam.com/portal/#/quote/{conid}')
    # Extract from DOM
```

### Method 2: IB Client Portal API (Recommended)
**Pros:** REST API, stable, direct data access
**Cons:** Requires separate download/setup

Download: `https://www.interactivebrokers.com/en/index.php?f=16457`
Runs on: `localhost:5000`

## Current Limitations
1. **No direct API access** — Cookie complexity prevents standalone API calls
2. **Browser-dependent** — Must maintain Chrome session
3. **No pre-market data** — Same as TASI limitation

## Next Steps
- [ ] Download and test IB Client Portal API
- [ ] Verify REST endpoints work with local gateway
- [ ] Test market data retrieval for AAPL, SPY, QQQ
- [ ] Compare latency vs Alpaca
- [ ] Plan migration from Alpaca to IB

## Files
- Current Chrome profile: `~/.config/google-chrome/derayah-profile/`
- Session cookies: Managed by browser
- CDP endpoint: `http://127.0.0.1:18801`

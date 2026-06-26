# IB Gateway Setup for Derayah Global

## Installation Status
**Date:** 2026-06-26
**Location:** `/home/mino/Jts/ibgateway/1045`
**Version:** IB Gateway 10.45

## What is IB Gateway?
IB Gateway is a headless (no GUI) version of Interactive Brokers TWS that provides:
- **REST API** on localhost:5000 (or configured port)
- **WebSocket** streaming for real-time data
- **Order execution** programmatically
- **Market data** without browser overhead

## Architecture
```
Your App (Python)
    ↓ HTTP/WebSocket
IB Gateway (localhost:5000)
    ↓ TCP
Interactive Brokers (US Markets)
```

## Current Status
- ✅ Downloaded (245 MB installer)
- ✅ Installed to `/home/mino/Jts/ibgateway/1045`
- ⏳ Configuration needed (login credentials, API settings)
- ⏳ Testing needed

## Next Steps Required

### 1. Configure IB Gateway
IB Gateway requires:
- **Username/Password** — Your Derayah Global IB account credentials
- **API Settings** — Enable API, set port (5000), allow localhost

### 2. First Run (Manual)
```bash
/home/mino/Jts/ibgateway/1045/ibgateway
```
- Login with Derayah Global credentials
- Accept API connection settings
- Enable "Create API message log"
- Enable "Read-Only API" (safer for testing)

### 3. Headless Run (Future)
Use **IBC (IB Controller)** for automated login:
```bash
# Download IBC from: https://github.com/IbcAlpha/IBC
git clone https://github.com/IbcAlpha/IBC.git ~/ib-controller
# Configure config.ini with credentials
# Run: ~/ib-controller/scripts/gatewaystart.sh
```

## API Endpoints (Once Running)

### Market Data
```
GET /v1/api/iserver/marketdata/history?conid={id}&period={period}
GET /v1/api/iserver/marketdata/snapshot?conids={ids}&fields={fields}
```

### Portfolio
```
GET /v1/api/portfolio/{account_id}/positions
GET /v1/api/iserver/account/orders
```

### Contract Info
```
GET /v1/api/iserver/contract/{conid}/info
GET /v1/api/trsrv/secdef?conids={ids}
```

## Comparison: IB Gateway vs Browser Scraping

| Feature | IB Gateway | Browser Scraping |
|---------|-----------|------------------|
| **Speed** | Fast (direct TCP) | Slow (DOM parsing) |
| **Reliability** | High (official API) | Low (UI changes) |
| **Real-time data** | Yes (WebSocket) | No (polling) |
| **Setup complexity** | Medium | Low (already working) |
| **Maintenance** | Low | High (breaks on UI updates) |

## Files
- Installation: `/home/mino/Jts/ibgateway/1045/`
- Startup script: `/home/mino/tasi-exec/scripts/start_ib_gateway.sh`
- Documentation: `/home/mino/tasi-exec/docs/ib_gateway_setup.md`

## Notes
- IB Gateway requires Java (already installed: OpenJDK 21)
- First run must be interactive (login + API confirmation)
- For production: Use IBC for automated credential handling
- Port 5000 may conflict with existing services (check first)

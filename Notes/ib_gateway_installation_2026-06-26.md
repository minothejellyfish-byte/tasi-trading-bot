# IB Gateway Installation — Derayah Global Integration

**Date:** 2026-06-26  
**Time:** ~11:50 KSA  
**Installer:** IB Gateway 10.45 (stable)  
**Downloaded from:** `https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh`  
**Installed to:** `/home/mino/Jts/ibgateway/1045`

---

## Installation Steps Performed

1. Downloaded 245 MB installer to `/tmp/ib-setup/ibgateway.sh`
2. Made executable: `chmod +x ibgateway.sh`
3. Ran unattended install (default path accepted)
4. Installation completed to `/home/mino/Jts/ibgateway/1045`

---

## What IB Gateway Provides

| Feature | Description |
|---------|-------------|
| **REST API** | `localhost:5000/v1/api/` |
| **WebSocket** | Real-time streaming |
| **Order execution** | Programmatic buy/sell |
| **Market data** | Quotes, history, fundamentals |

---

## Files Created

| File | Purpose |
|------|---------|
| `/home/mino/Jts/ibgateway/1045/ibgateway` | Main executable |
| `/home/mino/Jts/ibgateway/1045/ibgateway.vmoptions` | JVM config |
| `/home/mino/tasi-exec/scripts/start_ib_gateway.sh` | Startup script |
| `/home/mino/tasi-exec/docs/ib_gateway_setup.md` | Setup guide |
| `/home/mino/tasi-exec/docs/derayah_global_migration_plan.md` | Migration timeline |

---

## Next Step (Manual)

**First run must be interactive** — need to login with Derayah Global credentials and enable API:

```bash
/home/mino/Jts/ibgateway/1045/ibgateway
```

Then:
1. Enter Derayah Global IB username/password
2. Accept API connection settings
3. Enable "Read-Only API" (safer for testing)
4. Set port to 5000
5. Allow localhost only

---

## API Endpoints (After Login)

```
GET /v1/api/iserver/marketdata/history?conid=265598&period=1d
GET /v1/api/iserver/marketdata/snapshot?conids=265598&fields=31,70,71
GET /v1/api/iserver/contract/265598/info
GET /v1/api/portfolio/{account_id}/positions
```

---

## Migration Context

This is **Phase 1** of migrating US trading from Alpaca to Derayah Global + IB. Timeline: ~1 week from now. See `docs/derayah_global_migration_plan.md` for full plan.

---

## Notes

- Java 21 already installed (required for IB Gateway)
- Installer is self-contained (includes JRE)
- For headless/automated runs, use **IB Controller (IBC)** in future
- IB Gateway is more stable than browser scraping for API access

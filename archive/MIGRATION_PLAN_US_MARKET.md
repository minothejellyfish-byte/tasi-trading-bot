# US Market Migration Plan — TASI System

## Core Principle: Complete Separation

**NEVER share:**
- Process names (both can't be `bot.py` or `poller.py`)
- Config files (separate `capital.json`, `positions.json`, etc.)
- Log files (separate `exec.log`, `poller.log`)
- Ports (separate CDP ports, separate WebSocket feeds)
- Working directories (separate `/home/mino/tasi-exec/` and `/home/mino/us-exec/`)
- Cron jobs (separate `agentId` or distinct job names)
- Telegram bot tokens (if using separate bots) OR shared bot with prefixed commands

---

## Directory Structure

```
/home/mino/
├── tasi-exec/              # Saudi TASI system (UNCHANGED)
│   ├── bot.py
│   ├── poller.py
│   ├── ws_probe.py
│   ├── screener.py
│   ├── midscreen_ws.py
│   ├── market_regime.py
│   ├── derayah_api.py
│   ├── capital_tracker.py
│   ├── capital.json
│   ├── positions.json
│   ├── regime.json
│   ├── exec.log
│   ├── poller.log
│   └── ...
│
└── us-exec/                # NEW US market system
    ├── us_bot.py           # Renamed from bot.py
    ├── us_poller.py        # Renamed from poller.py
    ├── us_ws_probe.py      # Renamed from ws_probe.py
    ├── us_screener.py      # New: US premarket screener
    ├── us_midscreen.py     # New: US mid-session screener
    ├── us_market_regime.py # SPY/QQQ-based regime
    ├── alpaca_api.py       # NEW: Alpaca broker wrapper
    ├── us_capital_tracker.py
    ├── us_capital.json
    ├── us_positions.json
    ├── us_regime.json
    ├── us_exec.log
    ├── us_poller.log
    └── ...
```

---

## Process Naming

| TASI | US | Why |
|------|-----|-----|
| `bot.py` | `us_bot.py` | Different process names |
| `poller.py` | `us_poller.py` | No PID conflicts |
| `ws_probe.py` | `us_ws_probe.py` | Separate WebSocket feeds |
| `screener.py` | `us_screener.py` | Different stock universe |

**Check command:**
```bash
ps aux | grep -E "bot.py|us_bot.py|poller.py|us_poller.py"
```

---

## Port Allocation

| Service | TASI Port | US Port | Purpose |
|---------|-----------|---------|---------|
| Chrome/CDP | 18801 | 18802 | Browser automation |
| WebSocket feed | Internal (Derayah) | Alpaca/Polygon | Real-time prices |
| Telegram bot | Shared or separate | Shared or separate | Notifications |

**Chrome profiles:**
- TASI: `/home/mino/.config/google-chrome/derayah-profile`
- US: `/home/mino/.config/google-chrome/alpaca-profile` (or none if using API)

---

## Configuration Files (Complete Separation)

| File | TASI Path | US Path | Content |
|------|-----------|---------|---------|
| Capital | `tasi-exec/capital.json` | `us-exec/us_capital.json` | Separate balances |
| Positions | `tasi-exec/positions.json` | `us-exec/us_positions.json` | Separate holdings |
| Regime | `tasi-exec/regime.json` | `us-exec/us_regime.json` | Separate market state |
| Picks | `tasi-exec/picks.json` | `us-exec/us_picks.json` | Separate stock picks |
| Blocked | `tasi-exec/blocked_symbols.txt` | `us-exec/us_blocked.txt` | Separate blocked lists |
| Stand Down | `tasi-exec/stand_down` | `us-exec/us_stand_down` | Separate halt flags |

---

## Telegram Bot Strategy

**Option A: Shared Bot (Recommended)**
- Use same bot token
- Prefix commands:
  - `/status` → TASI status
  - `/us_status` → US status
  - `/stand_down` → TASI halt
  - `/us_stand_down` → US halt
- Pros: Single chat interface
- Cons: Command namespace pollution

**Option B: Separate Bots**
- TASI bot: @tasi_exec_bot
- US bot: @us_exec_bot
- Pros: Clean separation
- Cons: Two chats to monitor

**My recommendation: Option A with prefixed commands**

---

## Cron Jobs (Separate Scheduling)

**TASI Crons** (agentId: main, existing — NO CHANGES):
```
09:50 — tasi-premarket-screener
10:00 — tasi-price-poller
10:30 — tasi-midscreen-1
12:00 — tasi-midscreen-2
13:30 — tasi-rescreen
15:35 — post-market-analysis
```

**US Crons** (NEW, agentId: main or us-agent):
```
04:00 ET / 11:00 GMT+3 — us-premarket-screener (scan pre-market gaps)
09:30 ET / 16:30 GMT+3 — us-price-poller (market open)
11:00 ET / 18:00 GMT+3 — us-midscreen-1
13:00 ET / 20:00 GMT+3 — us-midscreen-2
15:30 ET / 22:30 GMT+3 — us-rescreen
16:30 ET / 23:30 GMT+3 — us-post-market-analysis
```

**Note**: US market runs 16:30-23:00 GMT+3 — overlaps with evening in Saudi.

---

## Shared Components (Read-Only)

These can be **symlinked** or **copied** — never written by both systems:

```
us-exec/
├── shared/
│   ├── telegram_handler.py   # Shared notification logic (read-only)
│   ├── utils.py               # Shared utilities (read-only)
│   └── market_regime_base.py # Base regime logic (read-only)
```

---

## Systemd Services (If Using)

| Service | Command | Purpose |
|---------|---------|---------|
| `tasi-bot.service` | `python3 /home/mino/tasi-exec/bot.py` | TASI bot |
| `tasi-poller.service` | `python3 /home/mino/tasi-exec/poller.py` | TASI poller |
| `us-bot.service` | `python3 /home/mino/us-exec/us_bot.py` | US bot |
| `us-poller.service` | `python3 /home/mino/us-exec/us_poller.py` | US poller |

---

## Data Flow Comparison

### TASI Flow
```
TickerChart WebSocket → ws_probe.py → poller.py → Derayah API (CDP)
                                              ↓
                                        Telegram
```

### US Flow (Proposed)
```
Alpaca WebSocket → us_ws_probe.py → us_poller.py → Alpaca REST API
                                              ↓
                                        Telegram
```

**Key difference**: US uses official API → no Chrome/CDP needed for trading.

---

## Capital Isolation

**Critical**: Never allow US trades to affect TASI capital or vice versa.

```python
# TASI
capital_file = "/home/mino/tasi-exec/capital.json"

# US
us_capital_file = "/home/mino/us-exec/us_capital.json"
```

---

## Testing Strategy

| Phase | TASI | US | Duration |
|-------|------|-----|----------|
| 1. Parallel Data | Running normally | Screener only, no trades | 1 week |
| 2. US Paper Trading | Running normally | Paper trades via Alpaca | 2 weeks |
| 3. Parallel Live | Running normally | Live with 1/10th capital | 2 weeks |
| 4. Full Deployment | Full capital | Full capital | Ongoing |

---

## Risk Isolation

| Risk | Mitigation |
|------|-----------|
| Process conflict | Different names, different PIDs |
| File corruption | Separate directories, separate JSONs |
| Capital mix-up | Hardcoded separate paths |
| Network conflict | Separate ports, separate Chrome profiles |
| Log confusion | Separate log files |
| Cron collision | Different job names, different schedules |

---

## Implementation Order

### Week 1: Foundation
- [ ] Create `/home/mino/us-exec/` directory
- [ ] Implement `us_screener.py` (SPY/QQQ universe)
- [ ] Test data feed (Alpaca paper)
- [ ] Run parallel screens for 5 days

### Week 2: Broker Integration
- [ ] Implement `alpaca_api.py`
- [ ] Implement `us_bot.py` (Telegram with `/us_*` prefix)
- [ ] Paper trade for 5 days
- [ ] Compare signals with TASI

### Week 3: Poller + Signals
- [ ] Implement `us_poller.py` with entry/exit logic
- [ ] Adapt regime classifier for SPY
- [ ] Test all signal types (VWAP, breakout, zone hold)

### Week 4: Deployment
- [ ] Live trade with $1K capital
- [ ] Monitor for 5 days
- [ ] Scale up if profitable

---

## Summary

**Complete separation achieved through:**
1. Different directory (`us-exec/` vs `tasi-exec/`)
2. Different file names (`us_*.py` vs `*.py`)
3. Different config files (`us_*.json` vs `*.json`)
4. Different ports (18802 vs 18801)
5. Different cron names (`us-*` vs `tasi-*`)
6. Prefixed Telegram commands (`/us_*` vs `/*`)
7. Separate log files (`us_*.log` vs `*.log`)

**Both systems can run simultaneously without interference.**

**Ready to start?**

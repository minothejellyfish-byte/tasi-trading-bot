# TASI Trading System — Weekly Timeline

## Sunday Night (Before Market Opens)

| Time | Action | Who |
|------|--------|-----|
| **21:00** | Check Chrome is running (`pgrep chrome`) | Mino |
| **21:00** | Verify Derayah session valid (`curl 18801/json/version`) | Mino |
| **21:00** | Check SOCKS5 tunnel (`systemctl --user status socks-tunnel`) | Mino |
| **21:00** | Check bot is running (`pgrep -f bot.py`) | Mino |
| **21:00** | Verify Sharia list current (check `sharia_list.json` date) | Mino |

## Sunday–Thursday (Trading Days)

| Time | Action | Script | Output |
|------|--------|--------|--------|
| **09:50** | Pre-market screen | `screener.py` | `picks.json` (top 20) |
| **10:00** | Market opens — poller starts | `poller.py` | Real-time monitoring |
| **10:30** | Mid-screen 1 | `midscreen_ws.py` | `picks_1030.json` |
| **10:30** | Poller reloads picks | `poller.py` | Position upgrade check |
| **12:00** | Mid-screen 2 | `midscreen_ws.py` | `picks_1200.json` |
| **12:00** | Poller reloads picks | `poller.py` | Position upgrade check |
| **13:30** | Rescreen | `midscreen_ws.py` | `picks_1330.json` |
| **13:30** | Poller reloads picks | `poller.py` | Position upgrade check |
| **14:30** | Stop cycling | `poller.py` | No more auto-rebuys |
| **14:45** | Hard close | `poller.py` | Sell all positions |
| **15:30** | Market closes | — | — |
| **15:35** | Post-market analysis | `post_market_v2.py` | HTML report |
| **15:35** | Send report to Telegram | `post_market_v2.py` | Group message |

## Thursday Night

| Time | Action | Script |
|------|--------|--------|
| **22:00** | Refresh Sharia list | `screener.py` | Update `sharia_list.json` |

## Friday

| Time | Action | Who |
|------|--------|-----|
| **03:00** | Weekly reboot | Cron (`sudo /sbin/reboot`) |
| **After reboot** | Chrome auto-starts | `start-chrome.sh` |
| **After reboot** | Bot auto-starts | `tasi-exec.service` |
| **After reboot** | SOCKS tunnel auto-starts | `socks-tunnel.service` |

## Every Day (Background)

| Interval | Action | Script |
|----------|--------|--------|
| Every 5 min | Keep Derayah alive | `derayah_keepalive.py` |
| Every 30 min | Regime check | `market_regime.py` |
| Every 30 min | Update exit targets | `poller.py` |
| Every tick | WebSocket data logging | `ws_logger.py` |
| Every day 04:00 | Log cleanup | `cleanup_logs.py` |
| Every day 04:00 | RAM cleanup | `daily-ram-cleanup` |

## Manual Triggers (Amin Only)

| Command | Action |
|---------|--------|
| `BUY SYMBOL QTY @ PRICE` | Manual buy |
| `SELL SYMBOL QTY @ PRICE` | Manual sell |
| `STATUS` | Portfolio snapshot |
| `CLOSE ALL` | Emergency close all |

## Key Files Updated Daily

| File | Updated By | When |
|------|-----------|------|
| `picks.json` | `screener.py` | 09:50 |
| `picks_1030.json` | `midscreen_ws.py` | 10:30 |
| `picks_1200.json` | `midscreen_ws.py` | 12:00 |
| `picks_1330.json` | `midscreen_ws.py` | 13:30 |
| `positions.json` | `poller.py` | Every trade |
| `regime.json` | `market_regime.py` | Every 30 min |
| `ws_prices_YYYY-MM-DD.jsonl` | `ws_logger.py` | Continuous |
| `reports/post_market_YYYY-MM-DD.html` | `post_market_v2.py` | 15:35 |
| `learning.json` | `post_market_v2.py` | 15:35 |

## Sunday Pre-Market Checklist

```
[ ] Chrome running on port 18801
[ ] Derayah session valid
[ ] SOCKS5 tunnel active
[ ] Bot running
[ ] Sharia list fresh (check date)
[ ] picks.json from last session reviewed
[ ] positions.json empty (no stale positions)
[ ] regime.json shows current regime
```

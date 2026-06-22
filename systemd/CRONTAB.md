# TASI Crontab Reference

**Last updated:** 2026-06-22

## OpenClaw Cron Jobs (Managed via OpenClaw)

| Name | Schedule | Purpose |
|------|----------|---------|
| `tasi-premarket-screener` | `50 9 * * 0-4` | Premarket pick generation |
| `tasi-stand-down-cleanup` | `55 9 * * 0-4` | Remove stale stand_down files |
| `tasi-price-poller` | `0 10 * * 0-4` | Start price poller |
| `tasi-midscreen-1` | `30 10 * * 0-4` | First mid-session screen |
| `tasi-midscreen-2` | `0 12 * * 0-4` | Second mid-session screen |
| `tasi-rescreen` | `30 13 * * 0-4` | Pre-cutoff rescreen |
| `tasi-evaluator-v4.12` | `10,40 10-14 * * 0-4` | Pick evaluation (9x/day) |
| `tasi-watchdog-start` | `25 9 * * 0-4` | Start watchdog |
| `tasi-watchdog-stop` | `35 16 * * 0-4` | Stop watchdog |
| `post-market-analysis` | `35 15 * * 0-4` | Daily post-market review |
| `sharia-list-refresh` | `0 22 * * 4` | Refresh Sharia list |
| `tasi-weekly-report` | `0 20 * * 5` | Weekly report |

## System Cron (Traditional crontab)

```bash
# Derayah SSO refresh + auto-recovery (v4.3 — 5-min interval)
*/5 * * * * /home/mino/tasi-exec/derayah_refresh_cron.sh >> /home/mino/tasi-exec/refresh_cron.log 2>&1

# Cleanup stale stand_down files before market open
55 9 * * 0-4 /home/mino/tasi-exec/cleanup_stand_down.sh >> /home/mino/tasi-exec/cleanup.log 2>&1

# Integrity monitor — hourly checksum check for ASK files
0 * * * * cd /home/mino/tasi-exec && ./.integrity_monitor.sh >> /tmp/integrity_check.log 2>&1
```

## Entry Details

| Schedule | Script | Purpose | Logs |
|----------|--------|---------|------|
| `*/5 * * * *` | `derayah_refresh_cron.sh` | SSO token refresh, auto-recovery | `refresh_cron.log` |
| `55 9 * * 0-4` | `cleanup_stand_down.sh` | Remove stale stand_down files | `cleanup.log` |
| `0 * * * *` | `.integrity_monitor.sh` | Checksum verification for ASK files | `/tmp/integrity_check.log` |

## Notes

- OpenClaw cron jobs are stored in OpenClaw database, not in traditional crontab
- To view: `openclaw cron list`
- To edit: `openclaw cron update`
- Traditional crontab entries are stored in system cron (not in repo)
- To edit traditional crontab: `crontab -e`

## v4.12 Changes (2026-06-22)

- **Evaluator schedule updated:** Now runs at 10:10, 10:40, 11:10, 11:40, 12:10, 12:40, 13:10, 13:40, 14:10 (was 10:15, 10:45, etc.)
- **Mid-screen announcements fixed:** 10:30 and 12:00 screens now explicitly read picks file and send Telegram announcements (previously expected script to announce, but script doesn't send Telegram messages)
- **Evaluator v4.12 deployed:** Two-gate system with WS data as primary price source

---

*Last updated: 2026-06-22 14:25 KSA*

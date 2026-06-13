# TASI Crontab Reference

**Last updated:** 2026-06-13

## Active Entries

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

- Crontab is stored in system cron (not in repo)
- To edit: `crontab -e`
- To view: `crontab -l`
- 5-min cron change applied 2026-06-10 (was 15-min)

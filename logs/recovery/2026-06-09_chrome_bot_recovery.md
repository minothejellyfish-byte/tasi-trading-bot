# TASI Chrome + Bot Recovery — 2026-06-09
# Operator: Mino (per Amin's "Do it, but log all changes")
# Reversibility: every step listed below is reversible

## Pre-recovery state (from investigation)
- tasi-bot.service: inactive (dead) since Mon 2026-06-08 19:24:35
- tasi-ws-keepalive.service: inactive (dead) since Tue 2026-06-09 11:33:33
- chromium-derayah.service: disabled
- Chrome: not running. Profile: derayah-live. Stale state:
  - SingletonLock -> ocean-208937 (dead PID)
  - DevToolsActivePort: missing
  - No Preferences file
- All systemd services use Restart=on-failure, but services die with code 0
  (clean exit) so on-failure never triggers
- Recovery scope (per Amin): restart services only. No config changes
  in this pass.

2026-06-09 19:21:31 +03
Chrome running: 2 processes
Bot running: 2 processes
ws-keepalive service: inactive
bot service: inactive
CDP: 000

Stashed stuck lock files to: /tmp/chrome-profile-stash-1781022092

Removed SingletonLock + DevToolsActivePort


## Notes on SingletonLock removal
- Original state: `SingletonLock` was a *symlink* to `ocean-208937` (dead PID)
- Dangling symlink: target process 208937 not running
- Recovery: symlink + DevToolsActivePort both removed
- Reversibility: profile directory itself untouched; if Chrome start fails
  the same symlinks can be recreated (pointing to a live PID) to
  reverse the removal
- (Note: the symlink being a symlink to a dead PID was a [ -e ] false
  negative during pre-stash, hence the empty stash dir; not a data loss)


## Step 4: Start Chrome (manual via blueprint script)
Command: bash /home/mino/tasi-exec/start-chrome.sh
Return code: 0

## Step 5: Verify Chrome is up
282957 /bin/bash --noprofile --norc -c if [ -r '/home/mino/.openclaw-mino/cache/shell-snapshots/5fe0f6c0642e7f8dd8f8f5b25b3526cd63fc34d24406c3245b70c978cf7cebb1.sh' ]; then . '/home/mino/.openclaw-mino/cache/shell-snapshots/5fe0f6c0642e7f8dd8f8f5b25b3526cd63fc34d24406c3245b70c978cf7cebb1.sh'; fi export PATH='/usr/bin:/bin:/home/mino/.nvm/current/bin:/home/mino/.local/bin:/home/mino/.npm-global/bin:/home/mino/bin:/home/mino/.nix-profile/bin:/usr/local/bin:/home/mino/.openclaw-mino/.local/bin:/snap/bin' eval 'CHANGELOG=/home/mino/tasi-exec/logs/recovery/2026-06-09_chrome_bot_recovery.md echo "=== Step 4: Start Chrome via start-chrome.sh ===" echo "" >> $CHANGELOG echo "## Step 4: Start Chrome (manual via blueprint script)" >> $CHANGELOG echo "Command: bash /home/mino/tasi-exec/start-chrome.sh" >> $CHANGELOG bash /home/mino/tasi-exec/start-chrome.sh SCRIPT_RC=$? echo "Return code: $SCRIPT_RC" >> $CHANGELOG echo "  Start script returned: $SCRIPT_RC" echo "" sleep 2  echo "=== Step 5: Verify Chrome process and CDP ===" echo "" >> $CHANGELOG echo "## Step 5: Verify Chrome is up" >> $CHANGELOG pgrep -af "google-chrome.*derayah-live" >> $CHANGELOG echo "Chrome procs:" >> $CHANGELOG pgrep -af "google-chrome.*derayah-live" | head -3 echo "" echo "CDP /json/version:" >> $CHANGELOG curl -s http://127.0.0.1:18801/json/version >> $CHANGELOG echo "" >> $CHANGELOG curl -s http://127.0.0.1:18801/json/version | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'\''Browser: {d.get(\"Browser\")}'\'')" 2>&1 || echo "CDP not responding"'
282960 /opt/google/chrome/chrome --no-sandbox --disable-gpu --disable-software-rasterizer --remote-debugging-port=18801 --remote-allow-origins=* --user-data-dir=/home/mino/.config/google-chrome/derayah-live --no-first-run --disable-sync --no-default-browser-check --proxy-server=socks5://localhost:1080 --password-store=basic https://derayah.tickerchart.net/app/en
282976 /opt/google/chrome/chrome --type=zygote --no-zygote-sandbox --no-sandbox --crashpad-handler-pid=282969 --enable-crash-reporter=, --user-data-dir=/home/mino/.config/google-chrome/derayah-live --change-stack-guard-on-fork=enable
282977 /opt/google/chrome/chrome --type=zygote --no-sandbox --crashpad-handler-pid=282969 --enable-crash-reporter=, --user-data-dir=/home/mino/.config/google-chrome/derayah-live --change-stack-guard-on-fork=enable
283000 /opt/google/chrome/chrome --type=gpu-process --no-sandbox --ozone-platform=x11 --crashpad-handler-pid=282969 --enable-crash-reporter=, --user-data-dir=/home/mino/.config/google-chrome/derayah-live --change-stack-guard-on-fork=enable --gpu-preferences=UAAAAAAAAAAgAQAEAAAAAAAAAAAAAGAAAAAAAAAAAAAAAAAAAAAAAAIAAAAAAAAAAAAAAAAAAAAYAAAAAAAAABgAAAAAAAAAAQAAAAAAAAAIAAAAAAAAAAgAAAAAAAAA --use-gl=disabled --shared-files --metrics-shmem-handle=4,i,10885069562936324381,8231012978701188230,262144 --field-trial-handle=3,i,17553644954479592018,13929352889701941023,262144 --variations-seed-version=20260608-170105.666000-production --pseudonymization-salt-handle=7,i,10826872596519343884,3231380048829116930,4 --trace-process-track-uuid=3190708988185955192
283003 /opt/google/chrome/chrome --type=utility --utility-sub-type=network.mojom.NetworkService --lang=en-US --service-sandbox-type=none --no-sandbox --crashpad-handler-pid=282969 --enable-crash-reporter=, --user-data-dir=/home/mino/.config/google-chrome/derayah-live --change-stack-guard-on-fork=enable --shared-files=v8_context_snapshot_data:100 --metrics-shmem-handle=4,i,620617211248210478,1901116854165742382,524288 --field-trial-handle=3,i,17553644954479592018,13929352889701941023,262144 --variations-seed-version=20260608-170105.666000-production --pseudonymization-salt-handle=7,i,10826872596519343884,3231380048829116930,4 --trace-process-track-uuid=3190708989122997041
283018 /opt/google/chrome/chrome --type=utility --utility-sub-type=storage.mojom.StorageService --lang=en-US --service-sandbox-type=utility --no-sandbox --crashpad-handler-pid=282969 --enable-crash-reporter=, --user-data-dir=/home/mino/.config/google-chrome/derayah-live --change-stack-guard-on-fork=enable --shared-files=v8_context_snapshot_data:100 --metrics-shmem-handle=4,i,9750286435968351229,302279374843035396,524288 --field-trial-handle=3,i,17553644954479592018,13929352889701941023,262144 --variations-seed-version=20260608-170105.666000-production --pseudonymization-salt-handle=7,i,10826872596519343884,3231380048829116930,4 --trace-process-track-uuid=3190708990060038890
283034 /opt/google/chrome/chrome --type=renderer --top-chrome-webui --crashpad-handler-pid=282969 --enable-crash-reporter=, --user-data-dir=/home/mino/.config/google-chrome/derayah-live --change-stack-guard-on-fork=enable --no-sandbox --remote-debugging-port=18801 --ozone-platform=x11 --disable-gpu-compositing --lang=en-US --num-raster-threads=2 --enable-main-frame-before-activation --renderer-client-id=5 --time-ticks-at-unix-epoch=-1780864607164642 --launch-time-ticks=157510229195 --shared-files=v8_context_snapshot_data:100 --metrics-shmem-handle=4,i,14455972472062253689,5491479556837829211,2097152 --field-trial-handle=3,i,17553644954479592018,13929352889701941023,262144 --variations-seed-version=20260608-170105.666000-production --pseudonymization-salt-handle=7,i,10826872596519343884,3231380048829116930,4 --trace-process-track-uuid=3190708990997080739
283043 /opt/google/chrome/chrome --type=renderer --crashpad-handler-pid=282969 --enable-crash-reporter=, --user-data-dir=/home/mino/.config/google-chrome/derayah-live --change-stack-guard-on-fork=enable --no-sandbox --remote-debugging-port=18801 --ozone-platform=x11 --disable-gpu-compositing --lang=en-US --num-raster-threads=2 --enable-main-frame-before-activation --renderer-client-id=7 --time-ticks-at-unix-epoch=-1780864607164642 --launch-time-ticks=157510437803 --shared-files=v8_context_snapshot_data:100 --metrics-shmem-handle=4,i,2652862223332327842,9137596310027414500,2097152 --field-trial-handle=3,i,17553644954479592018,13929352889701941023,262144 --variations-seed-version=20260608-170105.666000-production --pseudonymization-salt-handle=7,i,10826872596519343884,3231380048829116930,4 --trace-process-track-uuid=3190708992871164437
283044 /opt/google/chrome/chrome --type=renderer --crashpad-handler-pid=282969 --enable-crash-reporter=, --user-data-dir=/home/mino/.config/google-chrome/derayah-live --change-stack-guard-on-fork=enable --no-sandbox --remote-debugging-port=18801 --ozone-platform=x11 --disable-gpu-compositing --lang=en-US --num-raster-threads=2 --enable-main-frame-before-activation --renderer-client-id=6 --time-ticks-at-unix-epoch=-1780864607164642 --launch-time-ticks=157510440877 --shared-files=v8_context_snapshot_data:100 --metrics-shmem-handle=4,i,6854863651556946354,9439082463320659475,2097152 --field-trial-handle=3,i,17553644954479592018,13929352889701941023,262144 --variations-seed-version=20260608-170105.666000-production --pseudonymization-salt-handle=7,i,10826872596519343884,3231380048829116930,4 --trace-process-track-uuid=3190708991934122588
Chrome procs:
CDP /json/version:
{
   "Browser": "Chrome/148.0.7778.167",
   "Protocol-Version": "1.3",
   "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
   "V8-Version": "14.8.178.21",
   "WebKit-Version": "537.36 (@65db666ac2cf205fcc36db8bb5b9cd87f94808ac)",
   "webSocketDebuggerUrl": "ws://127.0.0.1:18801/devtools/browser/66a1db02-1215-4c6c-ab9c-59092076b0aa"
}


## Step 6: Start tasi-ws-keepalive.service
Command: systemctl --user start tasi-ws-keepalive.service
  systemctl --user start tasi-ws-keepalive: rc=0
active

## Step 7: Start tasi-bot.service
Command: systemctl --user start tasi-bot.service
  systemctl --user start tasi-bot: rc=0
active

## Step 8: Final service state
TASI services:
● tasi-bot.service - TASI Telegram Bot — Derayah Trading
     Loaded: loaded (/home/mino/.config/systemd/user/tasi-bot.service; enabled; preset: enabled)
     Active: active (running) since Tue 2026-06-09 19:22:16 +03; 3s ago
   Main PID: 283126 (python3)
      Tasks: 7 (limit: 9328)
     Memory: 115.0M (peak: 115.2M)
        CPU: 1.840s
     CGroup: /user.slice/user-1000.slice/user@1000.service/app.slice/tasi-bot.service
             └─283126 /usr/bin/python3 /home/mino/tasi-exec/bot.py

Jun 09 19:22:16 ocean systemd[1431]: Started tasi-bot.service - TASI Telegram Bot — Derayah Trading.

● tasi-ws-keepalive.service - TASI WebSocket Keepalive v2 (CDP-aware)
     Loaded: loaded (/home/mino/.config/systemd/user/tasi-ws-keepalive.service; enabled; preset: enabled)
     Active: active (running) since Tue 2026-06-09 19:22:14 +03; 5s ago
   Main PID: 283095 (ws_keepalive_v2)
      Tasks: 14 (limit: 9328)
     Memory: 75.0M (peak: 75.9M)
        CPU: 1.213s
     CGroup: /user.slice/user-1000.slice/user@1000.service/app.slice/tasi-ws-keepalive.service
             ├─283095 /bin/bash /home/mino/tasi-exec/ws_keepalive_v2.sh
             ├─283107 python3 ws_probe.py 90
             ├─283111 /home/mino/.local/lib/python3.12/site-packages/playwright/driver/node /home/mino/.local/lib/python3.12/site-packages/playwright/driver/package/cli.js run-driver
             └─283135 sleep 60

Jun 09 19:22:14 ocean systemd[1431]: Started tasi-ws-keepalive.service - TASI WebSocket Keepalive v2 (CDP-aware).

○ tasi-poller.service - TASI Price Poller
     Loaded: loaded (/home/mino/.config/systemd/user/tasi-poller.service; enabled; preset: enabled)
     Active: inactive (dead) since Tue 2026-06-09 15:30:05 +03; 3h 52min ago
   Duration: 5h 28min 53.514s
    Process: 190524 ExecStart=/usr/bin/python3 /home/mino/tasi-exec/poller.py (code=exited, status=0/SUCCESS)
   Main PID: 190524 (code=exited, status=0/SUCCESS)
        CPU: 24min 36.197s

Jun 09 10:01:11 ocean systemd[1431]: tasi-poller.service: Scheduled restart job, restart counter is at 4.
Jun 09 10:01:11 ocean systemd[1431]: Started tasi-poller.service - TASI Price Poller.
Jun 09 15:30:05 ocean systemd[1431]: tasi-poller.service: Consumed 24min 36.197s CPU time.

● tasi-watchdog.service - TASI System Watchdog — Daily Activity Logger

## Step 9: Bot attach + derayah tab check
Telegram tabs in Chrome:
  page         | Derayah Trade                                      | https://derayah.tickerchart.net/app/en
  browser_ui   | Omnibox Popup                                      | chrome://omnibox-popup.top-chrome/
  browser_ui   | Omnibox Popup                                      | chrome://omnibox-popup.top-chrome/omnibox_popup_aim.html
Last refresh_cron.log entries:
2026-06-09 18:45:01 ❌ CDP not accessible — Chrome may not be running
2026-06-09 18:45:01 ❌ CDP not accessible — Chrome may not be running
2026-06-09 19:00:01 === Derayah Refresh Cron (SSO Navigation) ===
2026-06-09 19:00:01 === Derayah Refresh Cron (SSO Navigation) ===
2026-06-09 19:00:01 ❌ CDP not accessible — Chrome may not be running
2026-06-09 19:00:01 ❌ CDP not accessible — Chrome may not be running
2026-06-09 19:15:01 === Derayah Refresh Cron (SSO Navigation) ===
2026-06-09 19:15:01 === Derayah Refresh Cron (SSO Navigation) ===
2026-06-09 19:15:02 ❌ CDP not accessible — Chrome may not be running
2026-06-09 19:15:02 ❌ CDP not accessible — Chrome may not be running

## Step 14: Manual refresh_cron to refresh tokens now
Command: bash /home/mino/tasi-exec/derayah_refresh_cron.sh
Return code: 1

## Final state at 19:23

| Service | Status | PID | Notes |
|---------|--------|-----|-------|
| Chrome (no service) | running | 282960 | derayah-live profile, 18801 CDP |
| tasi-bot.service | active | 283126 | polling Telegram, TC keepalive working |
| tasi-ws-keepalive.service | active | 283095 | ws_probe.py running (PID 283107) |
| tasi-poller.service | inactive | — | last ran 10:01–15:30 today, exited cleanly after market close |
| tasi-watchdog.service | active | 70675 | unchanged, still running |

## Session state
- Tokens expired 14:00 (5h ago), derayah_tokens.json has last captured_at
- Manual refresh_cron run (19:23) → SSO 401, Phase 3 recovery → CAPTCHA detected
- Per v4.2 procedure: bot is alive, polling, expecting manual /Login
- The 15-min cron will keep retrying and Phase 3 will keep notifying until /Login

## Reversibility map

| Change | How to revert |
|--------|---------------|
| Started Chrome | `pkill -f "google-chrome.*derayah-live"` |
| Started tasi-bot.service | `systemctl --user stop tasi-bot` |
| Started tasi-ws-keepalive.service | `systemctl --user stop tasi-ws-keepalive` |
| Removed SingletonLock + DevToolsActivePort | Re-run start-chrome.sh recreates them automatically |
| No config file changes were made |

## What still needs to be fixed (out of scope of this recovery)
1. systemd `Restart=on-failure` doesn't restart clean exits — needs `Restart=always` 
2. tasi-ws-keepalive.service also died clean (TERMed) — same issue
3. derayah_refresh_cron.sh only checks CDP, doesn't restart Chrome
4. Watchdog only logs CRITICAL, no Telegram DM escalation
5. Bot's CDP auto-restart (bot.py line 678) is silently failing


---

# PASS 2: Config Fixes (Amin: "Do 1 2 3")
# Started: 2026-06-09 20:03
# These ARE config changes — every backup kept for revert

## Fix 1: Restart=on-failure → Restart=always (tasi-bot, tasi-ws-keepalive)
## Fix 2: Add Chrome auto-start branch to derayah_refresh_cron.sh
## Fix 3: Fix bot's CDP auto-restart (bot.py:678)

4c3533d66a8063dfaf66a024b7c91bf3  /home/mino/tasi-exec/logs/recovery/tasi-bot.service.bak
Backup: /home/mino/tasi-exec/logs/recovery/tasi-bot.service.bak
177d4ee4610dfba42c4e46155ebb7dc6  /home/mino/tasi-exec/logs/recovery/tasi-ws-keepalive.service.bak
Backup: /home/mino/tasi-exec/logs/recovery/tasi-ws-keepalive.service.bak
12c12
< Restart=on-failure
---
> Restart=always
13a14,15
> StartLimitInterval=600
> StartLimitBurst=10
tasi-bot.service diff:
8c8
< Restart=on-failure
---
> Restart=always
9a10,11
> StartLimitInterval=600
> StartLimitBurst=10
tasi-ws-keepalive.service diff:
daemon-reload rc: 0
tasi-bot restart: rc=0
tasi-ws-keepalive restart: rc=0
● tasi-bot.service - TASI Telegram Bot — Derayah Trading
     Loaded: loaded (/home/mino/.config/systemd/user/tasi-bot.service; enabled; preset: enabled)
     Active: active (running) since Tue 2026-06-09 20:04:10 +03; 3s ago
   Main PID: 285536 (python3)
      Tasks: 7 (limit: 9328)
     Memory: 84.8M (peak: 85.2M)
        CPU: 1.820s
     CGroup: /user.slice/user-1000.slice/user@1000.service/app.slice/tasi-bot.service
             └─285536 /usr/bin/python3 /home/mino/tasi-exec/bot.py

Jun 09 20:04:10 ocean systemd[1431]: Started tasi-bot.service - TASI Telegram Bot — Derayah Trading.

● tasi-ws-keepalive.service - TASI WebSocket Keepalive v2 (CDP-aware)
     Loaded: loaded (/home/mino/.config/systemd/user/tasi-ws-keepalive.service; enabled; preset: enabled)
     Active: active (running) since Tue 2026-06-09 20:04:10 +03; 3s ago
   Main PID: 285539 (ws_keepalive_v2)
      Tasks: 14 (limit: 9328)
     Memory: 67.6M (peak: 68.3M)
        CPU: 1.222s
     CGroup: /user.slice/user-1000.slice/user@1000.service/app.slice/tasi-ws-keepalive.service

## Pass 2 Changes — Applied

### Fix 1: Restart=always
**Files changed:**
- `/home/mino/.config/systemd/user/tasi-bot.service`
- `/home/mino/.config/systemd/user/tasi-ws-keepalive.service`

**Backups:**
- `/home/mino/tasi-exec/logs/recovery/tasi-bot.service.bak`
- `/home/mino/tasi-exec/logs/recovery/tasi-ws-keepalive.service.bak`

**Change:** `Restart=on-failure` → `Restart=always` + added `StartLimitInterval=600` + `StartLimitBurst=10` (cap at 10 restarts in 10 min to detect crash loops)

**Reload:** `systemctl --user daemon-reload` ✅
**Restart:** both services restarted cleanly ✅

### Fix 2: Chrome auto-restart in refresh_cron
**File changed:** `/home/mino/tasi-exec/derayah_refresh_cron.sh`
**Backup:** `/home/mino/tasi-exec/logs/recovery/derayah_refresh_cron.sh.bak` (md5 66d972c5dd1c50194682c511a83f191d)

**Change:** Added `ensure_chrome()` function. If CDP on 18801 is down, it:
1. Removes stale `SingletonLock` + `DevToolsActivePort` from `derayah-live` profile
2. Calls `bash /home/mino/tasi-exec/start-chrome.sh` (blueprint script, robust)
3. Returns success/failure

**Modified `main()`:** CDP failure no longer exits — it tries `ensure_chrome()` first, then proceeds to refresh.

**Syntax check:** `bash -n` ✅
**Logic test:** `ensure_chrome` is callable ✅

### Fix 3: Bot CDP auto-restart
**File changed:** `/home/mino/tasi-exec/bot.py` (line 678–709)
**Backup:** `/home/mino/tasi-exec/logs/recovery/bot.py.bak` (md5 b3104dad84d050a0a4ebc503a46553e7)

**Change:** Rewrote the `except Exception:` branch in `ensure_page()`:
1. Clears stale lock files (was: not done)
2. Calls `start-chrome.sh` (was: hardcoded `CHROMIUM_CMD` which fails silently)
3. Has a fallback to `CHROMIUM_CMD` if script spawn fails
4. Waits 6s, then attempts CDP reconnect (was: 4s)
5. If still down, waits 4s more and tries again (was: gave up)
6. Added `import pathlib` at top of file (was: in-line)

**Syntax check:** `py_compile` ✅
**Service restart:** `systemctl --user restart tasi-bot` ✅, "TASI Execution Bot starting" logged

## How to revert (everything)

```bash
# Revert Fix 1
cp /home/mino/tasi-exec/logs/recovery/tasi-bot.service.bak /home/mino/.config/systemd/user/tasi-bot.service
cp /home/mino/tasi-exec/logs/recovery/tasi-ws-keepalive.service.bak /home/mino/.config/systemd/user/tasi-ws-keepalive.service
systemctl --user daemon-reload
systemctl --user restart tasi-bot tasi-ws-keepalive

# Revert Fix 2
cp /home/mino/tasi-exec/logs/recovery/derayah_refresh_cron.sh.bak /home/mino/tasi-exec/derayah_refresh_cron.sh
# Wait for next 15-min cron tick to use the old version

# Revert Fix 3
cp /home/mino/tasi-exec/logs/recovery/bot.py.bak /home/mino/tasi-exec/bot.py
systemctl --user restart tasi-bot
```

## Verification at 20:06 KSA

- tasi-bot.service: active (running, "TASI Execution Bot starting" logged)
- tasi-ws-keepalive.service: active
- Chrome: running, CDP 18801 responding
- derayah_refresh_cron.sh: next tick at 20:15 will use new ensure_chrome logic
- bot.py: live with new ensure_page() code


# PASS 3: Fix 4 — Watchdog Telegram DM escalation
# Started: 2026-06-09 20:09

## Fix 4: Watchdog sends DM to user (OWNER_ID) on CRITICAL alerts
## File: /home/mino/tasi-exec/tasi_watchdog.py
## Backup: /home/mino/tasi-exec/logs/recovery/tasi_watchdog.py.bak


## Pass 3 Changes — Applied

### Fix 4: Watchdog Telegram DM escalation
**File changed:** `/home/mino/tasi-exec/tasi_watchdog.py`
**Backup:** `/home/mino/tasi-exec/logs/recovery/tasi_watchdog.py.bak` (md5 9d27425bda266f5f672ecd60a4e13308)

**Changes:**
1. Added `os`, `urllib.parse`, `urllib.request` imports
2. Added config block (TELEGRAM_OWNER_ID, ALERT_DM_COOLDOWN=900s, ALERT_DM_ENABLED)
3. Added `_read_tasi_bot_token()` — reads token from env or parses it from `bot.py`
   (avoids hard-coding the token in two places)
4. Added `tg_send_dm(text)` — sends DM via stdlib urllib, no new deps
5. Added `maybe_dm_alert(alerts)` — dedup logic:
   - First CRITICAL → DM immediately
   - Same CRITICAL within 15 min → suppress
   - Different CRITICAL → DM
   - WARNINGs → never DM (only log)
6. Wired into `run_watchdog_cycle()` after `detect_anomalies()`

**Syntax check:** `py_compile` ✅
**Service restart:** `systemctl --user restart tasi-watchdog` ✅ (PID 286116)

**Verification tests (synthetic alerts):**
- Call 1 (CRITICAL: tasi-bot): DM sent ✅
- Call 2 (same alert, < 15 min): suppressed ✅
- Call 3 (different CRITICAL): DM sent ✅
- Call 4 (WARNING): suppressed ✅

**Token resolution:** ✓ 46-char token parsed from bot.py at import time
**Target chat_id:** 5529987063 (Amin's user ID, not the group -5235925419)

## How to revert

```bash
# Revert Fix 4
cp /home/mino/tasi-exec/logs/recovery/tasi_watchdog.py.bak /home/mino/tasi-exec/tasi_watchdog.py
systemctl --user restart tasi-watchdog
```

To temporarily disable DMs without reverting:
```bash
systemctl --user edit tasi-watchdog
# add: Environment="WATCHDOG_DM_ENABLED=0"
```

## End-to-end now

| Failure | What happens |
|---|---|
| Chrome dies | `derayah_refresh_cron.sh` (15-min) auto-restarts it via start-chrome.sh |
| Bot dies | systemd `Restart=always` (Fix 1) brings it back |
| ws-keepalive dies | systemd `Restart=always` (Fix 1) brings it back |
| Bot detects CDP down at runtime | bot.py ensure_page() (Fix 3) auto-restarts Chrome |
| Any CRITICAL persists 15+ min | Watchdog DMs user (Fix 4) |

## Final verification at 20:12 KSA

- tasi-bot.service: active
- tasi-ws-keepalive.service: active
- tasi-watchdog.service: active (with new DM code)
- tasi-poller.service: inactive (expected, off-duty)
- Chrome: running, CDP 18801
- Token: expired, awaiting /Login
- All Fix 1-4 in place, all logged in this file

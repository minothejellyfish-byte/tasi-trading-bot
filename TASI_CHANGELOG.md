# TASI Changelog — 2026-06-15

## [FIX] Bot Crash Recovery

**Date:** 2026-06-15 07:45 KSA
**Type:** ASK (Critical Fix)
**Files:** `bot.py`, `tasi-bot.service`

### Problem
- Bot crashed on Jun 14 19:19 due to DNS failure
- Systemd entered crash loop (status 1/FAILURE)
- Bot code had indentation bug: `/Fund` and `/Withdraw` functions inside `main()`

### Solution
1. Restored bot from pre-fund commit (9d68cde) — working base
2. Fixed systemd service file:
   - Added `ExecStartPre` DNS wait loop
   - Added `RestartSec=60`, `StartLimitBurst=10`
   - Fixed environment variables
3. Properly re-added `/Fund` and `/Withdraw`:
   - Functions placed OUTSIDE `main()` at module level
   - Command handlers placed INSIDE `main()` correctly

### Result
- Bot running via systemd with auto-restart
- All Jun 14 features restored
- Fund/Withdraw commands working

---

## [SHOW] v4.5 Regime-Aware Exit Strategy

**Date:** 2026-06-15 05:53 KSA
**Type:** SHOW
**File:** `poller.py`

### Changes
- Phase 1: Market Open Cooldown (10:00-10:15 blocked)
- Phase 2: VWAP Exit Control (disabled in TRENDING)
- Phase 3: 1-Minute Recovery Score (15 candles)
- Phase 4: Regime-Aware Exit Parameters (dynamic thresholds)
- Phase 5: Time/Trail Updates (adaptive stops)
- Phase 6: Entry VWAP Direction Filter (require rising VWAP)
- Phase 7: Adaptive Tiers (thresholds by regime)

### Key Values
- DEF profit target: +0.8% (covers Derayah fees)
- DEF hard stop: -5%
- DEF min hold: 10 min

---

*Updated: 2026-06-15 08:05 KSA*

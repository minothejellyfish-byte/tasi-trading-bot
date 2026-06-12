#!/usr/bin/env python3
"""
TASI System Watchdog — Daily Activity Logger
Runs from 09:30 to 16:30 KSA (30 min before first job, 30 min after last job)
Logs: all cron events, bot actions, system state, errors
Purpose: Post-mortem investigation — when something breaks, read this log
"""

import json
import os
import subprocess
import time
import signal
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
import pytz

RIYADH = pytz.timezone("Asia/Riyadh")
BASE_DIR = Path("/home/mino/tasi-exec")
WATCHDOG_LOG = BASE_DIR / "logs" / "watchdog.log"
STATE_LOG = BASE_DIR / "logs" / "watchdog_state.jsonl"
PID_FILE = BASE_DIR / ".watchdog.pid"

# Telegram DM escalation (Fix 4, 2026-06-09)
# Notify OWNER_ID on CRITICAL alerts via direct message (not group).
# Same token as bot.py (TASI Execution Bot) — reuses the same bot.
TELEGRAM_OWNER_ID  = 5529987063      # A A's Telegram user ID
ALERT_DM_COOLDOWN  = 900             # seconds between repeated DMs for same alert (15 min)
ALERT_DM_ENABLED   = os.getenv("WATCHDOG_DM_ENABLED", "1") == "1"  # set to "0" to disable
# Token priority: env var → first BOT_TOKEN line in bot.py → empty
def _read_tasi_bot_token() -> str:
    env_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        bot_py = (BASE_DIR / "bot.py").read_text()
        for line in bot_py.splitlines():
            if "BOT_TOKEN" in line and "=" in line and "os.getenv" not in line:
                # extract first quoted string after =
                after = line.split("=", 1)[1]
                for q in ('"', "'"):
                    if q in after:
                        return after.split(q)[1] if after.count(q) >= 2 else ""
        # Fallback: parse os.getenv line for default
        for line in bot_py.splitlines():
            if "BOT_TOKEN" in line and "os.getenv" in line:
                import re
                m = re.search(r'"([^"]+)"\s*\)', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return ""
TELEGRAM_BOT_TOKEN = _read_tasi_bot_token()
# Per-alert last-DM-time tracker (in-memory; resets on restart)
_alert_last_dm: dict = {}

# Ensure log directory exists
WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)

# ─── Config ──────────────────────────────────────────────────────────

FIRST_JOB_TIME = "09:30"  # 20 min before pre-market screener
LAST_JOB_TIME  = "16:30"  # 30 min after US daily report check

CHECK_INTERVAL = 30  # seconds between checks

COMPONENTS = {
    "tasi-bot": "ps aux | grep 'python.*bot.py' | grep -v grep",
    "us-bot": "ps aux | grep 'python.*us_bot.py' | grep -v grep",
    "ws-keepalive": "systemctl --user is-active tasi-ws-keepalive",
    "cdp-chrome": "curl -s --max-time 2 http://127.0.0.1:18801/json/version > /dev/null",
    "poller": "pgrep -f 'python.*poller.py'",
    "us-poller": "pgrep -f 'python.*us_poller.py'",
}

KEY_LOGS = [
    "/home/mino/tasi-exec/logs/bot.log",
    "/home/mino/tasi-exec/exec.log",
    "/home/mino/us-exec/us_exec.log",
]

# ─── Tracked Files (JSON + TXT) ────────────────────────────────────

TRACKED_FILES = [
    # Trading state files
    ("picks", "/home/mino/tasi-exec/picks.json"),
    ("positions", "/home/mino/tasi-exec/positions.json"),
    ("capital", "/home/mino/tasi-exec/capital.json"),
    ("regime", "/home/mino/tasi-exec/regime.json"),
    ("learning", "/home/mino/tasi-exec/learning.json"),
    ("blocked_symbols", "/home/mino/tasi-exec/blocked_symbols.txt"),
    ("stand_down", "/home/mino/tasi-exec/stand_down"),
    # Pick archives
    ("picks_1030", "/home/mino/tasi-exec/picks_1030.json"),
    ("picks_1200", "/home/mino/tasi-exec/picks_1200.json"),
    ("picks_1330", "/home/mino/tasi-exec/picks_1330.json"),
    # WS data
    ("ws_frames", "/home/mino/tasi-exec/ws_frames.json"),
    ("ws_probe_log", "/home/mino/tasi-exec/ws_probe.log"),
    # Reports
    ("post_market_html", "/home/mino/tasi-exec/reports/post_market_{date}.html"),
    ("intelligent_analysis", "/home/mino/tasi-exec/intelligent_analysis_{date}.txt"),
]

# File state tracking for change detection
_file_mtimes: dict = {}

running = True


def log(msg: str, level: str = "INFO"):
    ts = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    with open(WATCHDOG_LOG, "a") as f:
        f.write(line + "\n")


def tg_send_dm(text: str) -> bool:
    """Send a DM to TELEGRAM_OWNER_ID via the TASI bot. Returns True on success.

    Uses urllib (stdlib) to avoid a `requests` import; watchdog is stdlib-only.
    """
    if not ALERT_DM_ENABLED:
        return False
    if not TELEGRAM_BOT_TOKEN:
        log("Telegram token unavailable — skipping DM", level="WARNING")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": TELEGRAM_OWNER_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            ok = resp.status == 200
            if not ok:
                log(f"Telegram DM send returned HTTP {resp.status}", level="WARNING")
            return ok
    except Exception as e:
        log(f"Telegram DM send failed: {e}", level="WARNING")
        return False


def maybe_dm_alert(alerts: list) -> None:
    """Send a Telegram DM to OWNER_ID for any CRITICAL alerts.

    Dedup rules:
      - First occurrence of a CRITICAL alert → DM immediately
      - Same alert within ALERT_DM_COOLDOWN → suppressed
      - All-CRITICAL summary DM every 15 min max
    """
    if not ALERT_DM_ENABLED:
        return
    critical = [a for a in alerts if a.startswith("CRITICAL")]
    if not critical:
        return

    now_ts = time.time()
    fresh = []
    for a in critical:
        last = _alert_last_dm.get(a, 0)
        if now_ts - last >= ALERT_DM_COOLDOWN:
            fresh.append(a)
            _alert_last_dm[a] = now_ts

    if not fresh:
        return

    body = (
        f"\U0001f6a8 TASI Watchdog \u2014 {len(fresh)} CRITICAL alert(s)\n\n"
        + "\n".join(f"\u2022 {a}" for a in fresh)
        + f"\n\nTime: {datetime.now(RIYADH).strftime('%Y-%m-%d %H:%M:%S %Z')}"
        + f"\nFull log: {WATCHDOG_LOG}"
    )
    if tg_send_dm(body):
        log(f"DM sent to owner ({len(fresh)} alerts)", level="INFO")
    else:
        log("DM send failed (see warnings above)", level="WARNING")


def save_state(state: dict):
    """Append structured state to JSONL for programmatic analysis."""
    with open(STATE_LOG, "a") as f:
        f.write(json.dumps(state) + "\n")


def check_component(name: str, cmd: str) -> tuple:
    """Run a check command, return (status, detail)."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return "OK", result.stdout.strip()[:100]
        else:
            return "FAIL", result.stderr.strip()[:200] or result.stdout.strip()[:200]
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "Command timed out after 5s"
    except Exception as e:
        return "ERROR", str(e)[:200]


def get_recent_log_lines(log_file: str, lines: int = 3) -> list:
    """Get last N lines from a log file."""
    try:
        if not Path(log_file).exists():
            return ["FILE_NOT_FOUND"]
        result = subprocess.run(
            ["tail", "-n", str(lines), log_file],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip().split("\n")
    except Exception as e:
        return [f"ERROR: {e}"]


def check_ws_health() -> dict:
    """Check WebSocket health via CDP and ws_probe status."""
    result = {
        "cdp_tabs": None,
        "ws_probe_running": False,
        "ws_probe_recent_lines": [],
        "ws_frames_size": 0,
        "ws_frames_growing": False,
    }
    
    # Check CDP tabs
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "3", "http://127.0.0.1:18801/json/list"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0 and r.stdout.strip():
            tabs = json.loads(r.stdout)
            tc_tabs = [t for t in tabs if "tickerchart" in t.get("url", "")]
            result["cdp_tabs"] = len(tc_tabs)
    except Exception:
        pass
    
    # Check ws_probe process
    try:
        r = subprocess.run("pgrep -f 'ws_probe.py'", shell=True, capture_output=True, timeout=2)
        result["ws_probe_running"] = r.returncode == 0
    except:
        pass
    
    # Get recent ws_probe log lines
    result["ws_probe_recent_lines"] = get_recent_log_lines("/home/mino/tasi-exec/ws_probe.log", lines=2)
    
    # Check ws_frames.json size
    ws_frames = Path("/home/mino/tasi-exec/ws_frames.json")
    if ws_frames.exists():
        result["ws_frames_size"] = ws_frames.stat().st_size
        # Compare with previous check
        prev_size = _file_mtimes.get("ws_frames_size", 0)
        result["ws_frames_growing"] = result["ws_frames_size"] > prev_size
        _file_mtimes["ws_frames_size"] = result["ws_frames_size"]
    
    return result


def check_tracked_files(date_str: str) -> list:
    """Check tracked files for existence, size, and modification time."""
    file_status = []
    
    for name, filepath_template in TRACKED_FILES:
        filepath = filepath_template.replace("{date}", date_str)
        path = Path(filepath)
        
        if not path.exists():
            file_status.append({
                "name": name,
                "path": filepath,
                "status": "MISSING",
                "size": 0,
                "mtime": None,
                "changed": False
            })
            continue
        
        stat = path.stat()
        size = stat.st_size
        mtime = stat.st_mtime
        
        # Check if changed since last watch
        prev_mtime = _file_mtimes.get(filepath)
        changed = prev_mtime is not None and mtime > prev_mtime
        _file_mtimes[filepath] = mtime
        
        file_status.append({
            "name": name,
            "path": filepath,
            "status": "OK",
            "size": size,
            "mtime": datetime.fromtimestamp(mtime, RIYADH).isoformat(),
            "changed": changed
        })
    
    return file_status


def check_execution_orders(date_str: str) -> dict:
    """Check exec.log for today's orders and their status."""
    result = {
        "buy_orders": 0,
        "sell_orders": 0,
        "errors": [],
        "last_order": None,
    }
    
    exec_log = Path("/home/mino/tasi-exec/exec.log")
    if not exec_log.exists():
        return result
    
    try:
        # Get today's lines from exec.log
        today_prefix = date_str
        r = subprocess.run(
            f"grep '^{today_prefix}' {exec_log} | tail -n 50",
            shell=True, capture_output=True, text=True, timeout=5
        )
        
        if r.stdout.strip():
            lines = r.stdout.strip().split("\n")
            for line in lines:
                if "BUY" in line.upper():
                    result["buy_orders"] += 1
                if "SELL" in line.upper():
                    result["sell_orders"] += 1
                if "ERROR" in line.upper() or "FAIL" in line.upper():
                    result["errors"].append(line[:200])
            
            if lines:
                result["last_order"] = lines[-1][:200]
    except Exception:
        pass
    
    return result


def check_poller_activity() -> dict:
    """Check poller.py recent activity from exec.log."""
    result = {
        "last_activity": None,
        "signals_generated": 0,
        "trades_executed": 0,
        "status": "UNKNOWN"
    }
    
    exec_log = Path("/home/mino/tasi-exec/exec.log")
    if not exec_log.exists():
        result["status"] = "NO_LOG"
        return result
    
    try:
        # Get last 20 lines mentioning poller activity
        r = subprocess.run(
            "tail -n 50 /home/mino/tasi-exec/exec.log | grep -i 'poller\\|signal\\|entry\\|exit\\|buy\\|sell' | tail -n 10",
            shell=True, capture_output=True, text=True, timeout=5
        )
        
        if r.stdout.strip():
            lines = r.stdout.strip().split("\n")
            result["last_activity"] = lines[-1][:200]
            result["status"] = "ACTIVE"
            
            for line in lines:
                if "signal" in line.lower():
                    result["signals_generated"] += 1
                if "buy" in line.lower() or "sell" in line.lower():
                    result["trades_executed"] += 1
        else:
            result["status"] = "IDLE"
    except Exception:
        result["status"] = "ERROR"
    
    return result


def check_all_components():
    """Check all tracked components and return state dict."""
    now = datetime.now(RIYADH)
    date_str = now.strftime("%Y-%m-%d")
    
    state = {
        "timestamp": datetime.now(RIYADH).isoformat(),
        "components": {},
        "log_snippets": {},
        "system": {},
        "ws_health": {},
        "tracked_files": [],
        "execution_orders": {},
        "poller_activity": {},
    }

    for name, cmd in COMPONENTS.items():
        status, detail = check_component(name, cmd)
        state["components"][name] = {"status": status, "detail": detail}

    for log_file in KEY_LOGS:
        log_name = Path(log_file).name
        state["log_snippets"][log_name] = get_recent_log_lines(log_file)

    # System metrics
    try:
        mem = subprocess.run("free -m | grep Mem", shell=True, capture_output=True, text=True, timeout=2)
        state["system"]["memory"] = mem.stdout.strip()
    except:
        state["system"]["memory"] = "UNAVAILABLE"

    try:
        disk = subprocess.run("df -h / | tail -1", shell=True, capture_output=True, text=True, timeout=2)
        state["system"]["disk"] = disk.stdout.strip()
    except:
        state["system"]["disk"] = "UNAVAILABLE"

    try:
        load = subprocess.run("uptime | awk -F'load average:' '{print $2}'", shell=True, capture_output=True, text=True, timeout=2)
        state["system"]["load"] = load.stdout.strip()
    except:
        state["system"]["load"] = "UNAVAILABLE"
    
    # NEW: WS health, tracked files, execution orders, poller activity
    state["ws_health"] = check_ws_health()
    state["tracked_files"] = check_tracked_files(date_str)
    state["execution_orders"] = check_execution_orders(date_str)
    state["poller_activity"] = check_poller_activity()

    return state


def log_components(state: dict):
    """Human-readable summary of component status."""
    log("--- Component Check ---")
    for name, data in state["components"].items():
        emoji = "✅" if data["status"] == "OK" else "❌"
        log(f"{emoji} {name}: {data['status']} — {data['detail'][:80]}")
    
    # WS Health
    ws = state.get("ws_health", {})
    if ws:
        log("--- WebSocket Health ---")
        tabs = ws.get("cdp_tabs", "?")
        probe = "✅" if ws.get("ws_probe_running") else "❌"
        growing = "📈" if ws.get("ws_frames_growing") else "⏸️"
        size_kb = ws.get("ws_frames_size", 0) / 1024
        log(f"CDP TC tabs: {tabs} | ws_probe: {probe} | ws_frames: {size_kb:.0f}KB {growing}")
    
    # Tracked Files
    files = state.get("tracked_files", [])
    if files:
        log("--- Tracked Files ---")
        changed_files = [f for f in files if f.get("changed")]
        missing_files = [f for f in files if f.get("status") == "MISSING"]
        
        if changed_files:
            log(f"📝 Changed: {', '.join(f['name'] for f in changed_files[:5])}")
        if missing_files:
            log(f"⚠️ Missing: {', '.join(f['name'] for f in missing_files[:5])}")
        if not changed_files and not missing_files:
            log("✅ All tracked files present (no recent changes)")
    
    # Execution Orders
    orders = state.get("execution_orders", {})
    if orders:
        log("--- Execution Orders ---")
        buys = orders.get("buy_orders", 0)
        sells = orders.get("sell_orders", 0)
        errors = orders.get("errors", [])
        log(f"BUY: {buys} | SELL: {sells} | Errors: {len(errors)}")
        if orders.get("last_order"):
            log(f"Last: {orders['last_order'][:100]}")
    
    # Poller Activity
    poller = state.get("poller_activity", {})
    if poller:
        log("--- Poller Activity ---")
        status = poller.get("status", "UNKNOWN")
        signals = poller.get("signals_generated", 0)
        trades = poller.get("trades_executed", 0)
        emoji = "✅" if status == "ACTIVE" else "⚠️" if status == "IDLE" else "❌"
        log(f"{emoji} Status: {status} | Signals: {signals} | Trades: {trades}")
        if poller.get("last_activity"):
            log(f"Last activity: {poller['last_activity'][:100]}")


def should_be_running(now: datetime = None) -> bool:
    """Return True if watchdog should be active (between FIRST and LAST job times)."""
    if now is None:
        now = datetime.now(RIYADH)

    # Convert times to minutes since midnight for comparison
    def time_to_minutes(t_str):
        h, m = map(int, t_str.split(":"))
        return h * 60 + m

    current_mins = now.hour * 60 + now.minute
    first_mins = time_to_minutes(FIRST_JOB_TIME)
    last_mins = time_to_minutes(LAST_JOB_TIME)

    # Handle case where last job is on next calendar day (not needed here — both same day)
    return first_mins <= current_mins <= last_mins


def detect_anomalies(state: dict) -> list:
    """Detect anomalies in current state and return list of alerts."""
    alerts = []

    # Critical components down
    critical = ["tasi-bot", "cdp-chrome"]
    for name in critical:
        if state["components"].get(name, {}).get("status") != "OK":
            alerts.append(f"CRITICAL: {name} is {state['components'][name]['status']}")

    # Memory pressure
    mem_str = state["system"].get("memory", "")
    try:
        mem_parts = mem_str.split()
        if len(mem_parts) >= 7:
            available_pct = int(mem_parts[6]) / int(mem_parts[1]) * 100
            if available_pct < 10:
                alerts.append(f"WARNING: Memory critically low ({available_pct:.1f}% available)")
            elif available_pct < 20:
                alerts.append(f"WARNING: Memory low ({available_pct:.1f}% available)")
    except:
        pass

    # Disk pressure
    disk_str = state["system"].get("disk", "")
    try:
        usage_pct = int(disk_str.split()[4].rstrip("%"))
        if usage_pct > 90:
            alerts.append(f"CRITICAL: Disk usage {usage_pct}%")
        elif usage_pct > 75:
            alerts.append(f"WARNING: Disk usage {usage_pct}%")
    except:
        pass
    
    # NEW: WS health anomalies
    ws = state.get("ws_health", {})
    if ws.get("cdp_tabs") is not None and ws["cdp_tabs"] == 0:
        alerts.append("CRITICAL: No TickerChart tabs in CDP")
    if ws.get("ws_probe_running") is False:
        alerts.append("WARNING: ws_probe not running")
    
    # NEW: Missing critical files
    files = state.get("tracked_files", [])
    critical_files = ["picks", "positions", "capital"]
    for f in files:
        if f["name"] in critical_files and f["status"] == "MISSING":
            alerts.append(f"CRITICAL: {f['name']} file missing")
    
    # NEW: Execution errors
    orders = state.get("execution_orders", {})
    if orders.get("errors"):
        alerts.append(f"ALERT: {len(orders['errors'])} execution errors in exec.log")
    
    # NEW: Poller idle
    poller = state.get("poller_activity", {})
    if poller.get("status") == "IDLE" and state["components"].get("poller", {}).get("status") == "OK":
        alerts.append("WARNING: Poller running but no recent activity")

    return alerts


def run_watchdog_cycle():
    """Single watchdog cycle: check, log, alert."""
    now = datetime.now(RIYADH)
    time_str = now.strftime("%H:%M:%S")

    state = check_all_components()
    save_state(state)

    # Log every 5 minutes (every 10th cycle at 30s interval)
    if int(time.time()) % 300 < CHECK_INTERVAL:
        log_components(state)

    # Detect anomalies
    alerts = detect_anomalies(state)
    if alerts:
        for alert in alerts:
            log(alert, level="ALERT")
        # Fix 4: DM the owner for CRITICAL alerts
        maybe_dm_alert(alerts)

    # Check for log errors (grep last 10 lines for ERROR/FAIL/CRITICAL)
    for log_file in KEY_LOGS:
        if Path(log_file).exists():
            try:
                result = subprocess.run(
                    f"tail -n 10 {log_file} | grep -i 'error\\|fail\\|critical\\|exception' | tail -n 3",
                    shell=True, capture_output=True, text=True, timeout=5
                )
                if result.stdout.strip():
                    log(f"LOG ALERT [{Path(log_file).name}]: {result.stdout.strip()[:200]}", level="ALERT")
            except:
                pass


def signal_handler(signum, frame):
    global running
    log(f"Received signal {signum}, shutting down gracefully...", level="INFO")
    running = False


def main():
    # Write PID file for external control
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    log("=" * 60)
    log("TASI System Watchdog Started")
    log(f"Active window: {FIRST_JOB_TIME} — {LAST_JOB_TIME} KSA")
    log(f"Check interval: {CHECK_INTERVAL}s")
    log("=" * 60)

    try:
        while running:
            now = datetime.now(RIYADH)

            if should_be_running(now):
                run_watchdog_cycle()
            else:
                # Outside active window — minimal logging
                if now.minute == 0:  # Log once per hour off-duty
                    log(f"Off-duty ({now.strftime('%H:%M')}), waiting for {FIRST_JOB_TIME}")

            time.sleep(CHECK_INTERVAL)

    except Exception as e:
        log(f"FATAL ERROR: {e}", level="CRITICAL")
        import traceback
        traceback.print_exc()
        raise
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink()
        log("Watchdog stopped")


if __name__ == "__main__":
    import os  # noqa: E402
    main()

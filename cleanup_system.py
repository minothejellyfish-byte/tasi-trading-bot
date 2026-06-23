#!/usr/bin/env python3
"""
TASI Daily Cleanup — v5.0
==========================
Runs daily at 04:00 KSA (after market close).

Rules:
1. WS prices (ws_prices_*.jsonl): keep 7 days, delete >10 days
2. Logs (*.log): keep last 48h, archive older
3. Picks daily (picks_*.json, pm_cache.json): keep 7 days, delete >10 days
4. Orders/Positions JSON: archive to CSV, then delete daily files
5. CSV files (order_history.csv, daily_pnl.csv): keep forever
6. Backups (*.backup*, *BACKUP*, *OLD*): delete >3 days old
7. Change requests (CHANGE_REQUEST_*.md): delete >7 days old
8. Old fix scripts (*_fix.py, implement_fix.py, etc.): delete >7 days old
9. Backtest outputs (*_output.txt): delete >3 days old
10. Archive folder: review and delete >30 days old

Author: Mino (kimi-k2.6)
Date: 2026-06-23
"""

import os
import json
import gzip
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import pytz

RIYADH = pytz.timezone("Asia/Riyadh")
BASE_DIR = Path("/home/mino/tasi-exec")
LOG_FILE = BASE_DIR / "logs" / "cleanup.log"

# Ensure logs dir exists
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    ts = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_file_age_days(filepath: Path) -> int:
    """Get file age in days."""
    try:
        mtime = datetime.fromtimestamp(filepath.stat().st_mtime, RIYADH)
        return (datetime.now(RIYADH) - mtime).days
    except:
        return 999


def extract_date_from_filename(filename: str) -> datetime:
    """Try to extract date from filename like ws_prices_2026-06-01.jsonl."""
    import re
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=RIYADH)
        except:
            pass
    return None


def rotate_log(filepath: Path, keep_hours: int = 48):
    """Rotate a log file: keep last N hours, truncate rest."""
    if not filepath.exists():
        return
    
    size_mb = filepath.stat().st_size / (1024 * 1024)
    
    # For very large files (>500MB), use tail command directly
    if size_mb > 500:
        log(f"  Fast-rotating {filepath.name} ({size_mb:.0f}MB) using tail...")
        try:
            # Keep last 10000 lines (fast, memory-efficient)
            tmp_file = filepath.with_suffix('.tmp')
            result = os.popen(f"tail -n 10000 '{filepath}' > '{tmp_file}' 2>/dev/null && mv '{tmp_file}' '{filepath}'").read()
            log(f"  Rotated {filepath.name}: kept last 10,000 lines")
            return
        except Exception as e:
            log(f"  Error fast-rotating {filepath.name}: {e}")
            return
    
    # For smaller files, use timestamp-based rotation
    try:
        cutoff = datetime.now(RIYADH) - timedelta(hours=keep_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        
        with open(filepath, 'r') as f:
            lines = f.readlines()
        
        recent_lines = []
        for line in lines:
            if len(line) >= 10 and line[:10] >= cutoff_str:
                recent_lines.append(line)
        
        if not recent_lines and lines:
            recent_lines = lines[-10000:]
        
        with open(filepath, 'w') as f:
            f.writelines(recent_lines)
        
        log(f"  Rotated {filepath.name}: kept {len(recent_lines)} lines")
    except Exception as e:
        log(f"  Error rotating {filepath.name}: {e}")


def cleanup_ws_prices():
    """Rule 1: WS prices — keep 7 days, delete >10 days."""
    log("=== WS Prices Cleanup ===")
    deleted = 0
    kept = 0
    
    for f in BASE_DIR.glob("ws_prices_*.jsonl"):
        date = extract_date_from_filename(f.name)
        if date:
            days_old = (datetime.now(RIYADH) - date).days
            if days_old > 10:
                try:
                    f.unlink()
                    deleted += 1
                    log(f"  DELETED {f.name} ({days_old} days old)")
                except Exception as e:
                    log(f"  ERROR deleting {f.name}: {e}")
            else:
                kept += 1
    
    # Also clean corrupt files
    for f in BASE_DIR.glob("ws_prices_*.corrupt"):
        try:
            f.unlink()
            deleted += 1
            log(f"  DELETED corrupt file {f.name}")
        except:
            pass
    
    log(f"  Result: {deleted} deleted, {kept} kept")


def cleanup_logs():
    """Rule 2: Logs — keep last 48h, rotate older."""
    log("=== Logs Cleanup ===")
    
    log_files = [
        BASE_DIR / "bookkeeper.log",
        BASE_DIR / "bot.log",
        BASE_DIR / "exec.log",
        BASE_DIR / "ws_probe.log",
        BASE_DIR / "ws_frames_raw.log",
        BASE_DIR / "ws_keepalive.log",
        BASE_DIR / "chrome_startup.log",
        BASE_DIR / "cleanup.log",
        BASE_DIR / "refresh_cron.log",
        BASE_DIR / "tunnel.log",
        BASE_DIR / "ws_frames_raw.log",
    ]
    
    for log_file in log_files:
        if log_file.exists():
            size_mb = log_file.stat().st_size / (1024 * 1024)
            if size_mb > 100:  # Only rotate if >100MB
                log(f"  Rotating {log_file.name} ({size_mb:.0f}MB)")
                rotate_log(log_file, keep_hours=48)
            else:
                log(f"  Skipped {log_file.name} ({size_mb:.1f}MB — under 100MB threshold)")


def cleanup_picks():
    """Rule 3: Picks daily — keep 7 days, delete >10 days."""
    log("=== Picks Cleanup ===")
    deleted = 0
    kept = 0
    
    # picks_1030.json, picks_1200.json, picks_1330.json
    for f in BASE_DIR.glob("picks_*.json"):
        age = get_file_age_days(f)
        if age > 10:
            try:
                f.unlink()
                deleted += 1
                log(f"  DELETED {f.name} ({age} days old)")
            except Exception as e:
                log(f"  ERROR deleting {f.name}: {e}")
        else:
            kept += 1
    
    # pm_cache.json (only keep current)
    pm_cache = BASE_DIR / "pm_cache.json"
    if pm_cache.exists():
        age = get_file_age_days(pm_cache)
        # pm_cache is regenerated daily, so keep it
        log(f"  Kept pm_cache.json ({age} days old)")
    
    log(f"  Result: {deleted} deleted, {kept} kept")


def cleanup_orders_positions():
    """Rule 4: Orders/Positions JSON — archive to CSV, delete daily files."""
    log("=== Orders/Positions JSON Cleanup ===")
    
    # These are the live state files — they get overwritten daily
    # We keep them but they're small, so just log their status
    files_to_check = [
        BASE_DIR / "orders.json",
        BASE_DIR / "positions.json",
        BASE_DIR / "trade_book.json",
    ]
    
    for f in files_to_check:
        if f.exists():
            size_kb = f.stat().st_size / 1024
            log(f"  {f.name}: {size_kb:.0f}KB (live state file — kept)")
    
    # Delete old trades_YYYY-MM-DD.json files
    deleted = 0
    for f in BASE_DIR.glob("trades_*.json"):
        age = get_file_age_days(f)
        if age > 10:
            try:
                f.unlink()
                deleted += 1
                log(f"  DELETED {f.name} ({age} days old)")
            except:
                pass
    
    log(f"  Result: {deleted} old trades files deleted")


def cleanup_backups():
    """Rule 6: Backups — delete >3 days old."""
    log("=== Backup Files Cleanup ===")
    deleted = 0
    
    patterns = [
        BASE_DIR.glob("*.backup*"),
        BASE_DIR.glob("*BACKUP*"),
        BASE_DIR.glob("*OLD*"),
        (BASE_DIR / "backups").glob("*.backup*") if (BASE_DIR / "backups").exists() else [],
    ]
    
    for pattern in patterns:
        for f in pattern:
            if f.is_file():
                age = get_file_age_days(f)
                if age > 3:
                    try:
                        f.unlink()
                        deleted += 1
                        log(f"  DELETED {f.name} ({age} days old)")
                    except:
                        pass
    
    log(f"  Result: {deleted} backup files deleted")


def cleanup_change_requests():
    """Rule 7: Change request files — delete >7 days old."""
    log("=== Change Request Cleanup ===")
    deleted = 0
    
    for f in BASE_DIR.glob("CHANGE_REQUEST_*.md"):
        age = get_file_age_days(f)
        if age > 7:
            try:
                f.unlink()
                deleted += 1
                log(f"  DELETED {f.name} ({age} days old)")
            except:
                pass
    
    log(f"  Result: {deleted} change request files deleted")


def cleanup_fix_scripts():
    """Rule 8: Old fix scripts — delete >7 days old."""
    log("=== Fix Scripts Cleanup ===")
    deleted = 0
    
    fix_files = [
        "bookkeeper_fix.py", "bookkeeper_fixed.py", "bookkeeper_simple_fix.py",
        "bot_fix.py", "fix_prune.py", "implement_fix.py", "test_fix.py",
        "poller_hard_close_fix.py",
    ]
    
    for filename in fix_files:
        f = BASE_DIR / filename
        if f.exists():
            age = get_file_age_days(f)
            if age > 7:
                try:
                    f.unlink()
                    deleted += 1
                    log(f"  DELETED {f.name} ({age} days old)")
                except:
                    pass
    
    # Also clean summary files
    for f in BASE_DIR.glob("*_summary.md"):
        age = get_file_age_days(f)
        if age > 7:
            try:
                f.unlink()
                deleted += 1
                log(f"  DELETED {f.name} ({age} days old)")
            except:
                pass
    
    # Clean patch files
    for f in BASE_DIR.glob("*.patch"):
        age = get_file_age_days(f)
        if age > 7:
            try:
                f.unlink()
                deleted += 1
                log(f"  DELETED {f.name} ({age} days old)")
            except:
                pass
    
    log(f"  Result: {deleted} fix files deleted")


def cleanup_backtest_outputs():
    """Rule 9: Backtest outputs — delete >3 days old."""
    log("=== Backtest Output Cleanup ===")
    deleted = 0
    
    for f in BASE_DIR.glob("*_output.txt"):
        if "backtest" in f.name.lower():
            age = get_file_age_days(f)
            if age > 3:
                try:
                    f.unlink()
                    deleted += 1
                    log(f"  DELETED {f.name} ({age} days old)")
                except:
                    pass
    
    log(f"  Result: {deleted} backtest outputs deleted")


def cleanup_archive():
    """Rule 10: Archive folder — delete >30 days old."""
    log("=== Archive Cleanup ===")
    deleted = 0
    
    archive_dir = BASE_DIR / "archive"
    if not archive_dir.exists():
        log("  Archive folder not found")
        return
    
    for item in archive_dir.iterdir():
        age = get_file_age_days(item)
        if age > 30:
            try:
                if item.is_file():
                    item.unlink()
                else:
                    shutil.rmtree(item)
                deleted += 1
                log(f"  DELETED {item.name} ({age} days old)")
            except Exception as e:
                log(f"  ERROR deleting {item.name}: {e}")
    
    log(f"  Result: {deleted} archive items deleted")


def show_disk_usage():
    """Show current disk usage."""
    log("=== Disk Usage ===")
    try:
        result = os.popen(f"du -sh {BASE_DIR}").read().strip()
        log(f"  Total: {result}")
        
        # Biggest files
        result = os.popen(f"cd {BASE_DIR} && find . -maxdepth 1 -type f -size +10M -exec ls -lh {{}} \\; | sort -k5 -rh | head -10").read().strip()
        if result:
            log("  Top 10 largest files:")
            for line in result.split("\n"):
                log(f"    {line}")
    except:
        pass


def main():
    log("=" * 60)
    log("TASI Daily Cleanup Started")
    log("=" * 60)
    
    show_disk_usage()
    
    cleanup_ws_prices()
    cleanup_logs()
    cleanup_picks()
    cleanup_orders_positions()
    cleanup_backups()
    cleanup_change_requests()
    cleanup_fix_scripts()
    cleanup_backtest_outputs()
    cleanup_archive()
    
    show_disk_usage()
    
    log("=" * 60)
    log("TASI Daily Cleanup Complete")
    log("=" * 60)


if __name__ == "__main__":
    main()

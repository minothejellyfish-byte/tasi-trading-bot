#!/usr/bin/env python3
"""
TASI Log Cleanup Script
- Compresses old ws_prices JSONL files
- Summarizes old logs before compression
- Keeps last 7 days of logs
- Archives summarized logs
"""
import os
import gzip
import shutil
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path("/home/mino/tasi-exec")
ARCHIVE_DIR = BASE_DIR / "archive"
LOG_RETENTION_DAYS = 7

# Ensure archive dir exists
ARCHIVE_DIR.mkdir(exist_ok=True)

def summarize_log(log_path):
    """Extract key stats from log file before compression"""
    stats = {
        'lines': 0,
        'errors': 0,
        'warnings': 0,
        'first_date': None,
        'last_date': None,
        'key_events': []
    }
    
    try:
        with open(log_path, 'r') as f:
            for line in f:
                stats['lines'] += 1
                if 'ERROR' in line or 'error' in line.lower():
                    stats['errors'] += 1
                if 'WARN' in line or 'warning' in line.lower():
                    stats['warnings'] += 1
                
                # Extract first/last timestamps (basic)
                if stats['lines'] == 1:
                    stats['first_date'] = line[:50].strip()
                stats['last_date'] = line[:50].strip()
                
                # Keep key events (buys, sells, regime changes)
                if any(k in line for k in ['BUY', 'SELL', 'Target', 'Stop', 'regime']):
                    if len(stats['key_events']) < 20:  # Keep max 20
                        stats['key_events'].append(line.strip()[:200])
    except Exception as e:
        print(f"Error reading {log_path}: {e}")
    
    return stats

def compress_with_summary(file_path):
    """Compress file and create summary"""
    file_path = Path(file_path)
    if not file_path.exists():
        return
    
    # Only process .log files for summary
    if file_path.suffix == '.log':
        stats = summarize_log(file_path)
        
        # Write summary
        summary_path = ARCHIVE_DIR / f"{file_path.stem}_{file_path.stat().st_mtime:.0f}_summary.txt"
        with open(summary_path, 'w') as f:
            f.write(f"Log Summary: {file_path.name}\n")
            f.write(f"Generated: {datetime.now().isoformat()}\n")
            f.write(f"Lines: {stats['lines']}\n")
            f.write(f"Errors: {stats['errors']}\n")
            f.write(f"Warnings: {stats['warnings']}\n")
            f.write(f"First entry: {stats['first_date']}\n")
            f.write(f"Last entry: {stats['last_date']}\n")
            f.write(f"\nKey Events:\n")
            for event in stats['key_events']:
                f.write(f"  - {event}\n")
        
        print(f"Summary: {summary_path}")
    
    # Compress
    compressed = ARCHIVE_DIR / f"{file_path.name}.gz"
    with open(file_path, 'rb') as f_in:
        with gzip.open(compressed, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    
    # Remove original
    file_path.unlink()
    print(f"Compressed: {file_path.name} → {compressed}")

def cleanup_old_files():
    """Main cleanup routine"""
    cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
    
    # Files to compress (older than 7 days)
    patterns = [
        'ws_prices_*.jsonl',
        '*.log',
    ]
    
    for pattern in patterns:
        for file_path in BASE_DIR.glob(pattern):
            # Skip current day's files
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            if mtime < cutoff:
                # Keep exec.log and poller.log for current session
                if file_path.name in ['exec.log', 'poller.log', 'keepalive.log']:
                    if mtime > datetime.now() - timedelta(days=1):
                        continue  # Keep today's logs
                
                compress_with_summary(file_path)
    
    # Delete old compressed files (older than 30 days)
    archive_cutoff = datetime.now() - timedelta(days=30)
    for archived in ARCHIVE_DIR.glob('*.gz'):
        mtime = datetime.fromtimestamp(archived.stat().st_mtime)
        if mtime < archive_cutoff:
            archived.unlink()
            print(f"Deleted old archive: {archived.name}")
    
    # Delete old summaries (older than 30 days)
    for summary in ARCHIVE_DIR.glob('*_summary.txt'):
        mtime = datetime.fromtimestamp(summary.stat().st_mtime)
        if mtime < archive_cutoff:
            summary.unlink()
            print(f"Deleted old summary: {summary.name}")

if __name__ == '__main__':
    print(f"TASI Log Cleanup - {datetime.now().isoformat()}")
    print(f"Retention: {LOG_RETENTION_DAYS} days")
    print(f"Archive: {ARCHIVE_DIR}")
    print("-" * 50)
    cleanup_old_files()
    print("Done.")

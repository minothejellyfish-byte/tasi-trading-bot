#!/usr/bin/env python3
"""
Simple test to check weekly report functionality
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path("/home/mino/tasi-exec")
ARCHIVE_PICKS = BASE_DIR / "archive" / "picks"
RELEARNING_DIR = BASE_DIR / "relearning"

def get_week_range():
    today = datetime.now()
    if today.weekday() < 4:
        thursday = today - timedelta(days=today.weekday() - 3)
    else:
        thursday = today - timedelta(days=today.weekday() - 3)
    sunday = thursday - timedelta(days=4)
    return sunday.date(), thursday.date()

def test_basic():
    sunday, thursday = get_week_range()
    week_label = f"{sunday.strftime('%Y-%m-%d')}_to_{thursday.strftime('%Y-%m-%d')}"
    
    print(f"Week: {week_label}")
    print(f"Sunday: {sunday}")
    print(f"Thursday: {thursday}")
    
    # Check archive
    print(f"\nChecking archive directory: {ARCHIVE_PICKS}")
    if ARCHIVE_PICKS.exists():
        files = list(ARCHIVE_PICKS.glob("*.json"))
        print(f"Found {len(files)} pick files")
        
        # Check files for this week
        current = sunday
        while current <= thursday:
            date_str = current.isoformat()
            archive_files = list(ARCHIVE_PICKS.glob(f"picks_{date_str}_*.json"))
            print(f"  {date_str}: {len(archive_files)} files")
            current += timedelta(days=1)
    else:
        print("Archive picks directory doesn't exist")
    
    # Check relearning directory
    print(f"\nRelearning directory: {RELEARNING_DIR}")
    if RELEARNING_DIR.exists():
        report_files = list(RELEARNING_DIR.glob("report_*.json"))
        print(f"Found {len(report_files)} existing report files")
        for rf in report_files[:3]:
            print(f"  {rf.name}")
    else:
        print("Relearning directory doesn't exist")
    
    # Try to load a picks file
    if ARCHIVE_PICKS.exists():
        sample_files = list(ARCHIVE_PICKS.glob("*.json"))
        if sample_files:
            sample_file = sample_files[0]
            print(f"\nLoading sample file: {sample_file.name}")
            try:
                with open(sample_file) as f:
                    data = json.load(f)
                print(f"  Loaded successfully")
                if 'picks' in data:
                    print(f"  Contains {len(data['picks'])} picks")
                    if data['picks']:
                        print(f"  First pick: {data['picks'][0]}")
            except Exception as e:
                print(f"  Error loading: {e}")

if __name__ == "__main__":
    test_basic()
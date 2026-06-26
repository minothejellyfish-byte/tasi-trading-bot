#!/usr/bin/env python3
"""
Test date logic
"""

from datetime import datetime, timedelta

def get_week_range():
    today = datetime.now()
    print(f"Today: {today.date()} (weekday: {today.weekday()})")
    
    # If today is Friday (4) or Saturday (5), go back to last Thursday
    if today.weekday() >= 4:  # Friday (4) or Saturday (5)
        days_since_thursday = today.weekday() - 3
        thursday = today - timedelta(days=days_since_thursday)
    else:
        thursday = today - timedelta(days=today.weekday() - 3)
    
    sunday = thursday - timedelta(days=4)
    return sunday.date(), thursday.date()

# Test
sunday, thursday = get_week_range()
print(f"\nCalculated week range:")
print(f"  Sunday: {sunday}")
print(f"  Thursday: {thursday}")
print(f"  Week label: {sunday.strftime('%Y-%m-%d')}_to_{thursday.strftime('%Y-%m-%d')}")

# Check if this is correct
print(f"\nChecking if {sunday.strftime('%Y-%m-%d')} to {thursday.strftime('%Y-%m-%d')} is last trading week:")
print(f"  June 21-25, 2026: That's last week (Sunday to Thursday)")

# List all dates in that range
print(f"\nAll dates in range:")
current = sunday
while current <= thursday:
    print(f"  {current}")
    current += timedelta(days=1)
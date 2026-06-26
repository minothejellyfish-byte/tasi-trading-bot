#!/usr/bin/env python3
"""
Fetch TASI historical data from Saudi Exchange website.
Uses Playwright connected to Derayah Chrome (CDP port 18801) to bypass Akamai detection.

Usage:
    python3 fetch_tasi_history.py --output /path/to/tasi_history.csv
    python3 fetch_tasi_history.py --json --output /path/to/tasi_history.json
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright


SAUDI_EXCHANGE_URL = (
    "https://www.saudiexchange.sa/wps/portal/saudiexchange/newsandreports/"
    "reports-publications/historical-reports?locale=en"
)
CDP_URL = "http://127.0.0.1:18801"


def fetch_tasi_history():
    """Fetch TASI historical data from Saudi Exchange."""
    with sync_playwright() as p:
        # Connect to the running Derayah Chrome via CDP
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        print(f"Navigating to Saudi Exchange Historical Reports...")
        try:
            page.goto(SAUDI_EXCHANGE_URL, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"Navigation error (using fallback): {e}")
            # Try without waiting for load event
            page.goto(SAUDI_EXCHANGE_URL, timeout=60000, wait_until="commit")
        
        page.wait_for_timeout(10000)  # Wait for JS to render data

        # Extract table data
        rows = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            const data = [];
            // Table 1 is Performance Summary
            if (tables.length > 0) {
                const rows = tables[0].querySelectorAll('tr');
                for (let i = 1; i < rows.length; i++) {  // Skip header
                    const cells = rows[i].querySelectorAll('td, th');
                    if (cells.length >= 8) {
                        data.push({
                            date: cells[0].textContent.trim(),
                            open: cells[1].textContent.trim().replace(/,/g, ''),
                            high: cells[2].textContent.trim().replace(/,/g, ''),
                            low: cells[3].textContent.trim().replace(/,/g, ''),
                            close: cells[4].textContent.trim().replace(/,/g, ''),
                            volume: cells[5].textContent.trim().replace(/,/g, ''),
                            value: cells[6].textContent.trim().replace(/,/g, '').replace('^', ''),
                            trades: cells[7].textContent.trim().replace(/,/g, ''),
                        });
                    }
                }
            }
            return data;
        }""")

        context.close()
        browser.close()

        return rows


def save_csv(data, filepath):
    """Save data to CSV file."""
    fieldnames = ["date", "open", "high", "low", "close", "volume", "value", "trades"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    print(f"Saved {len(data)} rows to {filepath}")


def save_json(data, filepath):
    """Save data to JSON file."""
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {len(data)} rows to {filepath}")


def main():
    parser = argparse.ArgumentParser(description="Fetch TASI historical data from Saudi Exchange")
    parser.add_argument("--output", "-o", required=True, help="Output file path")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of CSV")
    parser.add_argument("--append", action="store_true", help="Append to existing file")
    args = parser.parse_args()

    data = fetch_tasi_history()
    if not data:
        print("No data fetched. Is Derayah Chrome running? Check CDP on port 18801.")
        sys.exit(1)

    # Parse dates and sort (newest first)
    for row in data:
        row["date_parsed"] = datetime.strptime(row["date"], "%Y/%m/%d")

    if args.json:
        save_json(data, args.output)
    else:
        save_csv(data, args.output)

    print(f"Data range: {data[-1]['date']} to {data[0]['date']}")
    print(f"Total rows: {len(data)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Fetch TASI historical data from Saudi Exchange using browser automation.
Requires Derayah Chrome to be running on CDP port 18801.

Usage:
    python3 fetch_tasi_saudi.py --output /path/to/tasi_history.csv
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


def fetch_tasi_history(output_path):
    """Fetch TASI historical data from Saudi Exchange."""
    with sync_playwright() as p:
        # Connect to the running Derayah Chrome via CDP
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:18801")
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        print(f"Navigating to Saudi Exchange...")
        page.goto(SAUDI_EXCHANGE_URL, timeout=60000)
        page.wait_for_timeout(10000)  # Wait for JS to render

        # Extract table data
        rows = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            const data = [];
            if (tables.length > 0) {
                const rows = tables[0].querySelectorAll('tr');
                for (let i = 1; i < rows.length; i++) {
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

        if not rows:
            print("No data fetched. Is Derayah Chrome running?")
            return False

        # Save to CSV
        fieldnames = ["date", "open", "high", "low", "close", "volume", "value", "trades"]
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"Saved {len(rows)} rows to {output_path}")
        print(f"Data range: {rows[-1]['date']} to {rows[0]['date']}")
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-o", required=True)
    args = parser.parse_args()
    
    success = fetch_tasi_history(args.output)
    sys.exit(0 if success else 1)

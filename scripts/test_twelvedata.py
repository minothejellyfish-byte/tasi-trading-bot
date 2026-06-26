#!/usr/bin/env python3
"""Test Twelve Data API for US market data fallback."""

import requests
from datetime import datetime

API_KEY = "demo"  # Replace with real key from https://twelvedata.com
BASE_URL = "https://api.twelvedata.com"

def test_quote(symbol="AAPL"):
    """Test real-time quote."""
    url = f"{BASE_URL}/quote?symbol={symbol}&apikey={API_KEY}"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    print(f"Quote for {symbol}:")
    print(f"  Price: {data.get('price')}")
    print(f"  Open: {data.get('open')}")
    print(f"  High: {data.get('high')}")
    print(f"  Low: {data.get('low')}")
    print(f"  Volume: {data.get('volume')}")
    return data

def test_time_series(symbol="AAPL", interval="1min", prepost=True, outputsize=10):
    """Test intraday time series with pre/post market."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "apikey": API_KEY,
        "prepost": "true" if prepost else "false",
        "outputsize": outputsize,
    }
    url = f"{BASE_URL}/time_series"
    resp = requests.get(url, params=params, timeout=15)
    data = resp.json()
    
    if data.get("status") == "ok":
        values = data.get("values", [])
        print(f"\nTime Series for {symbol} ({interval}, prepost={prepost}):")
        print(f"  Retrieved {len(values)} bars")
        for v in values[:3]:
            print(f"    {v['datetime']}: O={v['open']} H={v['high']} L={v['low']} C={v['close']} V={v.get('volume', 'N/A')}")
    else:
        print(f"Error: {data.get('message')}")
    
    return data

if __name__ == "__main__":
    print("=" * 50)
    print("Twelve Data API Test")
    print("=" * 50)
    
    # Test quote
    test_quote("AAPL")
    
    # Test time series with pre-market
    test_time_series("AAPL", interval="1min", prepost=True, outputsize=10)
    
    # Test time series without pre-market
    test_time_series("AAPL", interval="1min", prepost=False, outputsize=5)
    
    print("\n" + "=" * 50)
    print("Test complete!")
    print("=" * 50)

#!/usr/bin/env python3
"""
Test bookkeeper matching logic for MARKET->LIMIT split orders
"""

import json
import csv
from datetime import datetime

def test_matching():
    print("=== Testing Bookkeeper Matching Logic ===\n")
    
    # Load orders.json
    with open('orders.json', 'r') as f:
        orders = json.load(f)
    
    # Load order_history.csv
    with open('history/order_history.csv', 'r') as f:
        reader = csv.DictReader(f)
        csv_rows = list(reader)
    
    # Find parent-child candidate pairs
    print("Looking for potential parent-child relationships:")
    print("=" * 60)
    
    # Group by symbol+side+qty
    groups = {}
    for oid, order in orders.items():
        if order.get('status') == 5:  # REJECTED parent candidates
            key = (order.get('symbol', ''), order.get('side', ''), order.get('qty', 0))
            if key not in groups:
                groups[key] = {'parents': [], 'children': []}
            groups[key]['parents'].append({
                'id': oid,
                'price': order.get('price', 0),
                'type': order.get('type', ''),
                'trigger_basis': order.get('trigger_basis', ''),
                'initiated_at': order.get('initiated_at', ''),
                'initiated_by': order.get('initiated_by', '')
            })
    
    # Find matching FILLED children
    for oid, order in orders.items():
        if order.get('status') == 3:  # FILLED child candidates
            key = (order.get('symbol', ''), order.get('side', ''), order.get('qty', 0))
            if key in groups:
                groups[key]['children'].append({
                    'id': oid,
                    'price': order.get('price', 0),
                    'type': order.get('type', ''),
                    'trigger_basis': order.get('trigger_basis', ''),
                    'initiated_at': order.get('initiated_at', ''),
                    'initiated_by': order.get('initiated_by', '')
                })
    
    # Check 6019 SELL 9 (our suspected split case)
    print("\n=== 6019 SELL 9 (potential split) ===")
    key = ('6019', 'SELL', 9)
    if key in groups:
        parents = groups[key]['parents']
        children = groups[key]['children']
        
        print(f"Parents (REJECTED): {len(parents)}")
        for p in parents:
            print(f"  {p['id']}: price={p['price']} type={p['type']} trigger={p['trigger_basis']} time={p['initiated_at'][11:19] if p['initiated_at'] else 'N/A'} by={p['initiated_by']}")
        
        print(f"\nChildren (FILLED): {len(children)}")
        for c in children:
            print(f"  {c['id']}: price={c['price']} type={c['type']} trigger={c['trigger_basis']} time={c['initiated_at'][11:19] if c['initiated_at'] else 'N/A'} by={c['initiated_by']}")
    
    # Current matching logic simulation
    print("\n=== Current Bookkeeper Matching Logic ===")
    print("Match requires: symbol==symbol AND side==side AND qty==qty AND price==price")
    print("AND timestamp within 5 minutes (or timestamp parsing fails)")
    
    # Simulate matching for 6019 SELL 9
    parent = None
    child = None
    for p in groups.get(key, {}).get('parents', []):
        if p['id'] == '?':
            parent = p
            break
    
    for c in groups.get(key, {}).get('children', []):
        if c['id'] == '83':
            child = c
            break
    
    if parent and child:
        print(f"\nParent ?: price={parent['price']} type={parent['type']}")
        print(f"Child 83: price={child['price']} type={child['type']}")
        
        # Check price match
        price_match = parent['price'] == child['price']
        print(f"Price match (0.0 == 22.74): {price_match} → {'MATCH' if price_match else 'NO MATCH'}")
        
        # Check timestamp parsing
        parent_time = parent['initiated_at']
        child_time = child['initiated_at']
        
        print(f"\nParent time: {parent_time}")
        print(f"Child time: {child_time}")
        
        if parent_time and child_time:
            try:
                parent_dt = datetime.fromisoformat(parent_time.replace('Z', '+00:00'))
                child_dt = datetime.fromisoformat(child_time.replace('Z', '+00:00'))
                time_diff = abs((parent_dt - child_dt).total_seconds())
                print(f"Time difference: {time_diff:.1f} seconds")
                print(f"Within 5 minutes (300s): {time_diff <= 300}")
            except Exception as e:
                print(f"Timestamp parsing failed: {e}")
                print("Bookkeeper would MATCH anyway (fallback)")
        else:
            print("Missing timestamp → no time check")
    
    # Check CSV times
    print("\n=== CSV Times ===")
    for row in csv_rows:
        if row.get('symbol') == '6019' and row.get('side') == 'SELL' and row.get('qty') == '9':
            print(f"{row['order_id']} at {row.get('time', 'N/A')}: price={row['price']} trigger={row['trigger_basis']}")
    
    # Check if order 83 has parent in CSV
    print("\n=== Checking for parent ? in CSV ===")
    for row in csv_rows:
        if row.get('order_id') == '?':
            print(f"Parent ?: symbol={row['symbol']} side={row['side']} qty={row['qty']} price={row['price']} trigger={row['trigger_basis']} time={row.get('time', 'N/A')}")
    
    # Check bookkeeper logic
    print("\n=== Bookkeeper Bug ===")
    print("Line 614: 'trigger_basis': existing.get('trigger_basis') or 'unknown'")
    print("If existing = {} (empty dict), existing.get('trigger_basis') returns None")
    print("None or 'unknown' = 'unknown'")
    print("\nShould be: existing.get('trigger_basis', 'unknown')")
    
    print("\nLine 674 has same bug")
    print("\nLine 658: price exact match prevents MARKET->LIMIT matching")

if __name__ == "__main__":
    test_matching()
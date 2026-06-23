#!/usr/bin/env python3
"""
Proposed fix for bookkeeper.py to handle Derayah order splitting
"""

import json
from datetime import datetime

def test_fix_logic():
    """Test the proposed fix logic"""
    
    # Load current orders
    with open('orders.json', 'r') as f:
        orders = json.load(f)
    
    print("=== Current State ===")
    print("Parent order ?:")
    if '?' in orders:
        parent = orders['?']
        print(f"  symbol: {parent.get('symbol')}")
        print(f"  side: {parent.get('side')}")
        print(f"  qty: {parent.get('qty')}")
        print(f"  price: {parent.get('price')}")
        print(f"  type: {parent.get('type')}")
        print(f"  trigger_basis: {parent.get('trigger_basis')}")
        print(f"  initiated_at: {parent.get('initiated_at')}")
        print(f"  initiated_by: {parent.get('initiated_by')}")
    
    print("\nPotential children (status 3, same symbol/side):")
    children = []
    for oid, order in orders.items():
        if oid != '?' and order.get('status') == 3:  # FILLED
            if order.get('symbol') == '6019' and order.get('side') == 'SELL':
                children.append((oid, order))
    
    for oid, child in children:
        print(f"  {oid}: qty={child.get('qty')}, price={child.get('price')}, trigger={child.get('trigger_basis')}, initiated_at={child.get('initiated_at')}")
    
    print("\n=== Proposed Algorithm ===")
    print("1. Find all parent orders with id '?'")
    print("2. For each parent, find matching child orders:")
    print("   - Same symbol, side")
    print("   - Total qty matches parent qty")
    print("   - Timestamp after parent OR 00:00:00")
    print("   - Unknown/empty trigger_basis")
    print("   - Price tolerance for MARKET orders (price==0.0)")
    print("3. Copy parent metadata to children")
    print("4. Remove parent from orders.json")
    
    # Simulate the matching
    print("\n=== Simulating Matching ===")
    if '?' in orders:
        parent = orders['?']
        parent_qty = parent.get('qty', 0)
        parent_symbol = parent.get('symbol', '')
        parent_side = parent.get('side', '')
        parent_time = parent.get('initiated_at', '')
        parent_price = parent.get('price', 0)
        
        matched_children = []
        remaining_qty = parent_qty
        
        for oid, child in children:
            if child.get('symbol') == parent_symbol and child.get('side') == parent_side:
                # Price tolerance for MARKET orders
                price_ok = (child.get('price') == parent_price or parent_price == 0.0)
                
                # Check timestamp
                child_time = child.get('initiated_at', '')
                time_ok = False
                if child_time and parent_time:
                    try:
                        child_dt = datetime.fromisoformat(child_time.replace('Z', '+00:00'))
                        parent_dt = datetime.fromisoformat(parent_time.replace('Z', '+00:00'))
                        time_diff = (child_dt - parent_dt).total_seconds()
                        # Child after parent or within 5 minutes
                        time_ok = time_diff >= -300 and time_diff <= 300  # ±5 minutes
                    except:
                        # If parsing fails (e.g., 00:00:00), allow match
                        time_ok = True
                else:
                    time_ok = True
                
                # Check trigger_basis
                trigger_ok = child.get('trigger_basis') in ['', 'unknown', None]
                
                if price_ok and time_ok and trigger_ok:
                    matched_children.append((oid, child))
                    remaining_qty -= child.get('qty', 0)
                    print(f"  ✓ Matched child {oid}: qty={child.get('qty')}, remaining={remaining_qty}")
                else:
                    print(f"  ✗ Skipped child {oid}: price_ok={price_ok}, time_ok={time_ok}, trigger_ok={trigger_ok}")
        
        print(f"\nParent qty: {parent_qty}")
        print(f"Matched children qty total: {parent_qty - remaining_qty}")
        print(f"Remaining qty: {remaining_qty}")
        
        if remaining_qty == 0:
            print("✓ FULL MATCH - All parent qty accounted for")
        elif remaining_qty < parent_qty:
            print("⚠ PARTIAL MATCH - Some but not all qty matched")
        else:
            print("✗ NO MATCH - No children matched")
    
    print("\n=== Code Changes Needed ===")
    print("1. Change price check (line 658):")
    print("   From: api_data[\"price\"] == local_price")
    print("   To:   (api_data[\"price\"] == local_price or local_price == 0.0)")
    print()
    print("2. Track multiple children (remove 'break' at line 662):")
    print("   Instead of breaking after first match, collect all matches")
    print("   Sum their qty, match if total_qty == parent_qty")
    print()
    print("3. Check trigger_basis (add to matching logic):")
    print("   Check if child has empty/unknown trigger_basis")
    print()
    print("4. Copy parent timestamp (line 670):")
    print("   Use parent's initiated_at, not api_order_map[...][\"order_date\"]")
    print()
    print("5. Fix trigger_basis assignment (lines 614, 672):")
    print("   From: existing.get(\"trigger_basis\") or \"unknown\"")
    print("   To:   existing.get(\"trigger_basis\", \"unknown\")")
    print()
    print("6. After matching, remove parent '?' from new_orders")
    print("   Or mark as 'matched' status")

if __name__ == "__main__":
    test_fix_logic()
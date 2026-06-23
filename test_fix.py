#!/usr/bin/env python3
"""
Test the proposed fix for MARKET order splitting
"""

import json
from datetime import datetime

def simulate_bookkeeper_matching():
    """Simulate bookkeeper logic with fixes"""
    
    # Load orders
    with open('orders.json', 'r') as f:
        orders = json.load(f)
    
    print("=== Current Bookkeeper Matching (BROKEN) ===")
    print("Parent ?: symbol=6019, side=SELL, qty=9, price=0.0, type=MARKET")
    print("Child 83: symbol=6019, side=SELL, qty=9, price=22.74, type=LIMIT")
    print()
    print("Current check: api_data['price'] == local_price")
    print("0.0 == 22.74? FALSE → NO MATCH")
    print()
    
    print("=== Proposed Fix ===")
    print("Change line 658 from:")
    print("   api_data[\"price\"] == local_price")
    print("To:")
    print("   (api_data[\"price\"] == local_price or local_price == 0.0)")
    print()
    print("Also need to handle multiple children!")
    print("If parent qty=9, could be:")
    print("- Child1 qty=5, Child2 qty=4 (sum=9)")
    print("- Child1 qty=9 (single child)")
    print()
    
    # Simulate matching with fix
    parent = orders.get('?')
    children = []
    for oid, order in orders.items():
        if oid != '?' and order.get('symbol') == '6019' and order.get('side') == 'SELL':
            children.append((oid, order))
    
    print("Potential children for parent ?:")
    for oid, child in children:
        print(f"  {oid}: qty={child.get('qty')}, price={child.get('price')}, trigger={child.get('trigger_basis')}")
    
    # Check which would match with fix
    print("\n=== Matching with Price Tolerance ===")
    if parent:
        for oid, child in children:
            # Simulate fixed matching logic
            symbol_match = child.get('symbol') == parent.get('symbol')
            side_match = child.get('side') == parent.get('side')
            qty_match = child.get('qty') == parent.get('qty')
            price_match = (child.get('price') == parent.get('price') or parent.get('price') == 0.0)
            
            print(f"Child {oid}:")
            print(f"  symbol_match: {symbol_match}")
            print(f"  side_match: {side_match}")
            print(f"  qty_match: {qty_match}")
            print(f"  price_match: {price_match} (parent price={parent.get('price')})")
            print(f"  Would match: {symbol_match and side_match and qty_match and price_match}")
            print()
    
    # Check timestamp parsing
    print("=== Timestamp Issue ===")
    print("Child timestamp: 2026-06-15T00:00:00 (date-only from Derayah)")
    print("Parent timestamp: 2026-06-15T14:42:31.803777+03:00")
    print("Time diff would fail parsing → bookkeeper falls back and matches!")
    print()
    
    # Check trigger_basis inheritance bug
    print("=== trigger_basis Inheritance Bug ===")
    print("Line 672: 'trigger_basis': local_o.get('trigger_basis') or 'unknown'")
    print("If local_o.get('trigger_basis') returns '' (empty string),")
    print("'' or 'unknown' = 'unknown' (WRONG!)")
    print("Should be: local_o.get('trigger_basis', 'unknown')")
    print()
    
    print("Parent ? trigger_basis: 'vwap_breakdown'")
    print("Child would get: 'vwap_breakdown' with .get('trigger_basis', 'unknown')")
    print("Child actually gets: 'unknown' with .get('trigger_basis') or 'unknown'")
    print("(Actually parent has 'vwap_breakdown', not empty string)")
    print()
    
    # Check multiple children scenario
    print("=== Multiple Children Scenario ===")
    print("Current logic breaks after first match (line 662: break)")
    print("Need to track matched_qty and continue searching")
    print()
    print("Pseudocode:")
    print("matched_children = []")
    print("remaining_qty = parent_qty")
    print("for api_order in api_orders:")
    print("    if matches(parent, api_order) and remaining_qty > 0:")
    print("        matched_children.append(api_order)")
    print("        remaining_qty -= api_order.qty")
    print("if remaining_qty == 0: # All matched")
    print("    for child in matched_children:")
    print("        copy parent metadata to child")
    print("    mark parent as matched")
    print("elif remaining_qty < parent_qty: # Partial fill")
    print("    mark parent as PARTIAL")
    print("    for child in matched_children:")
    print("        copy parent metadata to child")

if __name__ == "__main__":
    simulate_bookkeeper_matching()
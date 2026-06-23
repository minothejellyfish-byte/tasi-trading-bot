#!/usr/bin/env python3
"""
Safe offline test of bookkeeper.py matching logic
Creates test environment without affecting production
"""

import json
import os
import shutil
from datetime import datetime

def setup_test_environment():
    """Create test copies of files"""
    print("Setting up test environment...")
    
    # Backup original files
    if os.path.exists('orders.json'):
        shutil.copy2('orders.json', 'orders.json.backup')
        print("  Backed up orders.json")
    
    # Create test orders.json
    test_orders = {
        "?": {
            "symbol": "6019",
            "side": "SELL", 
            "qty": 9,
            "price": 0.0,
            "type": "MARKET",
            "status": 0,  # INITIATED
            "trigger_basis": "vwap_breakdown",
            "initiated_by": "auto_sell",
            "initiated_at": "2026-06-15T14:42:31.803777+03:00",
            "updated_at": "2026-06-15T14:42:31.803777+03:00"
        },
        "83": {
            "symbol": "6019",
            "side": "SELL",
            "qty": 9,
            "price": 22.74,
            "type": "LIMIT",
            "status": 3,  # FILLED (from Derayah API)
            "trigger_basis": "",  # Empty - will become "unknown"
            "initiated_by": "",  # Empty
            "initiated_at": "2026-06-15T00:00:00",  # Date-only from Derayah
            "updated_at": "2026-06-15T14:42:00.000000+03:00"
        },
        "71": {
            "symbol": "6019", 
            "side": "SELL",
            "qty": 9,
            "price": 23.0,
            "type": "LIMIT",
            "status": 3,
            "trigger_basis": "",
            "initiated_by": "",
            "initiated_at": "2026-06-15T00:00:00",
            "updated_at": "2026-06-15T10:25:00.000000+03:00"
        }
    }
    
    with open('orders.test.json', 'w') as f:
        json.dump(test_orders, f, indent=2)
    print("  Created orders.test.json")
    
    # Create test API response (simulated Derayah API)
    test_api_orders = [
        {
            "orderId": "83",
            "symbol": "6019.SR",
            "side": 2,  # SELL
            "quantity": 9,
            "price": 22.74,
            "orderDate": "2026.

⚠️ [... middle content omitted — showing head and tail ...]

Status: INITIATED → FILLED")
        else:
            print(f"  Child {oid}: NOT updated (trigger_basis: {o.get('trigger_basis', 'N/A')})")
    
    # Check if parent was removed
    if "?" in results:
        print(f"\\n❌ Parent '?' still in orders: status={results['?'].get('status')}")
    else:
        print("\\n✅ Parent '?' removed after matching")
    
    return results

def cleanup():
    """Restore original files"""
    if os.path.exists('orders.json.backup'):
        shutil.copy2('orders.json.backup', 'orders.json')
        print("\\nRestored original orders.json")
    
    # Remove test files
    for f in ['orders.test.json', 'orders.test.result.json']:
        if os.path.exists(f):
            os.remove(f)

if __name__ == "__main__":
    try:
        print("=== SAFE OFFLINE TEST OF BOOKKEEPER MATCHING ===")
        print("Testing: Parent ? → Child 83 matching with current fixes")
        
        setup_test_environment()
        test_matching_logic()
        
        print("\\n=== TEST COMPLETE ===")
        print("Current implementation should:")
        print("1. ✅ Match parent ? to child 83 (price tolerance)")
        print("2. ✅ Copy trigger_basis: 'vwap_breakdown' to child")
        print("3. ✅ Use parent timestamp (14:42:31) for child")
        print("4. ⚠️ MAYBE remove parent ? (depends on logic)")
        print("5. ⚠️ Pick correct child (83 vs 71 based on time)")
        
    finally:
        cleanup()
#!/usr/bin/env python3
"""
Simple fix for bookkeeper.py:
1. Price tolerance for MARKET orders
2. Fix trigger_basis assignment (.get(key, default))
3. Use parent timestamp for children
"""

import re

# Read current bookkeeper.py
with open('bookkeeper.py', 'r') as f:
    content = f.read()

print("=== Current Issues ===")
print("1. Line 658: api_data[\"price\"] == local_price")
print("   Should be: (api_data[\"price\"] == local_price or local_price == 0.0)")
print()
print("2. Lines 614, 672: existing.get(\"trigger_basis\") or \"unknown\"")
print("   Should be: existing.get(\"trigger_basis\", \"unknown\")")
print()
print("3. Line 670: local_o.get(\"initiated_at\") or api_order_map[matched_api_order][\"order_date\"] or _now()")
print("   Should prefer parent timestamp over API date-only timestamp")
print()
print("4. Only matches FIRST child (line 662: break)")
print("   Need to handle multiple children summing to parent qty")

# Find line 658
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'api_data["price"] == local_price' in line and i > 640 and i < 670:
        print(f"Line {i+1}: {line}")
        # Check context
        print(f"Context:")
        for j in range(max(0, i-2), min(len(lines), i+3)):
            print(f"  {j+1}: {lines[j]}")
        print()

# Find lines 614 and 672
print("\nLooking for .get(\"trigger_basis\") or \"unknown\"")
for i, line in enumerate(lines):
    if '.get("trigger_basis") or "unknown"' in line:
        print(f"Line {i+1}: {line}")

# Find line 670
print("\nLooking for initiated_at assignment")
for i, line in enumerate(lines):
    if 'initiated_at": local_o.get("initiated_at") or api_order_map[matched_api_order]["order_date"] or _now()' in line:
        print(f"Line {i+1}: {line}")

print("\n=== Proposed Simple Fix ===")
print("1. Change price check to allow MARKET orders (price == 0.0)")
print("2. Change .get(\"trigger_basis\") or \"unknown\" to .get(\"trigger_basis\", \"unknown\")")
print("3. Keep current logic but at least fix price tolerance")
print("4. For multiple children, we need more complex logic")

# Actually, let's just fix the immediate issues first
print("\n=== Minimal Fix ===")
print("Change line 658 to:")
print("   if (api_data[\"symbol\"] == local_symbol and")
print("       api_data[\"side\"] == local_side and")
print("       api_data[\"qty\"] == local_qty and")
print("       (api_data[\"price\"] == local_price or local_price == 0.0)):")
print()
print("Change lines 614 and 672 to:")
print("   \"trigger_basis\": existing.get(\"trigger_basis\", \"unknown\"),")
print()
print("The multiple children issue requires more work")
#!/usr/bin/env python3
"""
Complete fix for bookkeeper.py to handle Derayah order splitting
"""

import re
from datetime import datetime

def apply_fix():
    with open('bookkeeper.py', 'r') as f:
        content = f.read()
    
    # Find the matching logic section
    lines = content.split('\n')
    
    # Find line numbers for the section we need to replace
    start_line = None
    end_line = None
    
    for i, line in enumerate(lines):
        if 'local_qty = local_o.get("qty", 0)' in line:
            start_line = i
        elif start_line is not None and 'if matched_api_order:' in line:
            end_line = i
            break
    
    if start_line is None or end_line is None:
        print("Could not find matching logic section")
        return
    
    print(f"Found section from line {start_line+1} to {end_line+1}")
    
    # Replace the entire section with new logic
    old_section = '\n'.join(lines[start_line:end_line])
    
    new_section = '''            local_qty = local_o.get("qty", 0)
            local_price = local_o.get("price", 0)
            
            # Special handling for orders with id "?" - Derayah may split them
            # Collect ALL matching child orders that sum to parent qty
            matched_children = []
            remaining_qty = local_qty
            
            for api_oid, api_data in api_order_map.items():
                if api_data["our_status"] == STATUS_FILLED:
                    # Check symbol, side match
                    if (api_data["symbol"] == local_symbol and 
                        api_data["side"] == local_side):
                        # Price tolerance for MARKET orders (price == 0.0)
                        price_match = (api_data["price"] == local_price or local_price == 0.0)
                        
                        # Check time window (±5 minutes) or date-only timestamp
                        time_match = False
                        api_time = api_data.get("order_date", "")
                        if api_time and local_time:
                            try:
                                local_dt = datetime.fromisoformat(local_time.replace('Z', '+00:00'))
                                api_dt = datetime.fromisoformat(api_time.replace('Z', '+00:00'))
                                time_diff = abs((api_dt - local_dt).total_seconds())
                                if time_diff <= 300:  # 5 minutes
                                    time_match = True
                            except Exception:
                                # If time parsing fails (e.g., date-only "2026-06-15")
                                # Check if it's today's date
                                try:
                                    local_date = local_time.split('T')[0]
                                    api_date = api_time.split('T')[0] if 'T' in api_time else api_time
                                    if local_date == api_date:
                                        time_match = True  # Same date, time parsing failed
                                except:
                                    time_match = True  # Allow match anyway
                        else:
                            time_match = True  # Missing timestamp, allow match
                        
                        # Check if child already has trigger_basis (don't overwrite)
                        child_trigger = api_data.get("trigger_basis", "")
                        if child_trigger and child_trigger != "unknown":
                            continue  # Child already has trigger_basis, skip
                        
                        # Check if this API order hasn't been matched yet
                        already_matched = any(m[0] == api_oid for m in matched_children)
                        
                        if price_match and time_match and not already_matched:
                            matched_children.append((api_oid, api_data))
                            remaining_qty -= api_data["qty"]
                            if remaining_qty <= 0:
                                break  # Found enough children to cover parent qty
            
            # Process matched children
            if matched_children and remaining_qty <= 0:
                # Found all child orders for this parent
                for child_oid, child_data in matched_children:
                    new_orders[child_oid] = {
                        "initiated_at": local_o.get("initiated_at") or child_data.get("order_date") or _now(),
                        "initiated_by": local_o.get("initiated_by") or "derayah-direct",
                        "trigger_basis": local_o.get("trigger_basis", "unknown"),
                        "trigger_detail": local_o.get("trigger_detail") or "",
                        "symbol": child_data["symbol"],
                        "side": child_data["side"],
                        "qty": child_data["qty"],
                        "price": child_data["price"],
                        "type": child_data["type"],
                        "status": STATUS_FILLED,
                        "updated_at": _now(),
                        "matched_from_api": True,
                        "original_order_id": oid,
                    }
                    transitions["status_changes"].append({
                        "order_id": child_oid, "old": STATUS_INITIATED, "new": STATUS_FILLED,
                        "symbol": local_o.get("symbol"), "side": local_o.get("side"),
                        "qty": local_o.get("qty"), "price": local_o.get("price"),
                    })
                # Don't add parent to new_orders - it's been matched to children
                continue
            else:
                matched_api_order = None'''
    
    # Replace the section
    new_lines = lines[:start_line] + new_section.split('\n') + lines[end_line:]
    
    # Also need to fix the trigger_basis lines (already done)
    # And fix the timestamp logic for matched children
    
    # Write back
    with open('bookkeeper.py', 'w') as f:
        f.write('\n'.join(new_lines))
    
    print("Fixed bookkeeper.py with new matching logic")
    print("Changes:")
    print("1. Collects ALL matching child orders (not just first)")
    print("2. Checks total qty matches parent qty")
    print("3. Skips children that already have trigger_basis")
    print("4. Uses parent timestamp for children")
    print("5. Removes parent from new_orders after matching")
    print("6. Copies parent trigger_basis to all matched children")

if __name__ == "__main__":
    apply_fix()
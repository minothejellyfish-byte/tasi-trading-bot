#!/usr/bin/env python3
"""
Implement the complete fix for bookkeeper.py
"""

import re
from datetime import datetime

def read_file():
    with open('bookkeeper.py', 'r') as f:
        return f.read()

def write_file(content):
    with open('bookkeeper.py', 'w') as f:
        f.write(content)

def find_section(content):
    """Find the INITIATED order matching section"""
    # Look for the specific pattern
    pattern = r'(            local_qty = local_o\.get\("qty", 0\)\s*\n.*?\n            if matched_api_order:)'
    match = re.search(pattern, content, re.DOTALL)
    
    if not match:
        # Try alternative pattern
        pattern = r'(local_qty = local_o\.get\("qty", 0\).*?if matched_api_order:)'
        match = re.search(pattern, content, re.DOTALL)
    
    return match

def apply_complete_fix():
    content = read_file()
    
    # Find the section to replace
    match = find_section(content)
    if not match:
        print("Could not find matching section")
        return False
    
    old_text = match.group(1)
    start_pos = match.start(1)
    
    print(f"Found section at position {start_pos}")
    print(f"Section length: {len(old_text)} chars")
    
    # New implementation
    new_text = '''            local_qty = local_o.get("qty", 0)
            local_price = local_o.get("price", x)
            
            # Special handling for orders with id "?" - Derayah may split into child orders
            # Find ALL matching child orders that sum to parent qty
            matched_children = []
            remaining_qty = local_qty
            
            # First pass: find potential matches
            potential_matches = []
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
                        
                        if price_match and time_match:
                            potential_matches.append((api_oid, api_data, api_time))
            
            # Sort by timestamp proximity (closest to parent time first)
            def get_time_score(api_time_str, local_time_str):
                """Lower score = closer in time"""
                if not api_time_str or not local_time_str:
                    return 999999  # Unknown time
                try:
                    local_dt = datetime.fromisoformat(local_time_str.replace('Z', '+00:00'))
                    api_dt = datetime.fromisoformat(api_time_str.replace('Z', '+00:00'))
                    return abs((api_dt - local_dt).total_seconds())
                except:
                    return 999998  # Parsing failed
            
            potential_matches.sort(key=lambda x: get_time_score(x[2], local_time))
            
            # Try to match qty exactly or with multiple children
            for api_oid, api_data, api_time in potential_matches:
                if api_data["qty"] == remaining_qty:
                    # Exact qty match
                    matched_children.append((api_oid, api_data))
                    remaining_qty = 0
                    break
                elif api_data["qty"] <= remaining_qty:
                    # Partial match (child order with smaller qty)
                    matched_children.append((api_oid, api_data))
                    remaining_qty -= api_data["qty"]
                    if remaining_qty <= 0:
                        break
            
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
                matched_api_order = matched_children[0][0] if matched_children else None
            else:
                matched_api_order = None
            
            if matched_api_order:'''

    # Replace the section
    new_content = content[:start_pos] + new_text + content[start_pos + len(old_text):]
    
    # Also need to add datetime import if not already there
    if 'from datetime import datetime' not in new_content:
        # Find imports section
        lines = new_content.split('\n')
        for i, line in enumerate(lines):
            if 'import' in line and 'datetime' not in line and i < 50:
                # Add import after this line
                lines.insert(i + 1, 'from datetime import datetime')
                new_content = '\n'.join(lines)
                break
    
    write_file(new_content)
    print("✅ Applied complete fix to bookkeeper.py")
    print("Changes implemented:")
    print("1. Collects ALL matching child orders (not just first)")
    print("2. Checks total qty matches parent qty (handles multiple children)")
    print("3. Sorts by timestamp proximity (closest match first)")
    print("4. Updates ALL matched children with parent metadata")
    print("5. Removes parent from new_orders after successful match")
    print("6. Uses parent timestamp for children (not 00:00:00)")
    
    return True

if __name__ == "__main__":
    # Backup first
    import shutil
    shutil.copy2('bookkeeper.py', 'bookkeeper.py.bak')
    print("Backed up to bookkeeper.py.bak")
    
    if apply_complete_fix():
        print("\n✅ Fix applied successfully")
        print("Test with: python3 -c \"import bookkeeper; bookkeeper.reconcile_orders()\"")
    else:
        print("❌ Failed to apply fix")
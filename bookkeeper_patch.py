#!/usr/bin/env python3
"""
Create patch for bookkeeper.py to fix Derayah order splitting
"""

patch_content = '''*** Begin Patch ***
--- /home/mino/tasi-exec/bookkeeper.py
+++ /home/mino/tasi-exec/bookkeeper.py
@@ -654,25 +654,69 @@
             local_qty = local_o.get("qty", 0)
             local_price = local_o.get("price", 0)
             
+            # For orders with id "?", we need special handling for Derayah splits
+            # Derayah may split MARKET orders into child orders with different IDs
+            # We need to find ALL matching child orders that sum to parent qty
+            matched_api_orders = []
+            remaining_qty = local_qty
+            
+            # First pass: collect potential matches
+            potential_matches = []
             for api_oid, api_data in api_order_map.items():
                 if api_data["our_status"] == STATUS_FILLED:
-                    # Check symbol, side, qty, price match
+                    # Check symbol, side match
                     if (api_data["symbol"] == local_symbol and 
-                        api_data["side"] == local_side and 
-                        api_data["qty"] == local_qty and
-                        api_data["price"] == local_price):
-                        # Check time window (±5 minutes)
+                        api_data["side"] == local_side):
+                        # Price tolerance for MARKET orders (price == 0.0)
+                        price_match = (api_data["price"] == local_price or local_price == 0.0)
+                        
+                        # Check time window (±5 minutes) or date-only timestamp
+                        time_match = False
                         api_time = api_data.get("order_date", "")
                         if api_time and local_time:
                             try:
                                 from datetime import datetime
-                                # Parse times (handle ISO format)
                                 local_dt = datetime.fromisoformat(local_time.replace('Z', '+00:00'))
                                 api_dt = datetime.fromisoformat(api_time.replace('Z', '+00:00'))
                                 time_diff = abs((api_dt - local_dt).total_seconds())
-                                if time_diff <= 300:  # 5 minutes = 300 seconds
-                                    matched_api_order = api_oid
-                                    break
+                                if time_diff <= 300:  # 5 minutes
+                                    time_match = True
                             except Exception:
-                                # If time parsing fails, match anyway (fallback)
-                                matched_api_order = api_oid
-                                break
+                                # If time parsing fails (e.g., date-only "2026-06-15")
+                                # Check if it's today's date
+                                try:
+                                    local_date = local_time.split('T')[0]
+                                    api_date = api_time.split('T')[0] if 'T' in api_time else api_time
+                                    if local_date == api_date:
+                                        time_match = True  # Same date, time parsing failed
+                                except:
+                                    time_match = True  # Allow match anyway
+                        else:
+                            time_match = True  # Missing timestamp, allow match
+                        
+                        # Check if this API order hasn't been matched yet
+                        already_matched = any(m[0] == api_oid for m in matched_api_orders)
+                        
+                        if price_match and time_match and not already_matched:
+                            potential_matches.append((api_oid, api_data))
+            
+            # Sort by timestamp proximity if possible
+            def get_timestamp_score(api_time_str, local_time_str):
+                """Lower score = closer in time"""
+                if not api_time_str or not local_time_str:
+                    return 999999  # Unknown time
+                try:
+                    local_dt = datetime.fromisoformat(local_time_str.replace('Z', '+00:00'))
+                    api_dt = datetime.fromisoformat(api_time_str.replace('Z', '+00:00'))
+                    return abs((api_dt - local_dt).total_seconds())
+                except:
+                    return 999998  # Parsing failed
+            
+            potential_matches.sort(key=lambda x: get_timestamp_score(x[1].get("order_date", ""), local_time))
+            
+            # Try to match qty exactly or find combination
+            for api_oid, api_data in potential_matches:
+                if api_data["qty"] == remaining_qty:
+                    # Exact qty match
+                    matched_api_orders.append((api_oid, api_data))
+                    remaining_qty = 0
+                    break
+                elif api_data["qty"] <= remaining_qty:
+                    # Partial match (child order with smaller qty)
+                    matched_api_orders.append((api_oid, api_data))
+                    remaining_qty -= api_data["qty"]
+                    if remaining_qty <= 0:
+                        break
+            
+            # If we found matches that sum to parent qty
+            if matched_api_orders and remaining_qty == 0:
+                matched_api_order = matched_api_orders[0][0]  # Use first match for backward compat
+                # We'll process all matches after the if statement
+            else:
+                matched_api_order = None
             
             if matched_api_order:
                 # Found matching FILLED order — update with real order ID and FILLED status
*** End Patch ***'''

print(patch_content)
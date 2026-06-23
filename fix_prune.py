#!/usr/bin/env python3
"""
Fix prune_orders_json_terminal() calls:
1. Remove from api_headers() (called too frequently)
2. Keep in reconcile_orders() (called after recording to history)
"""

with open('bookkeeper.py', 'r') as f:
    content = f.read()

# Remove prune call from api_headers
content = content.replace(
    '''def api_headers(tokens: dict = None) -> dict:
    if tokens is None:
        tokens = load_tokens()
    # Prune terminal orders from orders.json
    prune_orders_json_terminal()
    return {
        "Authorization": f"Bearer {tokens.get('TC_DERAYAH', '')}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }''',
    '''def api_headers(tokens: dict = None) -> dict:
    if tokens is None:
        tokens = load_tokens()
    return {
        "Authorization": f"Bearer {tokens.get('TC_DERAYAH', '')}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }'''
)

# Check if prune is called in reconcile_orders
if 'prune_orders_json_terminal()' in content:
    print('✅ prune_orders_json_terminal() is called in reconcile_orders()')
else:
    print('❌ prune_orders_json_terminal() NOT called in reconcile_orders()')
    # Add it
    if 'return {' in content:
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if line.strip().startswith('return {'):
                indent = len(line) - len(line.lstrip())
                spaces = ' ' * indent
                lines.insert(i, f'{spaces}# Prune terminal orders from orders.json')
                lines.insert(i + 1, f'{spaces}prune_orders_json_terminal()')
                content = '\n'.join(lines)
                break

with open('bookkeeper.py', 'w') as f:
    f.write(content)

print('Fixed prune_orders_json_terminal() calls:')
print('1. ✅ Removed from api_headers() (not called on every API request)')
print('2. ✅ Called in reconcile_orders() after recording to history')
print()
print('Now orders.json will be pruned of terminal orders after each reconciliation.')
print('Terminal orders (REJECTED, FILLED, EXPIRED) removed, only outstanding orders kept.')
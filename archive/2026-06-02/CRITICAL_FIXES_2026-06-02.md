# TASI System Critical Fixes - June 2, 2026

## Summary of Critical Issues

1. **WebSocket Connection Failures**: Repeated "Handshake status 500 Internal Server Error" and "No such target id" errors
2. **TickerChart Recovery Failures**: Both smart recovery and fallback navigation methods failing
3. **Token Expiration**: Authentication tokens not being refreshed properly
4. **Poller Service Issues**: WebSocket connection errors and order placement errors
5. **Trading Errors**: Multiple order placement errors including insufficient funds and quantity mismatches

## Immediate Actions Required

### 1. Restart Chrome Browser Instance
```bash
# Kill existing Chrome processes
pkill -f "chrome.*derayah-profile"

# Wait for processes to terminate
sleep 5

# Start fresh Chrome instance
cd /home/mino/tasi-exec
./start-chrome.sh
```

### 2. Refresh Authentication Tokens
```bash
# Remove expired token files
rm -f /home/mino/tasi-exec/derayah_token_live.json

# Run token refresh script (if available)
# python3 /home/mino/tasi-exec/refresh_tokens.py
```

### 3. Reset WebSocket Connection
```bash
# Kill existing WebSocket probe processes
pkill -f "ws_probe.py"

# Restart WebSocket probe
cd /home/mino/tasi-exec
nohup python3 ws_probe.py 90 >> /home/mino/tasi-exec/ws_probe.log 2>&1 &
```

### 4. Restart Poller Service
```bash
# Stop current poller
pkill -f "poller.py"

# Clear any corrupted data files
> /home/mino/tasi-exec/ws_frames.json

# Restart poller service
cd /home/mino/tasi-exec
./start_poller.sh
```

### 5. Clear Stand Down Mode
```bash
# Remove stand down file to allow trading
rm -f /home/mino/tasi-exec/stand_down
```

## Code Fixes Required

### 1. WebSocket Connection Handling (derayah_keepalive.py)
- Improve error handling for "No such target id" errors
- Add retry mechanism with exponential backoff
- Implement better tab recovery logic

### 2. Token Management (derayah_api.py)
- Add automatic token refresh before expiration
- Implement fallback authentication methods
- Add better error reporting for token issues

### 3. Order Placement Logic (poller.py)
- Fix decimal place handling for order prices
- Improve quantity calculation to prevent "greater than remaining" errors
- Add better validation before order submission

### 4. Capital Tracking (capital_tracker.py)
- Fix capital calculation to prevent insufficient funds errors
- Add synchronization with actual account balance
- Implement better error handling for capital-related issues

## Long-term Improvements

1. **Implement Robust Error Recovery**: Add comprehensive error handling and recovery mechanisms for all critical components
2. **Enhance Monitoring**: Add more detailed logging and alerting for critical system components
3. **Improve Test Coverage**: Add unit tests for WebSocket connections, token management, and order placement
4. **Add Circuit Breakers**: Implement circuit breakers to prevent cascading failures
5. **Regular Maintenance**: Schedule regular system maintenance to prevent token expiration and connection issues

## Verification Steps

1. Confirm Chrome browser starts successfully
2. Verify WebSocket connection is established
3. Check that TickerChart tab is accessible
4. Confirm authentication tokens are valid
5. Validate that poller service is running without errors
6. Test order placement functionality
7. Verify capital tracking is accurate

## Rollback Plan

If fixes cause additional issues:
1. Restore previous token files from backup
2. Revert code changes
3. Restart services with previous configuration
4. Manually verify system functionality
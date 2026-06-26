#!/bin/bash
# IB Gateway Startup Script for Derayah Global
# Runs IB Gateway in headless mode with API enabled

IB_GATEWAY_DIR="/home/mino/Jts/ibgateway/1045"
IB_GATEWAY_EXE="$IB_GATEWAY_DIR/ibgateway"

# IB Gateway config
export IBC_TWS_VERSION=1045
export IBC_IBC_DIR="/home/mino/ib-controller"
export IBC_CONFIG="$IBC_IBC_DIR/config.ini"

# API settings (will be configured in IBC)
export TWS_API_PORT=5000
export TWS_API_HOST=127.0.0.1

# Run IB Gateway
cd "$IB_GATEWAY_DIR"
exec "$IB_GATEWAY_EXE" "${TWS_API_PORT}" "${TWS_API_HOST}"

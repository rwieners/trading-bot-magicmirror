#!/bin/bash
# Quick trade status viewer with auto-refresh
cd /Users/rene/dev/Broker
while true; do
    clear
    python3 scripts/view_trades.py
    echo ""
    echo "Refreshing in 10 seconds... (Press Ctrl+C to exit)"
    sleep 10
done

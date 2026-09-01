#!/bin/bash

echo "=== DN WATCH ==="
pkill -f "dn_bot.py --watch" 2>/dev/null || true
sleep 1
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/hyperliquid"
nohup python dn_bot.py --watch > dn_watch.log 2>&1 &

echo "=== KRAKEN XSTOCK WATCH (ALERTS only — no auto-start!) ==="
# XSTOCK_AUTOSTART=false is MANDATORY locally: the server starts the real bot on an
# allocation; two watchers with auto-start = TWO bots on the same SPCX position.
pkill -f kraken_xstock_watch.py 2>/dev/null || true
sleep 1
cd "$ROOT/kraken"
XSTOCK_AUTOSTART=false nohup python kraken_xstock_watch.py > kraken_xstock_watch.log 2>&1 &

echo "DONE"

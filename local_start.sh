#!/bin/bash

echo "=== DN WATCH ==="
pkill -f "dn_bot.py --watch" 2>/dev/null || true
sleep 1
cd ~/binance/hyperliquid
nohup python3 dn_bot.py --watch > dn_watch.log 2>&1 &

echo "=== KRAKEN XSTOCK WATCH (doar ALERTE — fara auto-start!) ==="
# XSTOCK_AUTOSTART=false e OBLIGATORIU local: serverul porneste botul real la
# alocare; doua watchere cu auto-start = DOUA boturi pe aceeasi pozitie SPCX.
pkill -f xstock_watch.py 2>/dev/null || true
sleep 1
cd ~/binance/kraken
XSTOCK_AUTOSTART=false nohup python3 xstock_watch.py > xstock_watch.log 2>&1 &

echo "DONE"

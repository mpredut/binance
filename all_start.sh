#!/bin/bash

echo "=== RESTART BINANCE ==="
#sudo systemctl restart binance

echo "=== DN BOT ==="
pkill -f dn_bot.py 2>/dev/null || true
sleep 1
cd ~/binance/hyperliquid
nohup python3 dn_bot.py > dn_bot.log 2>&1 &

echo "=== DN WATCH ==="
cd ~/binance/hyperliquid
pkill -f "dn_bot.py --watch" 2>/dev/null || true
sleep 1
nohup python3 dn_bot.py --watch > dn_watch.log 2>&1 &

echo "=== KRAKEN BOT ==="
pkill -f kraken_bot.py 2>/dev/null || true
sleep 1
cd ~/binance/kraken
nohup python3 kraken_bot.py > kraken_bot.log 2>&1 &

echo "=== KRAKEN XSTOCK WATCH ==="
pkill -f xstock_watch.py 2>/dev/null || true
sleep 1
cd ~/binance/kraken
nohup python3 xstock_watch.py > xstock_watch.log 2>&1 &

echo "=== IPO NVDA ==="
pkill -f "ipo.py --profile nvda" 2>/dev/null || true
sleep 1
cd ~/binance/121trade
nohup python3 ipo.py --profile nvda > nvda.log 2>&1 &

echo "=== IPO SPCX ==="
pkill -f "ipo.py --profile spcx" 2>/dev/null || true
sleep 1
cd ~/binance/121trade
nohup python3 ipo.py --profile spcx > spcx.log 2>&1 &


echo "DONE"

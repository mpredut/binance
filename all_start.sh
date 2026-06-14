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
# NU sterge .state_HYPEUSD.json: botul isi reia pozitia din el; fara stare ar
# porni curat si ar CUMPARA o intrare noua peste pozitia veche (incidentul de 10 iun).
# pkill prinde si eventualul bot SPCX pornit de watcher — watchdog-ul il
# reporneste singur in <1 min, e ok.
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


echo "=== KRAKEN TRAILING (protectie crash HYPE) — momentan OPRIT ==="
# Disjunctor pe HYPE-ul cumparat MANUAL: vinde balanta LIBERA daca pretul cade
# -15% de la varf (nu atinge cei 3.38 ai botului, blocati in TP). Cand vrei:
#   1. decomenteaza cele 3 linii de mai jos FARA flag-ul ENABLED (ruleaza dry-run,
#      doar logheaza ce-ar vinde) si lasa-l o zi sa vezi ca e sanatos;
#   2. apoi adauga "KRAKEN_TRAILING_ENABLED=true " inainte de nohup ca sa vanda real.
# Lansat cu cale (kraken/trailing_stop.py) ca pkill sa NU prinda si trailing-ul din
# radacina (acelasi nume de fisier -> proces identic in ps).
# pkill -f "kraken/trailing_stop.py" 2>/dev/null || true
# sleep 1
# cd ~/binance && nohup python3 kraken/trailing_stop.py > kraken/trail_k.log 2>&1 &

echo "DONE"

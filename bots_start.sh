#!/bin/bash

# python cu SDK Hyperliquid (eth_account): dn_bot ARE nevoie de el; python3 de
# sistem NU il are. Prefera venv-ul, cade pe python3 (kraken/ipo merg si pe python3).
HLPY="$HOME/binance/myenv/bin/python"
{ [ -x "$HLPY" ] && "$HLPY" -c "import eth_account" 2>/dev/null; } || HLPY=python3

echo "=== RESTART BINANCE ==="
#sudo systemctl restart binance

echo "=== DN BOT ==="
pkill -f dn_bot.py 2>/dev/null || true
sleep 1
cd ~/binance/hyperliquid
nohup $HLPY dn_bot.py > dn_bot.log 2>&1 &

echo "=== DN WATCH ==="
cd ~/binance/hyperliquid
pkill -f "dn_bot.py --watch" 2>/dev/null || true
sleep 1
nohup $HLPY dn_bot.py --watch > dn_watch.log 2>&1 &

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
pkill -f kraken_xstock_watch.py 2>/dev/null || true
sleep 1
cd ~/binance/kraken
nohup python3 kraken_xstock_watch.py > kraken_xstock_watch.log 2>&1 &

echo "=== T212 BOT (toate activele din config.*.env, UN proces) ==="
# Inlocuieste cele 2 procese ipo.py --profile. Adaugi un activ = creezi
# config.<activ>.env (fara linie noua aici). Vechiul ipo.py ramane pt rulari manuale.
pkill -f "t212_bot.py" 2>/dev/null || true
pkill -f "ipo.py --profile" 2>/dev/null || true   # opreste si vechile procese, daca mai ruleaza
sleep 1
cd ~/binance/212trading
nohup python3 t212_bot.py > t212_bot.log 2>&1 &


echo "=== KRAKEN TRAILING (protectie crash HYPE) — LIVE (vinde real) ==="
# Disjunctor pe HYPE-ul cumparat MANUAL: vinde balanta LIBERA daca pretul cade
# -15% de la varf (nu atinge pozitia botului, blocata in TP). ACTIV prin
# KRAKEN_TRAILING_ENABLED=true (scoate flag-ul ca sa revii la dry-run, care doar logheaza).
# Lansat cu cale (kraken/trailing_stop.py) ca pkill sa NU prinda si trailing-ul din
# radacina (acelasi nume de fisier -> proces identic in ps).
 pkill -f "kraken/trailing_stop.py" 2>/dev/null || true
 sleep 1
 cd ~/binance && KRAKEN_TRAILING_ENABLED=true nohup python3 kraken/trailing_stop.py > kraken/trail_k.log 2>&1 &

echo "DONE"

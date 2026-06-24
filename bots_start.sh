#!/bin/bash

# python cu SDK Hyperliquid (eth_account): dn_bot ARE nevoie de el; python3 de
# sistem NU il are. Prefera venv-ul, cade pe python3 (kraken/ipo merg si pe python3).
ROOT="$HOME/binance"
_venv=""
for _d in ".venv" "myenv"; do [ -f "$ROOT/$_d/bin/activate" ] && _venv="$_d" && break; done
HLPY="$ROOT/$_venv/bin/python"
{ [ -x "$HLPY" ] && "$HLPY" -c "import eth_account" 2>/dev/null; } || HLPY=python3

echo "=== RESTART BINANCE ==="
#sudo systemctl restart binance

echo "=== DN BOT ==="
pkill -f dn_bot.py 2>/dev/null || true
sleep 1
cd ~/binance/hyperliquid
# myenv (eth_account) via activate -> cmdline curat "python3 dn_bot.py" (nu cale hardcodata)
( source "$ROOT/$_venv/bin/activate" && nohup python3 dn_bot.py > dn_bot.log 2>&1 & )

echo "=== DN WATCH ==="
cd ~/binance/hyperliquid
pkill -f "dn_bot.py --watch" 2>/dev/null || true
sleep 1
( source "$ROOT/$_venv/bin/activate" && nohup python3 dn_bot.py --watch > dn_watch.log 2>&1 & )

echo "=== KRAKEN CACHEMANAGER (fills partajat cross-proces, HYPE multi-proces) ==="
# Tine fills-urile Kraken intr-un fisier comun (cachedb/cache_trade_kraken.json) ca toate
# procesele de trading HYPE sa vada aceleasi tranzactii (gard corect cross-proces) + un
# singur fetcher (rate-limit). Mod 'poll' (REST) implicit; 'ws' real-time cu KRAKEN_CACHE_MODE=ws.
# Cheia _WS dedicata (nonce separat). Pornit INAINTEA botilor ca fisierul sa existe la citire.
pkill -f kraken_cachemanager.py 2>/dev/null || true
sleep 1
cd ~/binance/kraken
nohup python3 kraken_cachemanager.py > kraken_cachemanager.log 2>&1 &

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

echo "=== BINANCE TRAILING (protectie crash BTC/TAO) — LIVE (vinde real) ==="
# Disjunctor pe holdingurile Binance: vinde balanta LIBERA BTC/TAO daca pretul cade
# -20/-22% de la varf (force=True; NU atinge pozitiile din ordinele TP). ACTIV prin
# TRAILING_ENABLED=true (scoate flag-ul -> dry-run). Ruleaza cu myenv (SDK Binance) prin
# activate -> cmdline curat "python3 binance_api/trailing_stop.py".
 pkill -f "binance_api/trailing_stop.py" 2>/dev/null || true
 sleep 1
 # config in binance_api/trailing.conf (nu mai e in env)
 ( cd "$ROOT" && source "$ROOT/$_venv/bin/activate" && \
   nohup python3 binance_api/trailing_stop.py > binance_api/trail_b.log 2>&1 & )

echo "DONE"

#!/bin/bash
# bots_start.sh — porneste TOTI botii (role=bot) din manifestul UNIC procs.conf.
# Sursa unica de adevar (acelasi fisier citit de flota_start.sh + healthcheck.sh):
# adaugi/scoti/modifici un bot -> editezi procs.conf, NU acest fisier.
#
# NOTE pastrate din versiunea per-bot:
#  - NU stergem fisiere de stare (.state_*.json): botii isi reiau pozitia din ele
#    (un start "curat" ar cumpara o intrare noua peste pozitia veche). Aici doar pkill+restart.
#  - Ordinea din procs.conf conteaza: kraken_cachemanager INAINTEA kraken_bot (fisierul de
#    fills trebuie sa existe la prima citire).
#  - dn_bot / binance trailing au nevoie de venv (eth_account / SDK Binance) — comanda lor
#    din manifest face 'source $VENV/bin/activate' inline; kraken/t212 merg pe python3 de sistem.
ROOT="$HOME/binance"
MANIFEST="$ROOT/procs.conf"
# venv cu SDK-urile (prefera .venv, cade pe myenv) — expandat in comenzile din manifest ($VENV)
VENV=""
for _d in ".venv" "myenv"; do [ -f "$ROOT/$_d/bin/activate" ] && VENV="$_d" && break; done

[ -f "$MANIFEST" ] || { echo "❌ lipseste $MANIFEST"; exit 1; }

# Curatenie legacy: vechile procese 'ipo.py --profile' (inlocuite de t212_bot.py)
pkill -f "ipo.py --profile" 2>/dev/null || true

while IFS='|' read -r pat dir cmd label hblog hbstale role; do
    [ -z "$pat" ] && continue
    case "$pat" in \#*) continue;; esac
    [ "$role" = bot ] || continue
    dir=$(eval echo "$dir")
    echo "=== $label ==="
    pkill -f "$pat" 2>/dev/null || true
    sleep 1
    ( cd "$dir" && eval "$cmd" )   # $ROOT/$VENV expandate aici; comanda backgroundeaza singura (&)
done < "$MANIFEST"

echo "DONE — boti porniti din $MANIFEST"

#!/bin/bash
# bots_start.sh — starts ALL bots (role=bot) from the SINGLE manifest procs.conf.
# Sursa unica de adevar (acelasi fisier citit de flota_start.sh + healthcheck.sh):
# To add/remove/change a bot, edit procs.conf, NOT this file.
#
# NOTE pastrate din versiunea per-bot:
#  - We do NOT delete state files (.state_*.json): the bots resume their position from
#    them (a "clean" start would buy a new entry on top of the old position). Just pkill+restart.
#  - Ordinea din procs.conf conteaza: kraken_cachemanager INAINTEA kraken_bot (fisierul de
#    fills trebuie sa existe la prima citire).
#  - dn_bot / binance trailing au nevoie de venv (eth_account / SDK Binance) — comanda lor
#    from the manifest does 'source $VENV/bin/activate' inline; kraken/t212 run on system python3.
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

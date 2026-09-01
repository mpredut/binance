#!/bin/bash
# bots_start.sh — starts ALL bots (role=bot) from the SINGLE manifest procs.conf.
# The single source of truth (the same file read by flota_start.sh plus healthcheck.sh):
# To add/remove/change a bot, edit procs.conf, NOT this file.
#
# Notes kept from the per-bot version:
#  - We do NOT delete state files (.state_*.json): the bots resume their position from
#    them (a "clean" start would buy a new entry on top of the old position). Just pkill+restart.
#  - The order in procs.conf matters: kraken_cachemanager BEFORE kraken_bot (the fills
#    file has to exist at the first read).
#  - dn_bot / binance trailing au nevoie de venv (eth_account / SDK Binance) — comanda lor
#    from the manifest does 'source $VENV/bin/activate' inline; kraken/t212 run on system python3.
ROOT="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$ROOT/procs.conf"
# The venv with the SDKs (it prefers .venv and falls back to myenv) — expanded in the manifest's commands ($VENV)
VENV=""
for _d in ".venv" "myenv"; do [ -f "$ROOT/$_d/bin/activate" ] && VENV="$_d" && break; done

[ -f "$MANIFEST" ] || { echo "❌ is missing $MANIFEST"; exit 1; }

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

echo "DONE — bots started from $MANIFEST"

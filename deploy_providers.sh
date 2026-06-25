#!/usr/bin/env bash
# deploy_providers.sh — DEPLOY sigur de cod: git pull -> GATE de import (facada) -> restart
# flota -> verificare. Singurul script care face deploy (flota_start/bots_start=launchere,
# healthcheck=supervizor). Lista flotei vine din procs.conf (role=fleet) — NU mai e hardcodata.
# Restart flota = pkill procesele role=fleet; flota_start (systemd) le reia in <=30s cu codul nou.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/myenv/bin/python"; [ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
MANIFEST="$ROOT/procs.conf"
cd "$ROOT" || exit 1

echo "=== PULL ==="
git pull --ff-only origin main 2>&1 | tail -5

echo "=== SANITY (structura providers) ==="
ls providers/market_api.py binance_api/trailing_stop.py >/dev/null && echo "  providers ok"
ls market_api.py 2>/dev/null && echo "  ⚠ ROOT INCA are market_api.py" || echo "  root curat ok"

echo "=== GATE import facada (nu repornesc daca nu se incarca) ==="
"$PY" -c 'from providers.market_api import api; print("  facada OK -", len(api._providers), "provideri:", [p.name for p in api._providers])' || { echo "  GATE FAILED — NU repornesc"; exit 1; }

# Lista flotei din manifestul UNIC (role=fleet), nu hardcodata.
fleet="$(awk -F'|' '!/^#/ && $7=="fleet" {print $1}' "$MANIFEST")"
[ -n "$fleet" ] || { echo "fara role=fleet in $MANIFEST"; exit 1; }

echo "=== RESTART FLOTA (pkill; flota_start le reia) ==="
for p in $fleet; do pkill -f "$p" 2>/dev/null || true; done
echo "  killed; astept 95s..."; sleep 95

echo "=== VERIFICARE ==="
"$PY" verify_tools/check_cache_coherence.py >/tmp/coh.log 2>&1 || true
echo "  coherence: $(tail -1 /tmp/coh.log 2>/dev/null)"
for p in $fleet; do printf '  %-22s viu=%s\n' "$p" "$(pgrep -fc "$p")"; done
echo "  Traceback (monitortrades/cacheManager): $(grep -a -c Traceback logs/monitortrades.log logs/cacheManager.log 2>/dev/null | paste -sd' ')"
echo "  trailing viu=$(pgrep -fc trailing_stop.py)"

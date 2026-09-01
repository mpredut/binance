#!/usr/bin/env bash
# deploy_providers.sh — DEPLOY sigur de cod: git pull -> GATE de import (facada) -> restart
# fleet -> verification. The only script that deploys (flota_start/bots_start are launchers,
# healthcheck is the supervisor). The fleet list comes from procs.conf (role=fleet), not hardcoded.
# A fleet restart = pkill the role=fleet processes; flota_start (systemd) brings them back in <=30s with the new code.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/myenv/bin/python"; [ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
MANIFEST="$ROOT/procs.conf"
cd "$ROOT" || exit 1

echo "=== PULL ==="
git pull --ff-only origin main 2>&1 | tail -5

echo "=== SANITY (structura providers) ==="
ls providers/market_api.py binance_api/trailing_stop.py >/dev/null && echo "  providers ok"
ls market_api.py 2>/dev/null && echo "  ⚠ ROOT STILL has market_api.py" || echo "  root is clean, ok"

echo "=== facade import GATE (no restart if it does not load) ==="
"$PY" -c 'from providers.market_api import api; print("  facade OK -", len(api._providers), "providers:", [p.name for p in api._providers])' || { echo "  GATE FAILED — NOT restarting"; exit 1; }

# The fleet list comes from the SINGLE manifest (role=fleet), not hardcoded.
fleet="$(awk -F'|' '!/^#/ && $7=="fleet" {print $1}' "$MANIFEST")"
[ -n "$fleet" ] || { echo "no role=fleet in $MANIFEST"; exit 1; }

echo "=== RESTART FLOTA (pkill; flota_start le reia) ==="
for p in $fleet; do pkill -f "$p" 2>/dev/null || true; done
echo "  killed; waiting 95s..."; sleep 95

echo "=== VERIFICARE ==="
"$PY" verify_tools/check_cache_coherence.py >/tmp/coh.log 2>&1 || true
echo "  coherence: $(tail -1 /tmp/coh.log 2>/dev/null)"
for p in $fleet; do printf '  %-22s viu=%s\n' "$p" "$(pgrep -fc "$p")"; done
echo "  Traceback (monitortrades/cacheManager): $(grep -a -c Traceback logs/monitortrades.log logs/cacheManager.log 2>/dev/null | paste -sd' ')"
echo "  trailing alive=$(pgrep -fc trailing_stop.py)"

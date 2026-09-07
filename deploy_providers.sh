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

# Bots (role=bot) are NOT under systemd, so the fleet restart above leaves them on
# OLD code — the trailing stop kept running the pre-change revision until a manual
# pkill. Reload them the sanctioned way: pkill + bots_start.sh (single-instance safe;
# each bot reloads its own persisted state, so this is a CODE reload, not a state
# reset — see bots_start.sh). This closes the gap where a pulled bot never ran the
# new code after a deploy.
bots="$(awk -F'|' '!/^#/ && $7=="bot" {print $1}' "$MANIFEST")"
if [ -n "$bots" ]; then
  echo "=== RELOAD BOTS (pkill; bots_start.sh le reia cu codul nou) ==="
  for p in $bots; do pkill -f "$p" 2>/dev/null || true; done
  echo "  killed; waiting 8s..."; sleep 8
  # Redirect to a FILE, not a pipe. bots_start launches daemons that inherit stdout;
  # piping it (| tail) leaves the pipe's write end open in those daemons, so tail
  # never sees EOF and the deploy hangs. A file has no such semantics; timeout guards
  # a genuinely stuck launcher.
  timeout 60 bash "$ROOT/bots_start.sh" >/tmp/deploy_bots_start.log 2>&1 || true
  tail -3 /tmp/deploy_bots_start.log 2>/dev/null
  sleep 6
fi

echo "=== VERIFICARE ==="
"$PY" verify_tools/check_cache_coherence.py >/tmp/coh.log 2>&1 || true
echo "  coherence: $(tail -1 /tmp/coh.log 2>/dev/null)"
for p in $fleet; do printf '  %-24s viu=%s\n' "$p" "$(pgrep -fc "$p")"; done
for p in $bots;  do printf '  %-24s viu=%s\n' "$p" "$(pgrep -fc "$p")"; done
echo "  Traceback (monitortrades/cacheManager): $(grep -a -c Traceback logs/monitortrades.log logs/cacheManager.log 2>/dev/null | paste -sd' ')"

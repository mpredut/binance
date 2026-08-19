#!/usr/bin/env bash
# run_backtest_cycle.sh — un ciclu complet de backtest pe DEV: ruleaza pilotul in
# mod --propose (nu aplica nimic) si publica propunerile pe branch-ul git. Ruleaza
# pe DEV. Declansat de pe prod (trigger_backtest_dev.sh via ssh) sau manual.
#
# Datele (cachedb) si codul (git pull) sunt aduse la zi de refresh_dev.sh RULAT DE
# PE PROD inainte de a declansa acest ciclu — aici doar rulam si publicam.
set -euo pipefail

RUNNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${BINANCE_REPO_ROOT:-${ROOT:-$(cd "$RUNNER_DIR/../.." && pwd)}}"
ONLY="${PILOT_ONLY:-}"            # gol = toate cheile; ex. "maxage,hardtp"
cd "$REPO_ROOT"

echo "[cycle $(date '+%F %T')] pilot --propose (only='${ONLY:-toate}')"
args=(--propose)
[ -n "$ONLY" ] && args+=(--only "$ONLY")
./myenv/bin/python offline/research/monitortrades_backtest/scheduled_pilot.py "${args[@]}"

echo "[cycle $(date '+%F %T')] publish propuneri pe git"
"$REPO_ROOT/offline/runners/publish_proposals.sh"

echo "[cycle $(date '+%F %T')] gata"

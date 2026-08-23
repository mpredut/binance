#!/usr/bin/env bash
# trigger_backtest_dev.sh — declanseaza un ciclu COMPLET backtest->propunere->aplicare,
# de pe PROD. Executia grea (backtest) e pe DEV (CPU offload de pe masina de trading);
# aplicarea (scrie instruments.conf) ramane pe PROD, unde traieste config-ul live.
#
#   1. refresh_dev.sh          — sync cod (git pull ff-only) + date (rsync cachedb) prod->dev
#   2. ssh dev run_backtest_cycle.sh — pilot --propose + publish propuneri pe git
#   3. apply_proposals.py (LOCAL, pe prod) — trage propunerile de pe branch git si le
#      APLICA automat, cu TOATE guardrail-urile existente (confirmare pe 2 ferestre pe
#      dev + marja minima vs valoarea inerta, medie/amortizare cu valoarea live curenta,
#      rate-limit 7 zile/cheie, audit persistent, notificare). Watchdogfor_cacheandconfig
#      (cron separat) detecteaza schimbarea de instruments.conf si reporneste
#      monitortrades.py automat. NIMIC din asta nu ocoleste guardrail-urile — doar
#      elimina pasul manual "ruleaza apply_proposals.py cand vezi notificarea".
#
# Kill-switch pt DOAR pasul de aplicare (backtest+propunerile tot ruleaza): APPLY_DISABLED=true
# (env, citit de apply_proposals.py insusi — merge si la o rulare manuala separata).
# Kill-switch pt TOT ciclul (inclusiv backtest-ul): PILOT_DISABLED=true (scheduled_pilot).
#
# Ruleaza pe PROD. PILOT_ONLY (env, optional) limiteaza cheile testate (ex. "maxage,hardtp").
set -euo pipefail

RUNNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a; source "$RUNNER_DIR/dev_backtest.env"; set +a
REPO_ROOT="${BINANCE_REPO_ROOT:-$(cd "$RUNNER_DIR/../.." && pwd)}"
DEV_HOST="${DEV_HOST:-192.168.0.138}"; DEV_PORT="${DEV_PORT:-32238}"; DEV_USER="${DEV_USER:-predut}"
SSH="ssh -o BatchMode=yes -p $DEV_PORT"

echo "[trigger $(date '+%F %T')] 1/3 refresh dev (sync cod+date)"
"$REPO_ROOT/offline/runners/refresh_dev.sh"

echo "[trigger $(date '+%F %T')] 2/3 ruleaza ciclul de backtest pe dev"
$SSH "$DEV_USER@$DEV_HOST" "PILOT_ONLY='${PILOT_ONLY:-}' ~/$DEV_PATH/offline/runners/run_backtest_cycle.sh"

echo "[trigger $(date '+%F %T')] 3/3 aplica propunerile pe PROD (guardrail-uri: rate-limit/medie/audit)"
cd "$REPO_ROOT" && ./myenv/bin/python offline/runners/apply_proposals.py

echo "[trigger $(date '+%F %T')] gata."

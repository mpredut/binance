#!/usr/bin/env bash
# trigger_backtest_dev.sh — declanseaza un ciclu de backtest pe DEV, DE PE PROD.
# Asta e ce "porneste backtesturile" (cron pe prod), dar EXECUTIA e pe dev (CPU
# offload de pe masina de trading). NU aplica nimic pe prod — doar produce
# propuneri pe branch-ul git. Aplicarea e un pas separat, deliberat (apply_proposals.py).
#
#   1. refresh_dev.sh          — sync cod (git pull ff-only) + date (rsync cachedb) prod->dev
#   2. ssh dev run_backtest_cycle.sh — pilot --propose + publish propuneri pe git
#
# Ruleaza pe PROD. PILOT_ONLY (env, optional) limiteaza cheile (ex. "maxage,hardtp").
set -euo pipefail

SRC="/home/predut/binance"
DEV_HOST="${DEV_HOST:-192.168.0.138}"; DEV_PORT="${DEV_PORT:-32238}"; DEV_USER="${DEV_USER:-predut}"
SSH="ssh -o BatchMode=yes -p $DEV_PORT"

echo "[trigger $(date '+%F %T')] 1/2 refresh dev (sync cod+date)"
"$SRC/refresh_dev.sh"

echo "[trigger $(date '+%F %T')] 2/2 ruleaza ciclul de backtest pe dev"
$SSH "$DEV_USER@$DEV_HOST" "PILOT_ONLY='${PILOT_ONLY:-}' ~/binance/run_backtest_cycle.sh"

echo "[trigger $(date '+%F %T')] gata — propunerile sunt pe branch git backtest-proposals."
echo "  Aplicarea pe prod (cu guardrail-uri) = apply_proposals.py, rulat separat/deliberat."

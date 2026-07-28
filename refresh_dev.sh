#!/usr/bin/env bash
# refresh_dev.sh — aduce masina de BACKTEST (dev) la zi fata de prod, pe 2 canale:
#   1. COD: `git pull --ff-only` pe dev (codul circula prin github, nu prin script).
#   2. DATE (cachedb, netrackuit de git): rsync prod -> dev. Doar arhivele de pret
#      (cache_price_*.jsonl) sunt strict necesare backtestului; sincronizam tot
#      cachedb ca dev sa fie o oglinda reala (rsync transfera doar delta, ieftin).
#
# Ruleaza pe PROD (are cheia SSH spre dev). NU atinge prod. NU face git pull pe
# prod (masina live cu bani reali — codul se trage acolo DELIBERAT, niciodata auto).
# Idempotent; sigur de pus pe cron pe prod.
set -euo pipefail

DEV_HOST="${DEV_HOST:-192.168.0.138}"
DEV_PORT="${DEV_PORT:-32238}"
DEV_USER="${DEV_USER:-predut}"
DEV_PATH="${DEV_PATH:-binance}"          # relativ la ~ pe dev
SRC="/home/predut/binance"
SSH="ssh -o BatchMode=yes -p $DEV_PORT"

echo "[refresh_dev $(date '+%F %T')] cod: git pull --ff-only pe dev"
$SSH "$DEV_USER@$DEV_HOST" "cd ~/$DEV_PATH && git pull --ff-only origin main" 2>&1 | sed 's/^/  /'

echo "[refresh_dev $(date '+%F %T')] date: rsync cachedb/ prod -> dev"
rsync -a --info=stats1 -e "$SSH" \
  "$SRC/cachedb/" "$DEV_USER@$DEV_HOST:$DEV_PATH/cachedb/" 2>&1 | sed 's/^/  /'

echo "[refresh_dev $(date '+%F %T')] gata"

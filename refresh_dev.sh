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

# MUTEX (4 aug): refresh_dev poate fi pornit CONCURENT — o data din cron-ul propriu (*/30,
# deci si la 00:00/12:00) SI o data apelat de trigger_backtest_dev.sh (0 */12 = 00:00/12:00).
# Doua `git pull --ff-only` simultane pe dev -> FETCH_HEAD scris de amandoua -> 'Cannot
# fast-forward to multiple branches'. flock serializeaza: al doilea asteapta (max 300s) sa
# termine primul, apoi ruleaza (git pull devine 'already up to date', ieftin). Fara curse.
exec 9>"/tmp/refresh_dev.lock"
if ! flock -w 300 9; then
  echo "[refresh_dev $(date '+%F %T')] alt refresh_dev ruleaza de >300s — sar peste"
  exit 0
fi

DEV_HOST="${DEV_HOST:-192.168.0.138}"
DEV_PORT="${DEV_PORT:-32238}"
DEV_USER="${DEV_USER:-predut}"
DEV_PATH="${DEV_PATH:-binance}"          # relativ la ~ pe dev
SRC="/home/predut/binance"
SSH="ssh -o BatchMode=yes -p $DEV_PORT"

echo "[refresh_dev $(date '+%F %T')] cod: git pull --ff-only pe dev"
$SSH "$DEV_USER@$DEV_HOST" "cd ~/$DEV_PATH && git pull --ff-only origin main" 2>&1 | sed 's/^/  /'

echo "[refresh_dev $(date '+%F %T')] date: rsync cachedb/ prod -> dev"
# --exclude '*.tmp': cacheManager scrie atomic prin .tmp temporare care apar/dispar
# in timp real; nu are rost sa le copiem. Toleram codurile 23/24 (fisiere partiale/
# vanished) — benigne pe o sursa vie, NU un esec real de sync.
rc=0
rsync -a --info=stats1 --exclude '*.tmp' -e "$SSH" \
  "$SRC/cachedb/" "$DEV_USER@$DEV_HOST:$DEV_PATH/cachedb/" 2>&1 | sed 's/^/  /' || rc=$?
if [ "$rc" != 0 ] && [ "$rc" != 24 ] && [ "$rc" != 23 ]; then
  echo "[refresh_dev] rsync a esuat cu cod $rc"; exit "$rc"
fi

echo "[refresh_dev $(date '+%F %T')] gata"

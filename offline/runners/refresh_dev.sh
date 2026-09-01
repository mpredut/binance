#!/usr/bin/env bash
# refresh_dev.sh — aduce masina de BACKTEST (dev) la zi fata de prod, pe 2 canale:
#   1. CODE: `git pull --ff-only` on dev (code travels through GitHub, not this script).
#   2. DATA (cachedb, untracked by git): rsync prod -> dev. Only the price archives
#      (cache_price_*.jsonl) sunt strict necesare backtestului; sincronizam tot
#      cachedb ca dev sa fie o oglinda reala (rsync transfera doar delta, ieftin).
#
# Runs on PROD (it holds the SSH key to dev). It does NOT touch prod and does NOT git pull on
# prod (masina live cu bani reali — codul se trage acolo DELIBERAT, niciodata auto).
# Idempotent; sigur de pus pe cron pe prod.
set -euo pipefail

RUNNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$RUNNER_DIR/load_dev_backtest_env.sh"

# MUTEX (4 aug): refresh_dev poate fi pornit CONCURENT — o data din cron-ul propriu (*/30,
# so also at 00:00/12:00) AND once called by trigger_backtest_dev.sh (0 */12 = 00:00/12:00).
# Doua `git pull --ff-only` simultane pe dev -> FETCH_HEAD scris de amandoua -> 'Cannot
# fast-forward to multiple branches'. flock serializeaza: al doilea asteapta (max 300s) sa
# finish first, then runs (git pull becomes 'already up to date', cheap). No races.
exec 9>"/tmp/refresh_dev.lock"
if ! flock -w 300 9; then
  echo "[refresh_dev $(date '+%F %T')] another refresh_dev has run for >300s — skipping"
  exit 0
fi

REPO_ROOT="${BINANCE_REPO_ROOT:-$(cd "$RUNNER_DIR/../.." && pwd)}"
SSH="ssh -o BatchMode=yes -p $DEV_PORT"

echo "[refresh_dev $(date '+%F %T')] cod: git pull --ff-only pe dev"
$SSH "$DEV_USER@$DEV_HOST" "cd ~/$DEV_PATH && git pull --ff-only origin $DEV_CODE_BRANCH" 2>&1 | sed 's/^/  /'

echo "[refresh_dev $(date '+%F %T')] date: rsync cachedb/ prod -> dev"
# --exclude '*.tmp': cacheManager writes atomically through temporary .tmp files that come and go
# in timp real; nu are rost sa le copiem. Toleram codurile 23/24 (fisiere partiale/
# vanished) — benigne pe o sursa vie, NU un esec real de sync.
rc=0
rsync -a --info=stats1 --exclude '*.tmp' -e "$SSH" \
  "$REPO_ROOT/cachedb/" "$DEV_USER@$DEV_HOST:$DEV_PATH/cachedb/" 2>&1 | sed 's/^/  /' || rc=$?
if [ "$rc" != 0 ] && [ "$rc" != 24 ] && [ "$rc" != 23 ]; then
  echo "[refresh_dev] rsync a esuat cu cod $rc"; exit "$rc"
fi

echo "[refresh_dev $(date '+%F %T')] gata"

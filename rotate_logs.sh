#!/bin/bash
# rotate_logs.sh — roteste logurile de CONSOLA (nohup > *.log).
#
# Why: the logs made by logging.py rotate themselves; THESE (the redirect from
# nohup console output) are NOT rotated and grow without bound. We cap them here.
#
# How: we generate a logrotate config with paths derived from the script's location ($ROOT,
# so it is portable between server and local) and let logrotate do the work. copytruncate is
# MANDATORY: the bots hold the file open through a redirect, so we copy + truncate IN
# PLACE (no rename), otherwise the bot would keep writing to the renamed old file.
#
# A suggested cron (hourly, offset so it does not clash with the healthcheck at :*0/:*5):
# Schedule this script through the rendered production crontab.
ROOT="$(cd "$(dirname "$0")" && pwd)"
POLICY="$ROOT/logger_config.env"
[ -r "$POLICY" ] || { echo "rotate_logs: missing mandatory policy $POLICY"; exit 1; }
set -a
# shellcheck disable=SC1090
. "$POLICY"
set +a
: "${LOGROTATE_MAX_SIZE:?missing LOGROTATE_MAX_SIZE}"
: "${LOGROTATE_KEEP:?missing LOGROTATE_KEEP}"
LOGROTATE="$(command -v logrotate || echo /usr/sbin/logrotate)"
[ -x "$LOGROTATE" ] || { echo "rotate_logs: logrotate negasit"; exit 1; }

CONF="$(mktemp)"
trap 'rm -f "$CONF"' EXIT
# A GLOB per known directory (not a hardcoded list -> any new log is covered AUTOMATICALLY,
# without having to be added by hand, including one we forget or one written by
# a future script). Distinct directories => no overlaps (logrotate errors on a
# duplicate file). We do NOT use */*.log (it would catch myenv/venv). copytruncate: the processes
# hold the file open, we do not want them writing to the renamed old file.
#
# logger/*.log is included GENERALLY (not just a few known names) — 21 Jul: found a
# a file with a fixed name (tradeall_price_archiver.log, a nohup redirect) grew to
# 324MB in 4.5 hours, uncovered, because we used to exclude the whole of logger/ assuming
# that dated files manage themselves. size 20M plus copytruncate is SAFE for the
# dated files too (tradeall_2026-07-21.log and so on): if they pass the threshold on the
# same day, a chunk is compressed and the writer continues in the same file —
# it does not break the "a new file at midnight" convention at all. That way any new file
# ever appearing in logger/ (this script or another, today or in a year) is
# is covered AUTOMATICALLY, without having to be added to a list by hand.
{
    cat <<EOF
$ROOT/logs/*.log
$ROOT/kraken/*.log
$ROOT/hyperliquid/*.log
$ROOT/212trading/*.log
$ROOT/binance_api/*.log
$ROOT/logger/*.log
$ROOT/logger/*.jsonl
$ROOT/logger/execution_audit/*.jsonl
$ROOT/logs/*.jsonl
$ROOT/logs/shadow_live/*.jsonl
$ROOT/logs/hyperliquid_shadow/*.jsonl
$ROOT/*.log
{
    size $LOGROTATE_MAX_SIZE
    rotate $LOGROTATE_KEEP
    missingok
    notifempty
    compress
    copytruncate
}
EOF
} > "$CONF"

"$LOGROTATE" -s "$ROOT/.logrotate.state" "$CONF"

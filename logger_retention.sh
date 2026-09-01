#!/bin/bash
# Compress old date-stamped application logs and delete expired compressed logs.
#
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGGER_DIR="$ROOT/logger"
POLICY="$ROOT/logger_config.env"
[ -r "$POLICY" ] || { echo "logger_retention: missing mandatory policy $POLICY"; exit 1; }
set -a
# shellcheck disable=SC1090
. "$POLICY"
set +a
: "${LOG_COMPRESS_AFTER_DAYS:?missing LOG_COMPRESS_AFTER_DAYS}"
: "${LOG_DELETE_AFTER_DAYS:?missing LOG_DELETE_AFTER_DAYS}"

echo "=== logger_retention $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "  inainte: $(du -sh "$LOGGER_DIR" 2>/dev/null | cut -f1)"

find "$LOGGER_DIR" -maxdepth 1 -name "*.log" -mtime +"$LOG_COMPRESS_AFTER_DAYS" -print0 \
    | xargs -0 -r gzip -f

find "$LOGGER_DIR" -maxdepth 1 -name "*.log.gz" -mtime +"$LOG_DELETE_AFTER_DAYS" -print0 \
    | xargs -0 -r rm -f

echo "  dupa:    $(du -sh "$LOGGER_DIR" 2>/dev/null | cut -f1)"

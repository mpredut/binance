#!/bin/bash
# logger_retention.sh — cleans up OLD logger/*.log files (files with a DATE in the name,
# e.g. tradeall_2026-07-21.log). Those never delete themselves — each
# day gets a new file, but the old ones stay forever. Found 21 Jul:
# 10GB accumulated since 10 June with no cleanup at all -> a real risk of a full disk.
#
# It does NOT touch files with a FIXED NAME (tradeall_price_archiver.log and so on — those
# are kept under control by rotate_logs.sh, by size). An active file written on
# written continuously always has mtime "now", so it would never cross the
# COMPRESS_AFTER_DAYS below — the two scripts do not step on each other.
#
# It compresses (gzip) files older than COMPRESS_AFTER_DAYS days (safe —
# the writer moved to the next day's file long ago), and deletes
# COMPLETELY the archives older than DELETE_AFTER_DAYS.
#
# It does NOT touch logger/backtest/ (analysis results, reviewed by hand).
#
# Cron sugerat (zilnic, noaptea):
# Schedule this script through the rendered production crontab.
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGGER_DIR="$ROOT/logger"
COMPRESS_AFTER_DAYS=3
DELETE_AFTER_DAYS=45

echo "=== logger_retention $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "  before: $(du -sh "$LOGGER_DIR" 2>/dev/null | cut -f1)"

find "$LOGGER_DIR" -maxdepth 1 -name "*.log" -mtime +$COMPRESS_AFTER_DAYS -print0 \
    | xargs -0 -r gzip -f

find "$LOGGER_DIR" -maxdepth 1 -name "*.log.gz" -mtime +$DELETE_AFTER_DAYS -print0 \
    | xargs -0 -r rm -f

echo "  after:  $(du -sh "$LOGGER_DIR" 2>/dev/null | cut -f1)"

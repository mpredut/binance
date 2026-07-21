#!/bin/bash
# logger_retention.sh — curata logger/*.log VECHI (fisiere cu DATA in nume,
# ex. tradeall_2026-07-21.log). Astea nu se sterg niciodata singure — fiecare
# zi primeste fisier nou, dar cele vechi raman la nesfarsit. Gasit 21 iul:
# 10GB acumulate din 10 iunie, fara nicio curatare -> risc real de disc plin.
#
# NU atinge fisierele cu NUME FIX (tradeall_price_archiver.log etc. — alea
# le tine sub control rotate_logs.sh, prin marime). Un fisier activ scris in
# continuu are mtime mereu "acum", deci nu va trece niciodata pragul de
# COMPRESS_AFTER_DAYS de mai jos — cele doua scripturi nu se calca pe picioare.
#
# Comprima (gzip) fisierele mai vechi de COMPRESS_AFTER_DAYS zile (sigur —
# scriitorul a trecut deja la fisierul zilei urmatoare de mult), sterge
# COMPLET arhivele mai vechi de DELETE_AFTER_DAYS.
#
# NU atinge logger/backtest/ (rezultate de analiza, revizuiesc manual).
#
# Cron sugerat (zilnic, noaptea):
#   23 2 * * * /home/predut/binance/logger_retention.sh >> /home/predut/binance/logs/logger_retention.log 2>&1
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGGER_DIR="$ROOT/logger"
COMPRESS_AFTER_DAYS=3
DELETE_AFTER_DAYS=45

echo "=== logger_retention $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "  inainte: $(du -sh "$LOGGER_DIR" 2>/dev/null | cut -f1)"

find "$LOGGER_DIR" -maxdepth 1 -name "*.log" -mtime +$COMPRESS_AFTER_DAYS -print0 \
    | xargs -0 -r gzip -f

find "$LOGGER_DIR" -maxdepth 1 -name "*.log.gz" -mtime +$DELETE_AFTER_DAYS -print0 \
    | xargs -0 -r rm -f

echo "  dupa:    $(du -sh "$LOGGER_DIR" 2>/dev/null | cut -f1)"

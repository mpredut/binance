#!/bin/bash
# rotate_logs.sh — roteste logurile de CONSOLA (nohup > *.log).
#
# De ce: logurile facute de logging.py se rotesc singure; ASTEA (redirectul de
# nohup console output) are NOT rotated and grow without bound. We cap them here.
#
# Cum: generam un config logrotate cu cai derivate din locatia scriptului ($ROOT,
# so it is portable between server and local) and let logrotate do the work. copytruncate is
# MANDATORY: the bots hold the file open through a redirect, so we copy + truncate IN
# PLACE (no rename), otherwise the bot would keep writing to the renamed old file.
#
# Cron sugerat (orar, decalat sa nu se bata cu healthcheck-ul de la :*0/:*5):
#   17 * * * * /home/predut/binance/rotate_logs.sh >/dev/null 2>&1
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGROTATE="$(command -v logrotate || echo /usr/sbin/logrotate)"
[ -x "$LOGROTATE" ] || { echo "rotate_logs: logrotate negasit"; exit 1; }

CONF="$(mktemp)"
trap 'rm -f "$CONF"' EXIT
# GLOB per director cunoscut (nu lista hardcodata -> orice log nou e acoperit AUTOMAT,
# without having to be added by hand, including one we forget or one written by
# script viitor). Directoare distincte => fara suprapuneri (logrotate da eroare la
# fisier dublu). NU folosim */*.log (ar prinde myenv/venv). copytruncate: procesele
# hold the file open, we do not want them writing to the renamed old file.
#
# logger/*.log is included GENERALLY (not just a few known names) — 21 Jul: found a
# fisier cu nume fix (tradeall_price_archiver.log, redirect nohup) crescut la
# 324MB in 4.5 ore, neacoperit, pt ca inainte excludeam tot logger/ presupunand
# ca fisierele cu data se auto-gestioneaza. size 20M + copytruncate e SIGUR si pt
# fisierele cu data (tradeall_2026-07-21.log etc.): daca depasesc pragul in
# aceeasi zi, se comprima o bucata si scriitorul continua in acelasi fisier —
# nu strica deloc conventia "fisier nou la miezul noptii". Asa, orice fisier nou
# ever appearing in logger/ (this script or another, today or in a year) is
# acoperit AUTOMAT, fara sa mai trebuiasca adaugat manual la o lista.
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
    size 20M
    rotate 3
    missingok
    notifempty
    compress
    copytruncate
}
EOF
} > "$CONF"

"$LOGROTATE" -s "$ROOT/.logrotate.state" "$CONF"

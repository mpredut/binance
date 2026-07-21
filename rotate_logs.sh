#!/bin/bash
# rotate_logs.sh — roteste logurile de CONSOLA (nohup > *.log).
#
# De ce: logurile facute de logging.py se rotesc singure; ASTEA (redirectul de
# consola al lui nohup) NU se rotesc si cresc nelimitat. Le marginim aici.
#
# Cum: generam un config logrotate cu cai derivate din locatia scriptului ($ROOT,
# deci portabil server/local) si lasam logrotate sa faca treaba. copytruncate e
# OBLIGATORIU: botii tin fisierul deschis prin redirect, deci copiem + truncam IN
# LOC (nu rename), altfel botul ar scrie in fisierul vechi redenumit.
#
# Cron sugerat (orar, decalat sa nu se bata cu healthcheck-ul de la :*0/:*5):
#   17 * * * * /home/predut/binance/rotate_logs.sh >/dev/null 2>&1
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGROTATE="$(command -v logrotate || echo /usr/sbin/logrotate)"
[ -x "$LOGROTATE" ] || { echo "rotate_logs: logrotate negasit"; exit 1; }

CONF="$(mktemp)"
trap 'rm -f "$CONF"' EXIT
# GLOB per director cunoscut (nu lista hardcodata -> orice log nou e acoperit AUTOMAT,
# fara sa trebuiasca adaugat manual, inclusiv unul pe care-l uitam sau il scrie un
# script viitor). Directoare distincte => fara suprapuneri (logrotate da eroare la
# fisier dublu). NU folosim */*.log (ar prinde myenv/venv). copytruncate: procesele
# tin fisierul deschis, nu vrem sa scrie in fisierul vechi redenumit.
#
# logger/*.log e inclus GENERAL (nu doar cateva nume stiute) — 21 iul: gasit un
# fisier cu nume fix (tradeall_price_archiver.log, redirect nohup) crescut la
# 324MB in 4.5 ore, neacoperit, pt ca inainte excludeam tot logger/ presupunand
# ca fisierele cu data se auto-gestioneaza. size 20M + copytruncate e SIGUR si pt
# fisierele cu data (tradeall_2026-07-21.log etc.): daca depasesc pragul in
# aceeasi zi, se comprima o bucata si scriitorul continua in acelasi fisier —
# nu strica deloc conventia "fisier nou la miezul noptii". Asa, orice fisier nou
# aparut vreodata in logger/ (scriptul asta sau altul, azi sau peste un an) e
# acoperit AUTOMAT, fara sa mai trebuiasca adaugat manual la o lista.
{
    cat <<EOF
$ROOT/logs/*.log
$ROOT/kraken/*.log
$ROOT/hyperliquid/*.log
$ROOT/212trading/*.log
$ROOT/binance_api/*.log
$ROOT/logger/*.log
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

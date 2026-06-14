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
{
    for rel in hyperliquid/dn_bot.log hyperliquid/dn_watch.log \
               kraken/kraken_bot.log kraken/xstock_watch.log kraken/trail_k.log \
               "121trade/nvda.log" "121trade/spcx.log" healthcheck.log watchdog.log; do
        [ -f "$ROOT/$rel" ] && echo "$ROOT/$rel"
    done
    cat <<'EOF'
{
    size 20M
    rotate 3
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
}
EOF
} > "$CONF"

"$LOGROTATE" -s "$ROOT/.logrotate.state" "$CONF"

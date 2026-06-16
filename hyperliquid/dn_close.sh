#!/usr/bin/env bash
# dn_close.sh — IESIRE SIGURA din pozitia delta-neutral, intr-o singura comanda.
# Ordinea conteaza ca botul sa NU se bata cu inchiderea:
#   1. scoate watchdog-ul din cron (altfel reporneste botul imediat dupa ce-l oprim)
#   2. opreste botul de rebalansare (dn_bot.py, NU monitorul --watch)
#   3. inchide pozitia: vinde TOT spot + acopera TOT short  (dn_bot.py --close)
#
# Implicit REAL (foloseste STRAT_EXECUTE din config.env). Simulare:  ./dn_close.sh --paper
# Dupa, ca sa reactivezi DN-ul: porneste botul si ruleaza din nou ./dn_watchdog.sh --install
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${DN_PY:-$HERE/../myenv/bin/python}"
PAPER=""
[ "${1:-}" = "--paper" ] && PAPER="--paper"

echo "[dn_close] 1/3 scot watchdog-ul din cron (sa nu reporneasca botul)..."
"$HERE/dn_watchdog.sh" --uninstall || true

echo "[dn_close] 2/3 opresc botul de rebalansare..."
pid="$(pgrep -fa 'dn_bot\.py' | grep -v -- '--watch' | grep -v -e 'bash' -e 'dn_watchdog' -e 'dn_close' | awk '{print $1}' | head -n1)"
if [ -n "$pid" ]; then
  kill "$pid" 2>/dev/null; sleep 3
  if kill -0 "$pid" 2>/dev/null; then kill -9 "$pid" 2>/dev/null; sleep 2; fi
  echo "  oprit PID $pid"
else
  echo "  (rebalansarea nu rula)"
fi

echo "[dn_close] 3/3 inchid pozitia (${PAPER:-REAL})..."
cd "$HERE" || exit 1
"$PY" dn_bot.py --close $PAPER
rc=$?

echo "[dn_close] gata (rc=$rc). Verifica: $PY dn_bot.py --status"
echo "[dn_close] NB: watchdog-ul a fost SCOS din cron. Ca sa reactivezi DN-ul mai tarziu:"
echo "           porneste botul si ruleaza:  $HERE/dn_watchdog.sh --install"
exit "$rc"

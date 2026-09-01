#!/usr/bin/env bash
# dn_close.sh — a SAFE EXIT from the delta-neutral position, in a single command.
# The order matters so the bot does NOT fight the close:
#   1. remove the watchdog from cron (otherwise it restarts the bot right after we stop it)
#   2. stop the rebalancing bot (dn_bot.py, NOT the --watch monitor)
#   3. close the position: sell ALL the spot plus cover ALL the short  (dn_bot.py --close)
#
# REAL by default (uses STRAT_EXECUTE from config.env). Simulation:  ./dn_close.sh --paper
# Afterwards, to re-enable DN: start the bot and run ./dn_watchdog.sh --install again
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${DN_PY:-$HERE/../myenv/bin/python}"
PAPER=""
[ "${1:-}" = "--paper" ] && PAPER="--paper"

echo "[dn_close] 1/3 removing the watchdog from cron (so it does not restart the bot)..."
"$HERE/dn_watchdog.sh" --uninstall || true

echo "[dn_close] 2/3 opresc botul de rebalansare..."
pid="$(pgrep -fa 'dn_bot\.py' | grep -v -- '--watch' | grep -v -e 'bash' -e 'dn_watchdog' -e 'dn_close' | awk '{print $1}' | head -n1)"
if [ -n "$pid" ]; then
  kill "$pid" 2>/dev/null; sleep 3
  if kill -0 "$pid" 2>/dev/null; then kill -9 "$pid" 2>/dev/null; sleep 2; fi
  echo "  stopped PID $pid"
else
  echo "  (the rebalance was not running)"
fi

echo "[dn_close] 3/3 inchid pozitia (${PAPER:-REAL})..."
cd "$HERE" || exit 1
"$PY" dn_bot.py --close $PAPER
rc=$?

echo "[dn_close] done (rc=$rc). Check with: $PY dn_bot.py --status"
echo "[dn_close] NB: the watchdog was REMOVED from cron. To re-enable DN later:"
echo "           start the bot and run:  $HERE/dn_watchdog.sh --install"
exit "$rc"

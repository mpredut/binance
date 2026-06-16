#!/usr/bin/env bash
# dn_watchdog.sh — supervizor pentru botul de REBALANSARE delta-neutral (dn_bot.py).
# Reporneste botul daca a MURIT (proces absent) SAU a INGHETAT (proces viu dar
# dn_bot.log nu s-a mai scris de > DN_STALE_SEC). Heartbeat = mtime-ul dn_bot.log
# (botul logheaza la FIECARE tick / eroare, deci log inghetat = bot hung).
#
# De ce: spre deosebire de flota Binance (binance_start.sh + cron watchdog), dn_bot
# n-avea niciun supervizor — a stat hung ~39h fara sa-l reia nimeni. Vezi memoria
# proiectului "dn-bot-fara-watchdog".
#
# Instalare cron (5 min), pe SERVER:   ./dn_watchdog.sh --install
# NU atinge monitorul `dn_bot.py --watch` (read-only) si nu deschide pozitii — doar
# (re)porneste rebalansarea, care la pornire ADOPTA pozitia existenta (anti-dublare).
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${DN_PY:-$HERE/../myenv/bin/python}"   # python-ul cu SDK HL (myenv pe server)
LOG="$HERE/dn_bot.log"                      # heartbeat-ul rebalansarii
WLOG="$HERE/dn_watchdog.log"                # jurnalul watchdog-ului
STALE_SEC="${DN_STALE_SEC:-600}"            # >10 min fara scriere = hung (tick la 2 min, backoff max 5 min)
LOCKDIR="/tmp/dn_watchdog.lock"

log() { echo "$(date '+%F %T') $*" >> "$WLOG"; }

# --install: pune watchdog-ul in cron la 5 min (idempotent: scoate linia veche, o readauga)
if [ "${1:-}" = "--install" ]; then
  line="*/5 * * * * $HERE/dn_watchdog.sh >> $WLOG 2>&1"
  ( crontab -l 2>/dev/null | grep -v 'dn_watchdog.sh'; echo "$line" ) | crontab -
  echo "watchdog instalat in cron (5 min):"
  echo "  $line"
  exit 0
fi
# --uninstall: scoate watchdog-ul din cron
if [ "${1:-}" = "--uninstall" ]; then
  crontab -l 2>/dev/null | grep -v 'dn_watchdog.sh' | crontab - 2>/dev/null
  echo "watchdog scos din cron"
  exit 0
fi

# anti-suprapunere: un singur watchdog odata (restart-ul poate dura > intervalul cron)
if ! mkdir "$LOCKDIR" 2>/dev/null; then exit 0; fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

# PID-ul REBALANSARII = procesul python `dn_bot.py` FARA --watch. Excludem si liniile
# `bash`/`dn_watchdog` (lansatoare/watchdog-ul insusi care contin "dn_bot.py" in argv)
# ca sa nu confundam un wrapper cu botul real.
pid="$(pgrep -fa 'dn_bot\.py' | grep -v -- '--watch' | grep -v -e 'bash' -e 'dn_watchdog' | awk '{print $1}' | head -n1)"

need_start=0
if [ -z "$pid" ]; then
  log "rebalansare ABSENTA -> pornesc"
  need_start=1
else
  if [ -f "$LOG" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
  else
    age=999999
  fi
  if [ "$age" -ge "$STALE_SEC" ]; then
    log "rebalansare HUNG (PID $pid, log vechi de ${age}s >= ${STALE_SEC}s) -> kill+restart"
    kill "$pid" 2>/dev/null; sleep 3
    if kill -0 "$pid" 2>/dev/null; then kill -9 "$pid" 2>/dev/null; sleep 2; fi
    need_start=1
  fi
fi

if [ "$need_start" -eq 1 ]; then
  if [ ! -x "$PY" ]; then
    log "EROARE: python lipsa/neexecutabil la $PY — nu pot porni"
    exit 1
  fi
  cd "$HERE" || exit 1
  setsid nohup "$PY" dn_bot.py >> "$LOG" 2>&1 < /dev/null &
  log "pornit dn_bot.py (PY=$PY)"
fi

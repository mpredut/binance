#!/bin/bash
# deadman_switch.sh — an ntfy alert if the Linux server dies (crash/reboot/power-off),
# not just when a bot/process dies (healthcheck.sh --supervise already covers that).
#
# Cum functioneaza: la fiecare rulare (cron */15 min) impingem un mesaj ntfy PROGRAMAT
# (In: 35m) mai departe in timp, folosind acelasi sequence-id in URL
# (ntfy.sh/<topic>/server-alive). Fiecare update este totusi un request contabilizat
# in cota ntfy; cadenta veche */2 producea pana la 720 request-uri/zi si depasea singura
# limita gratuita. 96/zi lasa loc alertelor reale. Pattern-ul este documentat ca
# "dead man's switch": https://docs.ntfy.sh/publish/#scheduled-delivery
#
# If the server dies (or just cron does), nobody pushes the queued ntfy message
# further out and it delivers itself 35 minutes later — the alert arrives even if
# masina e complet oprita/fara curent.
ROOT="$(cd "$(dirname "$0")" && pwd)"
TOPIC=$(grep -hs '^NTFY_TOPIC_ERROR=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '" ')
[ -z "$TOPIC" ] && TOPIC=$(grep -hs '^NTFY_TOPIC=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '" ')
if [ -z "$TOPIC" ]; then
    echo "$(date '+%H:%M') deadman: no NTFY_TOPIC(_ERROR) found in $ROOT/.env"
    exit 1
fi

HOST=$(hostname)
# --retry 4 --retry-all-errors (8 aug): reincearca push-ul si la blip-uri DNS/retea tranzitorii
# (NameResolutionError), not only on 5xx. A typical blip (~30-40s) is ridden out in one run
# -> avoids a false alert when the server is alive but DNS resolution dropped briefly.
# Worst-case ~4x(10s+5s)=60s, mult sub cadenta de 15 min.
curl --fail-with-body -sS -m 10 --retry 4 --retry-delay 5 --retry-all-errors --retry-connrefused \
    -H "In: 35m" -H "Title: SERVER OPRIT ($HOST)" \
    -d "No heartbeat for 35 minutes — check the server (crash / reboot / power loss)." \
    "https://ntfy.sh/$TOPIC/server-alive" >/dev/null \
    && echo "$(date '+%H:%M') deadman: impins (+35m)" \
    || echo "$(date '+%H:%M') deadman: EROARE curl dupa reincercari (blip DNS/net prelungit?)"

#!/bin/bash
# deadman_switch.sh — alerta ntfy daca serverul Linux moare (crash/reboot/power-off),
# nu doar daca un bot/proces cade (asta il face deja healthcheck.sh --supervise).
#
# Cum functioneaza: la fiecare rulare (cron */15 min) impingem un mesaj ntfy PROGRAMAT
# (In: 35m) mai departe in timp, folosind acelasi sequence-id in URL
# (ntfy.sh/<topic>/server-alive). Fiecare update este totusi un request contabilizat
# in cota ntfy; cadenta veche */2 producea pana la 720 request-uri/zi si depasea singura
# limita gratuita. 96/zi lasa loc alertelor reale. Pattern-ul este documentat ca
# "dead man's switch": https://docs.ntfy.sh/publish/#scheduled-delivery
#
# Daca serverul moare (sau doar cronul), nimeni nu mai vine sa impinga mesajul din
# coada ntfy si acesta se livreaza singur peste 35 minute — alerta ajunge chiar daca
# masina e complet oprita/fara curent.
ROOT="$(cd "$(dirname "$0")" && pwd)"
TOPIC=$(grep -hs '^NTFY_TOPIC_ERROR=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '" ')
[ -z "$TOPIC" ] && TOPIC=$(grep -hs '^NTFY_TOPIC=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '" ')
if [ -z "$TOPIC" ]; then
    echo "$(date '+%H:%M') deadman: niciun NTFY_TOPIC(_ERROR) gasit in $ROOT/.env"
    exit 1
fi

HOST=$(hostname)
# --retry 4 --retry-all-errors (8 aug): reincearca push-ul si la blip-uri DNS/retea tranzitorii
# (NameResolutionError), nu doar la 5xx. Un blip tipic (~30-40s) e depasit intr-o singura rulare
# -> evita o alertă falsă când server-ul e viu, dar rezolvarea DNS a picat temporar.
# Worst-case ~4x(10s+5s)=60s, mult sub cadenta de 15 min.
curl --fail-with-body -sS -m 10 --retry 4 --retry-delay 5 --retry-all-errors --retry-connrefused \
    -H "In: 35m" -H "Title: SERVER OPRIT ($HOST)" \
    -d "Nu a mai trimis heartbeat de 35 minute — verifica serverul (crash / reboot / fara curent)." \
    "https://ntfy.sh/$TOPIC/server-alive" >/dev/null \
    && echo "$(date '+%H:%M') deadman: impins (+35m)" \
    || echo "$(date '+%H:%M') deadman: EROARE curl dupa reincercari (blip DNS/net prelungit?)"

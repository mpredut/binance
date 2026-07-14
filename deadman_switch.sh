#!/bin/bash
# deadman_switch.sh — alerta ntfy daca serverul Linux moare (crash/reboot/power-off),
# nu doar daca un bot/proces cade (asta il face deja healthcheck.sh --supervise).
#
# Cum functioneaza: la fiecare rulare (cron */2 min) impingem un mesaj ntfy PROGRAMAT
# (In: 6m) mai departe in timp, folosind acelasi sequence-id in URL
# (ntfy.sh/<topic>/server-alive) — asta INLOCUIESTE mesajul programat anterior in
# loc sa acumuleze unul nou la fiecare tick (spre deosebire de un `while true` naiv
# care ar crea cate un mesaj programat nou in fiecare minut). Documentat oficial ca
# "dead man's switch": https://docs.ntfy.sh/publish/#scheduled-delivery
#
# Daca serverul moare (sau doar cronul), nimeni nu mai vine sa impinga mesajul din
# coada ntfy si acesta se livreaza singur peste 6 minute — alerta ajunge chiar daca
# masina e complet oprita/fara curent.
ROOT="$(cd "$(dirname "$0")" && pwd)"
TOPIC=$(grep -hs '^NTFY_TOPIC_ERROR=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '" ')
[ -z "$TOPIC" ] && TOPIC=$(grep -hs '^NTFY_TOPIC=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '" ')
if [ -z "$TOPIC" ]; then
    echo "$(date '+%H:%M') deadman: niciun NTFY_TOPIC(_ERROR) gasit in $ROOT/.env"
    exit 1
fi

HOST=$(hostname)
curl -s -m 10 -H "In: 6m" -H "Title: SERVER OPRIT ($HOST)" \
    -d "Nu a mai trimis heartbeat de 6 minute — verifica serverul (crash / reboot / fara curent)." \
    "https://ntfy.sh/$TOPIC/server-alive" >/dev/null \
    && echo "$(date '+%H:%M') deadman: impins (+6m)" \
    || echo "$(date '+%H:%M') deadman: EROARE curl (fara net?)"

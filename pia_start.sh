#!/bin/bash

set -u

HEALTH_INTERVAL="${PIA_HEALTH_INTERVAL:-20}"
FAILURE_LIMIT="${PIA_FAILURE_LIMIT:-3}"
PROBE_TIMEOUT="${PIA_PROBE_TIMEOUT:-7}"
failures=0

vpn_healthy() {
    [ "$(piactl get connectionstate 2>/dev/null | tr -d '\r')" = "Connected" ] || return 1
    ip link show dev tun0 2>/dev/null | grep -q '<[^>]*UP[^>]*>' || return 1
    resolvectl query -i tun0 api.binance.com >/dev/null 2>&1 || return 1
    curl -4 --interface tun0 --connect-timeout 4 --max-time "$PROBE_TIMEOUT" \
        --fail --silent --show-error https://api.binance.com/api/v3/time \
        >/dev/null 2>&1 || return 1
}

# Configurare PIA

sleep 5

piactl dedicatedip add /home/predut/piatoken.txt
piactl set region dedicated-belgium-85.122.194.86
piactl set requestportforward true
piactl connect

echo "Astept asignarea IP..."
sleep 2
connected=0
for attempt in $(seq 1 12); do
    if piactl get pubip | grep -q '[0-9]'; then
        connected=1
        break
    fi
    sleep 5
    echo "Inca astept IP ($attempt/12)..."
done
if [ "$connected" -ne 1 ]; then
    echo "PIA nu a primit IP in 60s; systemd va reincerca."
    exit 1
fi

echo "VPN conectat cu IP:"
piactl get pubip

sleep 2
PORT=$(piactl get portforward)
echo "Port Forward: $PORT"

# piactl Connected singur nu este suficient: in timpul unui flap PIA poate
# raporta Connected desi tun0/DNS/HTTPS sunt deja nefunctionale. Proba este
# legata explicit de tun0, deci traficul nu cade pe conexiunea fizica.
# Trei esecuri consecutive evita restartul la un timeout izolat.
while true; do
    sleep "$HEALTH_INTERVAL"
    if vpn_healthy; then
        if [ "$failures" -gt 0 ]; then
            echo "PIA recuperat dupa $failures probe esuate"
        fi
        failures=0
        echo "PIA healthy (tun0 + DNS + HTTPS)"
        continue
    fi

    failures=$((failures + 1))
    state=$(piactl get connectionstate 2>/dev/null | tr -d '\r')
    echo "PIA nesanatos: proba $failures/$FAILURE_LIMIT (state=${state:-necunoscut})"
    if [ "$failures" -ge "$FAILURE_LIMIT" ]; then
        echo "PIA/DNS indisponibil persistent. Resetez tunelul; systemd reconecteaza."
        piactl disconnect >/dev/null 2>&1 || true
        exit 1
    fi
done

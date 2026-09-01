#!/bin/bash

set -u

HEALTH_INTERVAL="${PIA_HEALTH_INTERVAL:-20}"
FAILURE_LIMIT="${PIA_FAILURE_LIMIT:-3}"
PROBE_TIMEOUT="${PIA_PROBE_TIMEOUT:-7}"
CLI_TIMEOUT="${PIA_CLI_TIMEOUT:-6}"
failures=0

pia() {
    timeout "$CLI_TIMEOUT" piactl "$@"
}

vpn_healthy() {
    [ "$(pia get connectionstate 2>/dev/null | tr -d '\r')" = "Connected" ] || return 1
    ip link show dev tun0 2>/dev/null | grep -q '<[^>]*UP[^>]*>' || return 1
    resolvectl query -i tun0 api.binance.com >/dev/null 2>&1 || return 1
    curl -4 --interface tun0 --connect-timeout 4 --max-time "$PROBE_TIMEOUT" \
        --fail --silent --show-error https://api.binance.com/api/v3/time \
        >/dev/null 2>&1 || return 1
}

# Configurare PIA

DIP_TOKEN="${PIA_DIP_TOKEN:-/home/predut/piatoken.txt}"

sleep 5

# `piactl connect` is SILENTLY ignored when no graphical client is running, and none
# ever runs on the server, so background mode is mandatory (1 Sep 2026: without it
# the daemon accepts the "connectVPN" RPC and stays calmly Disconnected).
pia background enable || exit 1

# The region is no longer hardcoded: on every logout PIA deletes the dedicated IP
# registration, and a re-added token can return a DIFFERENT IP (1 Sep 2026: .86 -> .79).
# A hardcoded id then becomes "Unknown region", `set region` fails and the tunnel comes
# up on a pool IP -> Binance answers -2015. So we ask the daemon what the region is.
if ! pia get regions 2>/dev/null | grep -q "^dedicated-"; then
    pia dedicatedip add "$DIP_TOKEN" || exit 1
    sleep 3
fi
DEDICATED=$(pia get regions 2>/dev/null | tr -d '\r' | grep -m1 "^dedicated-")
if [ -z "$DEDICATED" ]; then
    echo "No dedicated IP registered (is token $DIP_TOKEN invalid?); systemd will retry."
    exit 1
fi

pia set protocol openvpn || exit 1
pia set region "$DEDICATED" || exit 1
pia set requestportforward true || exit 1
pia connect || exit 1

echo "Astept asignarea IP..."
sleep 2
connected=0
for attempt in $(seq 1 12); do
    if pia get vpnip | grep -q '[0-9]'; then
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

echo "VPN conectat cu IP dedicat:"
pia get vpnip

sleep 2
PORT=$(pia get portforward)
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
    state=$(pia get connectionstate 2>/dev/null | tr -d '\r')
    echo "PIA nesanatos: proba $failures/$FAILURE_LIMIT (state=${state:-necunoscut})"
    if [ "$failures" -ge "$FAILURE_LIMIT" ]; then
        echo "PIA/DNS indisponibil persistent. Resetez tunelul; systemd reconecteaza."
        pia disconnect >/dev/null 2>&1 || true
        exit 1
    fi
done

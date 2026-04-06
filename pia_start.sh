#!/bin/bash

# Configurare PIA

sleep 5

piactl dedicatedip add /home/predut/piatoken.txt
piactl set region dedicated-belgium-85.122.194.86
piactl set requestportforward true
piactl connect

# Aștept PIA
echo "Astept asignarea IP..."
sleep 2
until piactl get pubip | grep -q '[0-9]'; do
    sleep 5
    echo "Inca astept IP..."
done

echo "🔐 VPN conectat cu IP:"
piactl get pubip

sleep 2
PORT=$(piactl get portforward)
echo "Port Forward: $PORT"

# ======= MENȚINE SERVICE-UL ACTIV =========
while true; do
    sleep $((60*3)) # la 3 minute
    echo "Checking PIA connection ..."
    PORT=$(piactl get portforward)
    echo "Port Forward: $PORT"

    state=$(piactl get connectionstate)
    if [ "$state" != "Connected" ]; then
        echo "❌ VPN PICAT (state = $state). Ies → systemd va restarta."
        exit 1
    fi

    echo " PIA connected!"

done


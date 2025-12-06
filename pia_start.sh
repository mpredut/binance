#!/bin/bash

# Configurare PIA
piactl dedicatedip add /home/predut/binance/piatoken.txt
piactl set region dedicated-belgium-85.122.194.86
piactl set requestportforward true
piactl connect

# Așteaptă până VPN este conectat real și are IP
echo "Astept asignarea IP..."
until piactl get pubip | grep -q '[0-9]'; do
    sleep 5
    echo "Inca astept IP..."
done

echo "🔐 VPN conectat cu IP:"
piactl get pubip

piactl get portforward


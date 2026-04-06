#!/bin/bash

VPN_TIMEOUT=60    # max 60 sec să se conecteze
SLEEP_BETWEEN=10  # pauză scurtă între kill și restart

# ===== Verific și pornesc VPN dacă nu e conectat =====
echo "🔐 Verific conexiunea VPN..."
SECONDS_PASSED=0

sleep 5
while [ "$(piactl get connectionstate)" != "Connected" ]; do
    echo "⏳ VPN nu este conectat. Încerc să pornesc/reconectez pia.service..."
    #sudo systemctl restart pia.service
    piactl connect
    sleep 5
    SECONDS_PASSED=$((SECONDS_PASSED + 5))

    if [ "$SECONDS_PASSED" -ge "$VPN_TIMEOUT" ]; then
        echo "❌ VPN nu s-a conectat in $VPN_TIMEOUT sec!"
        exit 1
    fi
done

echo "✔ VPN activ (state = Connected)"
echo "IP Public: $(piactl get pubip)"
echo "Port Forward: $(piactl get portforward)"

# ===== Activare mediu virtual =====
echo "📦 Activez mediul Python..."
source /home/predut/binance/myenv/bin/activate

# ===== Lista scripturilor =====
scripts=(
    "cacheManager.py"
    "priceAnalysis.py"
    "tradeall.py"
    "monitortrades.py"
    "rtrade.py"
)

# ===== Omoară scripturile existente corect — FAILSAFE =====
for script in "${scripts[@]}"; do
    pids=$(pgrep -f "$script")
    if [ ! -z "$pids" ]; then
        echo "🔪 Oprire sigura pentru: $script"
        kill $pids
        sleep 1
        # dacă încă există → kill -9
        if pgrep -f "$script" > /dev/null; then
            echo "⚠ Procesul refuza, fortez kill -9"
            kill -9 $pids
        fi
    fi
done

sleep $SLEEP_BETWEEN

# ===== Pornire scripturi Python =====
echo "🚀 Pornesc scripturile Python..."
for script in "${scripts[@]}"; do
    log="/home/predut/binance/${script%.py}.log"
    nohup python "$script" > "$log" 2>&1 &
    echo "✔ Pornit $script → log: ${script%.py}.log"
done

echo "🎯 Toate scripturile ruleaza!"
wait

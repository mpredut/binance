#!/bin/bash
# Private Virtual Access
piactl dedicatedip add ../piatoken.txt
piactl set region dedicated-belgium-85.122.194.86
piactl set requestportforward true
piactl connect
piactl get portforward
piactl get pubip

# Script pentru restart automat al scripturilor Python

# Activează mediul virtual
source ~/myenv/bin/activate

# Lista de scripturi Python de gestionat
scripts=(
    "cacheManager.py"
    "priceAnalysis.py"
    "tradeall.py"
    "monitortrades.py"
    "rtrade.py"
)

# Omoară toate procesele care rulează aceste scripturi
for script in "${scripts[@]}"; do
    echo "🔪 Oprire procese pentru: $script"
    pids=$(pgrep -f "$script")
    if [ ! -z "$pids" ]; then
        echo "$pids" | xargs kill -9
        echo "✔ Oprit $script"
    else
        echo "⚠ Niciun proces pentru $script"
    fi
done

# Pornește fiecare script cu nohup și log separat
echo "🚀 Repornire procese..."
for script in "${scripts[@]}"; do
    log="${script%.py}.log"
    nohup python "$script" > "$log" 2>&1 &
    echo "✔ Pornit $script (log: $log)"
done

echo "✅ Toate scripturile au fost repornite!"


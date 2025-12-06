#!/bin/bash

# Activare mediu virtual
source /home/predut/binance/myenv/bin/activate

scripts=(
    "cacheManager.py"
    "priceAnalysis.py"
    "tradeall.py"
    "monitortrades.py"
    "rtrade.py"
)

# Omoară scripturile existente
for script in "${scripts[@]}"; do
    pkill -f "$script"
done

# Porneste scripturile
for script in "${scripts[@]}"; do
    log="${script%.py}.log"
    nohup python "$script" > "$log" 2>&1 &
    echo "Pornit $script (log: $log)"
done

wait


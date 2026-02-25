#!/bin/bash
source /home/predut/binance/myenv/bin/activate

# Usage:
#   ./start.sh          - porneste normal, fara watchdog
#   ./start.sh --watch  - porneste cu watchdog (restart automat la crash)

WATCHDOG=false
if [[ "$1" == "--watch" ]]; then
    WATCHDOG=true
    echo "🔁 Mod watchdog activat - restart automat la crash"
else
    echo "▶️  Mod normal - fara watchdog"
fi

scripts=(
    "cacheManager.py"
    "priceAnalysis.py"
    "tradeall.py"
    "monitortrades.py"
    "rtrade.py"
)

SCRIPT_DIR="/home/predut/binance"

# Omoară scripturile existente
for script in "${scripts[@]}"; do
    pkill -f "$script"
done
sleep 1

# Functie care porneste un script (si il reporneste la crash daca watchdog e activ)
start_script() {
    local script=$1
    local log="${SCRIPT_DIR}/${script%.py}.log"

    if $WATCHDOG; then
        # Loop infinit - reporneste la crash
        while true; do
            echo "[$(date '+%H:%M:%S')] 🚀 Pornesc $script" >> "$log"
            python "$SCRIPT_DIR/$script" >> "$log" 2>&1
            EXIT_CODE=$?
            echo "[$(date '+%H:%M:%S')] ⚠️  $script a ieșit cu cod $EXIT_CODE. Repornesc în 5s..." >> "$log"
            echo "⚠️  $script a crashat (cod $EXIT_CODE). Repornesc în 5s..."
            sleep 5
        done
    else
        nohup python "$SCRIPT_DIR/$script" > "$log" 2>&1 &
    fi
}

# Porneste scripturile
declare -A pids
for script in "${scripts[@]}"; do
    log="${script%.py}.log"
    if $WATCHDOG; then
        start_script "$script" &
        pids[$script]=$!
    else
        nohup python "$SCRIPT_DIR/$script" > "$log" 2>&1 &
        pids[$script]=$!
    fi
    echo "Pornit $script PID=${pids[$script]}"
done

# Verificare dupa 3 secunde
sleep 3
echo ""
echo "=== Verificare status ==="
all_ok=true
for script in "${scripts[@]}"; do
    pid=${pids[$script]}
    if kill -0 "$pid" 2>/dev/null; then
        echo "✅ $script rulează (PID=$pid)"
    else
        echo "❌ $script a crashat! Ultimele erori:"
        tail -5 "${script%.py}.log"
        all_ok=false
    fi
done

if $all_ok; then
    echo ""
    echo "✅ Toate scripturile rulează OK."
else
    echo ""
    echo "❌ Unele scripturi au crashat - verifică log-urile!"
fi

# In mod watchdog trebuie sa asteptam (watchdog ruleaza in background)
if $WATCHDOG; then
    echo ""
    echo "Watchdog activ. Pentru a opri tot: pkill -f start.sh && pkill -f python"
fi
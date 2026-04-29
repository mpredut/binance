#!/bin/bash

VPN_TIMEOUT=60
SLEEP_BETWEEN=10
PYTHON_START_WAIT=5   # secunde să așteptăm după pornire înainte să verificăm

# ===== Verific și pornesc VPN =====
echo "🔐 Verific conexiunea VPN..."
SECONDS_PASSED=0
sleep 5
while [ "$(piactl get connectionstate)" != "Connected" ]; do
    echo "⏳ VPN nu este conectat. Încerc reconectare..."
    piactl connect
    sleep 5
    SECONDS_PASSED=$((SECONDS_PASSED + 5))
    if [ "$SECONDS_PASSED" -ge "$VPN_TIMEOUT" ]; then
        echo "❌ VPN nu s-a conectat in $VPN_TIMEOUT sec!"
        exit 1
    fi
done
echo "✔ VPN activ"
echo "IP Public: $(piactl get pubip)"
echo "Port Forward: $(piactl get portforward)"

# ===== Activare mediu virtual =====
echo "📦 Activez mediul Python..."
VENV_PATH="/home/predut/binance/myenv/bin/activate"
if [ ! -f "$VENV_PATH" ]; then
    echo "❌ Mediul virtual nu există la $VENV_PATH. Abort!"
    exit 1
fi
source "$VENV_PATH"

# Verifică că python e cel din venv
PYTHON_BIN=$(which python)
if [[ "$PYTHON_BIN" != *"myenv"* ]]; then
    echo "❌ Python activ nu e din venv: $PYTHON_BIN. Abort!"
    exit 1
fi
echo "✔ Python activ: $PYTHON_BIN"

# ===== Verific că scripturile există =====
SCRIPT_DIR="/home/predut/binance"
scripts=(
    "cacheManager.py"
    "assetguardian.py"
    "priceAnalysis.py"
    "tradeall.py"
    "monitortrades.py"
    "rtrade.py"
)

echo "🔍 Verific existența scripturilor..."
for script in "${scripts[@]}"; do
    if [ ! -f "$SCRIPT_DIR/$script" ]; then
        echo "❌ Script lipsă: $SCRIPT_DIR/$script. Abort!"
        exit 1
    fi
done
echo "✔ Toate scripturile există."

# ===== Omoară procesele existente =====
for script in "${scripts[@]}"; do
    pids=$(pgrep -f "$script")
    if [ -n "$pids" ]; then
        echo "🔪 Oprire: $script (pids: $pids)"
        kill $pids
        sleep 1
        if pgrep -f "$script" > /dev/null; then
            echo "⚠ Forțez kill -9 pentru $script"
            kill -9 $pids
        fi
    fi
done

sleep $SLEEP_BETWEEN

declare -a PIDS
declare -a LOGS
FAILED=()

echo "🚀 Pornesc scripturile Python..."
# Pornim scripturile
for script in "${scripts[@]}"; do
    log="$SCRIPT_DIR/${script%.py}.log"
    LOGS+=("$log")
    cd "$SCRIPT_DIR" || exit 1
    nohup python "$script" > "$log" 2>&1 &
    PID=$!
    PIDS+=("$PID")
done

sleep "$PYTHON_START_WAIT"

# Verificăm fiecare proces
for i in "${!scripts[@]}"; do
    script="${scripts[$i]}"
    PID="${PIDS[$i]}"
    log="${LOGS[$i]}"

    if kill -0 "$PID" 2>/dev/null; then
        echo "✔ Pornit $script (PID=$PID) → $log"
    else
        echo "❌ $script a crăpat la pornire! Vezi log-ul:"
        tail -20 "$log"
        FAILED+=("$script")
    fi
done


# ===== Raport final =====
if [ ${#FAILED[@]} -eq 0 ]; then
    echo "🎯 Toate scripturile rulează!"
else
    echo "⚠ Scripturi eșuate: ${FAILED[*]}"
    exit 1
fi

wait

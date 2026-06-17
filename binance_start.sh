#!/bin/bash

# ===== SINGLE-INSTANCE (flock) — împiedică două instanțe să ruleze simultan =====
# Fără asta, o a doua instanță (ex. systemd + lansare manuală) intra în „război de
# supervizare": fiecare reînvie procesele pe care le omoară cealaltă → DUPLICARE.
# A doua instanță nu obține lock-ul → iese imediat.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"   # radacina = locul scriptului (portabil, fara /home/predut hardcodat)
mkdir -p "$SCRIPT_DIR/logs"   # loguri de consola in folder dedicat (nu mai in root)
LOCK_PATH="$SCRIPT_DIR/binance_start.lock"
exec 9>"$LOCK_PATH" || exit 1
if ! flock -n 9; then
    echo "❌ binance_start.sh rulează deja (lock activ: $LOCK_PATH)."
    echo "   Pentru restart: 'systemctl restart binance' sau oprește instanța existentă."
    exit 1
fi
# lock-ul (fd 9) e ținut cât trăiește scriptul; se eliberează automat la ieșire.

VPN_RETRY_TIMEOUT=60
SLEEP_AFTER_VPN_CONNECT=3
SLEEP_AFTER_KILL=5
PYTHON_START_WAIT=5   # secunde să așteptăm după pornire înainte să verificăm

# ===== Verific și pornesc VPN =====
echo "🔐 Verific conexiunea VPN..."
SECONDS_PASSED=0
sleep 5
while [ "$(piactl get connectionstate)" != "Connected" ]; do
    echo "⏳ VPN nu este conectat. Încerc reconectare..."
    piactl connect
    sleep SLEEP_AFTER_VPN_CONNECT
    SECONDS_PASSED=$((SECONDS_PASSED + SLEEP_AFTER_VPN_CONNECT))
    if [ "$SECONDS_PASSED" -ge "$VPN_RETRY_TIMEOUT" ]; then
        echo "❌ VPN nu s-a conectat in $VPN_RETRY_TIMEOUT sec!"
        exit 1
    fi
done
echo "✔ VPN activ"
echo "IP Public: $(piactl get pubip)"
echo "Port Forward: $(piactl get portforward)"

# ===== Activare mediu virtual =====
echo "📦 Activez mediul Python..."
VENV_PATH="$SCRIPT_DIR/myenv/bin/activate"
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

# ===== Verific că scripturile există =====  (SCRIPT_DIR e definit la inceput)
scripts=(
    "cacheManager.py"
    "assetguardian.py"
    "priceAnalysis.py"
    "tradeall.py"
    "monitortrades.py"
    "rtrade.py"
    "market_alerts.py"
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

sleep $SLEEP_AFTER_KILL

declare -a PIDS
declare -a LOGS
FAILED=()

echo "🚀 Pornesc scripturile Python..."
# Pornim scripturile
for script in "${scripts[@]}"; do
    log="$SCRIPT_DIR/logs/${script%.py}.log"
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

# Afișăm toate procesele Python care rulează
echo
echo "Procese Python active:"
ps aux | grep '[p]ython'

# ===== Watchdog (cron la 5 min) — instalat/refresh idempotent =====
# Rulează DOAR pe această mașină (cea care pornește monitorul). Căile sunt derivate
# din mediul curent (SCRIPT_DIR + python-ul din venv activat), deci e corect oriunde.
WATCHDOG_MARKER="price_monitor_watchdog.py"
WATCHDOG_PY="$(command -v python)"
WATCHDOG_LINE="*/5 * * * * cd $SCRIPT_DIR && $WATCHDOG_PY $SCRIPT_DIR/$WATCHDOG_MARKER >> $SCRIPT_DIR/logs/watchdog.log 2>&1"

install_watchdog() {
    ( crontab -l 2>/dev/null | grep -v "$WATCHDOG_MARKER"; echo "$WATCHDOG_LINE" ) | crontab -
    echo "✔ Watchdog activ (cron la 5 min) → $SCRIPT_DIR/watchdog.log"
}
remove_watchdog() {
    crontab -l 2>/dev/null | grep -v "$WATCHDOG_MARKER" | crontab - 2>/dev/null
    echo "✔ Watchdog dezactivat"
}
install_watchdog

# La Ctrl+C / SIGTERM: oprim procesele ȘI scoatem watchdog-ul, ca o oprire INTENȚIONATĂ
# să nu declanșeze alarma „monitorul s-a oprit". Repornirea îl reinstalează.
cleanup() {
    echo
    echo "🛑 Oprire..."
    remove_watchdog
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null; done
    exit 0
}
trap cleanup INT TERM

echo "All good. Supervizez procesele (repornesc orice cade). <ctrl c> = stop."

# ===== Buclă de SUPERVIZARE =====
# În loc de `wait` (care se întoarce doar dacă mor TOATE procesele), verificăm
# periodic fiecare PID și repornim individual orice proces mort. Așa, dacă pică
# UN singur script (ex. market_alerts), e repornit în max SUPERVISE_INTERVAL,
# nu rămâne mort până cad toate. systemd rămâne plasa de siguranță pt „a căzut tot".
SUPERVISE_INTERVAL=30
while true; do
    for i in "${!scripts[@]}"; do
        if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
            script="${scripts[$i]}"
            log="${LOGS[$i]}"
            echo "♻ $(date '+%H:%M:%S') $script a murit (PID ${PIDS[$i]}) → repornesc"
            cd "$SCRIPT_DIR" || exit 1
            nohup python "$script" > "$log" 2>&1 &
            PIDS[$i]=$!
            echo "   → nou PID ${PIDS[$i]} → $log"
        fi
    done
    sleep "$SUPERVISE_INTERVAL"
done

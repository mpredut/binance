#!/bin/bash

# ===== SINGLE INSTANCE (flock) — stops two instances from running at once =====
# Without it, a second instance (e.g. systemd plus a manual launch) starts a
# "supervision war": each revives the processes the other kills -> DUPLICATION.
# The second instance does not get the lock -> it exits immediately.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"   # radacina = locul scriptului (portabil, fara /home/predut hardcodat)
mkdir -p "$SCRIPT_DIR/logs"   # loguri de consola in folder dedicat (nu mai in root)
LOCK_PATH="$SCRIPT_DIR/flota_start.lock"
exec 9>"$LOCK_PATH" || exit 1
if ! flock -n 9; then
    echo "❌ flota_start.sh is already running (lock held: $LOCK_PATH)."
    echo "   To restart: 'systemctl restart binance' or stop the existing instance."
    exit 1
fi
# The lock (fd 9) is held for the life of the script and released automatically on exit.

VPN_RETRY_TIMEOUT=60
PIA_CLI_TIMEOUT="${PIA_CLI_TIMEOUT:-6}"
SLEEP_AFTER_VPN_CONNECT=3
SLEEP_AFTER_KILL=5
PYTHON_START_WAIT=5   # Seconds to wait after starting before checking.

# ===== Check and start the VPN =====
echo "🔐 Verific conexiunea VPN..."
SECONDS_PASSED=0
sleep 5
pia() { timeout "$PIA_CLI_TIMEOUT" piactl "$@"; }
while [ "$(pia get connectionstate 2>/dev/null | tr -d '\r')" != "Connected" ]; do
    echo "⏳ VPN is not connected. Trying to reconnect..."
    pia connect >/dev/null 2>&1 || true
    sleep $SLEEP_AFTER_VPN_CONNECT
    SECONDS_PASSED=$((SECONDS_PASSED + SLEEP_AFTER_VPN_CONNECT))
    if [ "$SECONDS_PASSED" -ge "$VPN_RETRY_TIMEOUT" ]; then
        echo "❌ VPN nu s-a conectat in $VPN_RETRY_TIMEOUT sec!"
        exit 1
    fi
done
echo "✔ VPN activ"
# `pubip` is the PHYSICAL link's IP (the ISP line) and stays on it even when the
# tunnel is perfectly healthy — shown here it looked like the fleet was leaving
# unprotected. What matters for the Binance whitelist is the tunnel exit IP: `vpnip`.
echo "VPN exit IP: $(pia get vpnip 2>/dev/null)  (ISP link IP: $(pia get pubip 2>/dev/null))"
echo "Port Forward: $(pia get portforward 2>/dev/null)"

# ===== Activare mediu virtual =====
echo "📦 Activez mediul Python..."
VENV_DIR=""
for _d in ".venv" "myenv"; do
    [ -f "$SCRIPT_DIR/$_d/bin/activate" ] && VENV_DIR="$_d" && break
done
VENV_PATH="$SCRIPT_DIR/$VENV_DIR/bin/activate"
if [ -z "$VENV_DIR" ] || [ ! -f "$VENV_PATH" ]; then
    echo "❌ No venv found (.venv / myenv) in $SCRIPT_DIR. Abort!"
    exit 1
fi
source "$VENV_PATH"

# Check that python is the one from the venv.
PYTHON_BIN=$(which python)
if [[ "$PYTHON_BIN" != *"$VENV_DIR"* ]]; then
    echo "❌ The active python is not from the venv: $PYTHON_BIN. Abort!"
    exit 1
fi
echo "✔ Python activ: $PYTHON_BIN"

# ===== Lista flotei din manifestul UNIC procs.conf (role=fleet) =====
# Sursa unica de adevar (acelasi fisier citit de bots_start.sh + healthcheck.sh).
# To add/remove a fleet process, edit procs.conf, not this file.
MANIFEST="$SCRIPT_DIR/procs.conf"
scripts=()
if [ -f "$MANIFEST" ]; then
    while IFS='|' read -r _pat _dir _cmd _label _hb _stale _role; do
        [ -z "$_pat" ] && continue
        case "$_pat" in \#*) continue;; esac
        [ "$_role" = fleet ] && scripts+=("$_pat")
    done < "$MANIFEST"
fi
if [ "${#scripts[@]}" -eq 0 ]; then
    echo "❌ Nicio intrare role=fleet in $MANIFEST. Abort!"
    exit 1
fi

echo "🔍 Checking that the scripts exist..."
for script in "${scripts[@]}"; do
    if [ ! -f "$SCRIPT_DIR/$script" ]; then
        echo "❌ Missing script: $SCRIPT_DIR/$script. Abort!"
        exit 1
    fi
done
echo "✔ All scripts are present."

# ===== Kill the existing processes =====
for script in "${scripts[@]}"; do
    pids=$(pgrep -f "$script")
    if [ -n "$pids" ]; then
        echo "🔪 Oprire: $script (pids: $pids)"
        kill $pids
        sleep 1
        if pgrep -f "$script" > /dev/null; then
            echo "⚠ Forcing kill -9 on $script"
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
    nohup python "$script" >> "$log" 2>&1 9>&- &
    PID=$!
    PIDS+=("$PID")
done

sleep "$PYTHON_START_WAIT"

# Check each process.
for i in "${!scripts[@]}"; do
    script="${scripts[$i]}"
    PID="${PIDS[$i]}"
    log="${LOGS[$i]}"

    if kill -0 "$PID" 2>/dev/null; then
        echo "✔ Pornit $script (PID=$PID) → $log"
    else
        echo "❌ $script crashed on startup! See the log:"
        tail -20 "$log"
        FAILED+=("$script")
    fi
done


# ===== Raport final =====
if [ ${#FAILED[@]} -eq 0 ]; then
    echo "🎯 All scripts are running!"
else
    echo "⚠ Failed scripts: ${FAILED[*]}"
    exit 1
fi

# Show every running Python process.
echo
echo "Procese Python active:"
ps aux | grep '[p]ython'

# ===== Watchdog (cron la 5 min) — instalat/refresh idempotent =====
# Runs ONLY on this machine (the one starting the monitor). The paths are derived
# from the current environment (SCRIPT_DIR + the activated venv python), so it is portable.
WATCHDOG_PY="$(command -v python)"
# Doua watchdog-uri: prospetime cache + anomalii (rata erori din loguri).
_WD_CACHE="*/2 * * * * cd $SCRIPT_DIR && $WATCHDOG_PY $SCRIPT_DIR/verify_tools/watchdogfor_cacheandconfig.py >> $SCRIPT_DIR/logs/watchdog.log 2>&1"
_WD_ANOM="*/5 * * * * cd $SCRIPT_DIR && $WATCHDOG_PY $SCRIPT_DIR/verify_tools/watchdogfor_anomaly.py >> $SCRIPT_DIR/logs/anomaly_watchdog.log 2>&1"
# Markers to clean from crontab (including OLD names, so nothing is orphaned after a rename).
_WD_STRIP='cache_watchdog\.py|log_anomaly_watchdog\.py|watchdogfor_cache\.py|watchdogfor_cacheandconfig\.py|watchdogfor_anomaly\.py|price_monitor_watchdog\.py'

install_watchdog() {
    ( crontab -l 2>/dev/null | grep -vE "$_WD_STRIP"; echo "$_WD_CACHE"; echo "$_WD_ANOM" ) | crontab -
    echo "✔ Watchdog-uri active (cache + anomalii, cron la 5 min)"
}
remove_watchdog() {
    crontab -l 2>/dev/null | grep -vE "$_WD_STRIP" | crontab - 2>/dev/null
    echo "✔ Watchdog-uri dezactivate"
}
install_watchdog

# On Ctrl+C / SIGTERM: stop the processes AND remove the watchdog, so an INTENTIONAL
# shutdown does not trigger the "monitor stopped" alarm. Restarting reinstalls it.
cleanup() {
    echo
    echo "🛑 Oprire..."
    remove_watchdog
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null; done
    exit 0
}
trap cleanup INT TERM

echo "All good. Supervizez procesele (repornesc orice cade). <ctrl c> = stop."

# ===== SUPERVISION loop =====
# Instead of `wait` (which returns only when ALL processes die), we check each
# PID periodically and restart any dead process individually. That way, if a
# SINGLE script dies (e.g. market_alerts) it is restarted within SUPERVISE_INTERVAL,
# not left dead until everything falls. systemd stays the safety net for "everything died".
SUPERVISE_INTERVAL=30
while true; do
    for i in "${!scripts[@]}"; do
        pid="${PIDS[$i]}"
        state=$(ps -o state= -p "$pid" 2>/dev/null | tr -d ' ')
        # SIGSTOP/Ctrl-Z lasa PID-ul existent, iar kill -0 il considera sanatos.
        # Incercam SIGCONT o data; daca ramane oprit, il inlocuim controlat.
        if [[ "$state" == T* ]]; then
            echo "♻ $(date '+%H:%M:%S') ${scripts[$i]} STOPPED (PID $pid) → SIGCONT"
            kill -CONT "$pid" 2>/dev/null || true
            sleep 2
            state=$(ps -o state= -p "$pid" 2>/dev/null | tr -d ' ')
        fi
        if ! kill -0 "$pid" 2>/dev/null || [[ "$state" == T* ]] || [[ "$state" == Z* ]]; then
            script="${scripts[$i]}"
            log="${LOGS[$i]}"
            echo "♻ $(date '+%H:%M:%S') $script nesanatos (PID $pid, state=${state:-absent}) → repornesc"
            kill "$pid" 2>/dev/null || true
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
            cd "$SCRIPT_DIR" || exit 1
            nohup python "$script" >> "$log" 2>&1 9>&- &
            PIDS[$i]=$!
            echo "   → nou PID ${PIDS[$i]} → $log"
        fi
    done
    sleep "$SUPERVISE_INTERVAL"
done

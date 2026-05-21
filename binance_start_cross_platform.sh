#!/bin/bash

# ===== Configurare =====
VPN_RETRY_TIMEOUT=60
SLEEP_AFTER_VPN_CONNECT=3
SLEEP_AFTER_KILL=5
PYTHON_START_WAIT=5

# ===== Detectare piactl (inclusiv symlink-uri Windows/WSL) =====
find_piactl() {
    local locations=(
        "/usr/local/bin/piactl"
        "/usr/bin/piactl"
        "/bin/piactl"
        "$HOME/.local/bin/piactl"
        "/mnt/c/Program Files/Private Internet Access/piactl.exe"
        "/mnt/c/Program Files (x86)/Private Internet Access/piactl.exe"
    )
    
    for loc in "${locations[@]}"; do
        if [ -f "$loc" ] || [ -L "$loc" ]; then
            echo "$loc"
            return 0
        fi
    done
    
    if command -v piactl &> /dev/null; then
        which piactl
        return 0
    fi
    
    return 1
}

PIA_CTL=$(find_piactl)
if [ -n "$PIA_CTL" ]; then
    echo "🔐 Găsit piactl: $PIA_CTL"
    
    if [ ! -x "$PIA_CTL" ]; then
        echo "   Adaug permisiuni de execuție..."
        chmod +x "$PIA_CTL" 2>/dev/null || sudo chmod +x "$PIA_CTL" 2>/dev/null
    fi
else
    echo "⚠ Nu s-a găsit piactl. Se sare peste verificarea VPN."
    PIA_CTL=""
fi

# ===== Detectare automată =====
CURRENT_USER=$(whoami)

if [ $# -eq 0 ]; then
    echo "❗ Folosire: $0 <cale_catre_scripturi>"
    echo "   Exemplu: $0 /home/$(whoami)/binance"
    echo "   Sau: $0 . (pentru directorul curent)"
    exit 1
fi

SCRIPT_DIR="$1"
if [ ! -d "$SCRIPT_DIR" ]; then
    echo "❌ Director invalid: $SCRIPT_DIR"
    exit 1
fi

echo "📁 Director scripturi: $SCRIPT_DIR"
echo "👤 User curent: $CURRENT_USER"
echo "🪟 Sistem: $(uname -a | grep -qi microsoft && echo 'WSL' || echo 'Native Linux')"

# ===== Verific și pornesc VPN (dacă avem piactl) =====
if [ -n "$PIA_CTL" ]; then
    echo "🔐 Verific conexiunea VPN..."
    
    # Funcție pentru a curăța output-ul (remove \r, whitespace)
    clean_output() {
        echo "$1" | tr -d '\r' | xargs
    }
    
    run_piactl() {
        if [[ "$PIA_CTL" == *.exe ]]; then
            "$PIA_CTL" "$@" 2>/dev/null
        else
            "$PIA_CTL" "$@"
        fi
    }
    
    SECONDS_PASSED=0
    sleep 5
    
    # Obține starea și curăță output-ul
    RAW_STATE=$(run_piactl get connectionstate 2>/dev/null)
    CONNECTION_STATE=$(clean_output "$RAW_STATE")
    
    echo "   Stare VPN detectată: '${CONNECTION_STATE}'"
    
    while [ "$CONNECTION_STATE" != "Connected" ]; do
        echo "⏳ VPN nu este conectat (stare: '${CONNECTION_STATE}'). Încerc reconectare..."
        run_piactl connect
        
        sleep $SLEEP_AFTER_VPN_CONNECT
        SECONDS_PASSED=$((SECONDS_PASSED + SLEEP_AFTER_VPN_CONNECT))
        
        if [ "$SECONDS_PASSED" -ge "$VPN_RETRY_TIMEOUT" ]; then
            echo "❌ VPN nu s-a conectat in $VPN_RETRY_TIMEOUT sec!"
            exit 1
        fi
        
        RAW_STATE=$(run_piactl get connectionstate 2>/dev/null)
        CONNECTION_STATE=$(clean_output "$RAW_STATE")
        echo "   Stare curentă: '${CONNECTION_STATE}'"
    done
    
    echo "✔ VPN activ"
    
    # Obține IP și port (curățat)
    PUB_IP=$(run_piactl get pubip 2>/dev/null | tr -d '\r' | xargs)
    PORT_FW=$(run_piactl get portforward 2>/dev/null | tr -d '\r' | xargs)
    
    [ -n "$PUB_IP" ] && echo "IP Public: $PUB_IP" || echo "IP Public: (nu se poate obține)"
    [ -n "$PORT_FW" ] && echo "Port Forward: $PORT_FW" || echo "Port Forward: (nu se poate obține)"
else
    echo "⚠ Verificare VPN omisă (piactl negăsit)"
fi

# ===== Activare mediu virtual =====
echo "📦 Caut mediu virtual Python..."

VENV_ACTIVATE=""
USE_UV=""

if [ -n "$VIRTUAL_ENV" ] && [ -f "$VIRTUAL_ENV/bin/activate" ]; then
    VENV_ACTIVATE="$VIRTUAL_ENV/bin/activate"
    echo "✔ Găsit mediu virtual din VIRTUAL_ENV: $VENV_ACTIVATE"
fi

if [ -z "$VENV_ACTIVATE" ]; then
    for venv_dir in "$SCRIPT_DIR/.venv" "$SCRIPT_DIR/venv" "$SCRIPT_DIR/env"; do
        if [ -f "$venv_dir/bin/activate" ]; then
            VENV_ACTIVATE="$venv_dir/bin/activate"
            echo "✔ Găsit mediu virtual: $VENV_ACTIVATE"
            break
        fi
    done
fi

if [ -z "$VENV_ACTIVATE" ]; then
    PARENT_DIR=$(dirname "$SCRIPT_DIR")
    for venv_dir in "$PARENT_DIR/.venv" "$PARENT_DIR/venv" "$PARENT_DIR/env"; do
        if [ -f "$venv_dir/bin/activate" ]; then
            VENV_ACTIVATE="$venv_dir/bin/activate"
            echo "✔ Găsit mediu virtual în director părinte: $VENV_ACTIVATE"
            break
        fi
    done
fi

if [ -z "$VENV_ACTIVATE" ] && command -v uv &> /dev/null && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
    echo "🔧 Proiect detectat cu uv"
    USE_UV="true"
fi

if [ -n "$VENV_ACTIVATE" ]; then
    source "$VENV_ACTIVATE"
    PYTHON_BIN=$(which python 2>/dev/null || which python3 2>/dev/null)
    echo "✔ Python activ: $PYTHON_BIN"
elif [ "$USE_UV" != "true" ]; then
    echo "⚠ Nu s-a găsit mediu virtual! Se folosește Python-ul de sistem."
    PYTHON_BIN=$(which python3 2>/dev/null || which python 2>/dev/null)
fi

# ===== Lista scripturilor =====
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
        echo "❌ Script lipsă: $SCRIPT_DIR/$script"
        exit 1
    fi
done
echo "✔ Toate scripturile există."

# ===== Omoară procesele existente =====
echo "🔪 Oprire procese vechi..."
for script in "${scripts[@]}"; do
    pids=$(pgrep -f "python.*${script}" 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "   Oprire: $script (pids: $pids)"
        kill $pids 2>/dev/null
        sleep 1
        pids=$(pgrep -f "python.*${script}" 2>/dev/null)
        if [ -n "$pids" ]; then
            echo "   ⚠ Forțez kill -9 pentru $script"
            kill -9 $pids 2>/dev/null
        fi
    fi
done

sleep $SLEEP_AFTER_KILL

# ===== Pornire scripturi =====
declare -a PIDS
declare -a LOGS
FAILED=()

echo "🚀 Pornesc scripturile Python..."

for script in "${scripts[@]}"; do
    log="$SCRIPT_DIR/${script%.py}.log"
    LOGS+=("$log")
    cd "$SCRIPT_DIR" || exit 1
    
    if [ "$USE_UV" = "true" ]; then
        nohup uv run python "$script" > "$log" 2>&1 &
    else
        nohup python "$script" > "$log" 2>&1 &
    fi
    
    PID=$!
    PIDS+=("$PID")
    echo "   Pornit $script (PID=$PID)"
done

sleep "$PYTHON_START_WAIT"

for i in "${!scripts[@]}"; do
    script="${scripts[$i]}"
    PID="${PIDS[$i]}"
    log="${LOGS[$i]}"
    
    if kill -0 "$PID" 2>/dev/null; then
        echo "✔ Rulează: $script (PID=$PID)"
    else
        echo "❌ $script a crăpat la pornire!"
        if [ -f "$log" ]; then
            echo "   Ultimele linii din log:"
            tail -5 "$log" 2>/dev/null | sed 's/^/     /'
        fi
        FAILED+=("$script")
    fi
done

echo
echo "========================================="
if [ ${#FAILED[@]} -eq 0 ]; then
    echo "✅ Toate scripturile rulează!"
    echo
    echo "📊 Procese active:"
    for i in "${!scripts[@]}"; do
        echo "   ${scripts[$i]} (PID: ${PIDS[$i]})"
    done
else
    echo "⚠ Scripturi eșuate: ${FAILED[*]}"
    exit 1
fi
echo "========================================="
echo "🟢 Sistem pornit. Apasă Ctrl+C pentru a ieși"
echo "💡 Pentru a opri toate procesele: kill ${PIDS[*]}"

wait
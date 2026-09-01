#!/bin/bash
# healthcheck.sh — supraveghere + raport consolidat pentru boti/flota (HL/Kraken/T212).
#
# SURSA UNICA DE ADEVAR: procs.conf (citit si de bots_start.sh + flota_start.sh).
# Aici NU mai exista liste de procese hardcodate. Detectie DUBLA: absenta (pgrep) SI
# hang (proces viu dar log inghetat, heartbeat pe mtime) — inlocuieste dn_watchdog.sh.
#   --supervise  (cron */5): reporneste botii (role=bot) morti/inghetati cu backoff; flota = doar alerta.
#   --alert      : doar alerta daca lipseste/e hung ceva (nu reporneste).
#   --check      : preview READ-ONLY (ce ar face --supervise) — sigur, nu atinge nimic.
#   (fara arg)   : raport complet (procese + conturi HL/Kraken/T212).
ROOT="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$ROOT/procs.conf"
# python cu SDK Hyperliquid (eth_account): prefera venv, cade pe python3
VENV=""
for _d in ".venv" "myenv"; do [ -f "$ROOT/$_d/bin/activate" ] && VENV="$_d" && break; done
HLPY="$ROOT/$VENV/bin/python"
{ [ -x "$HLPY" ] && "$HLPY" -c "import eth_account" 2>/dev/null; } || HLPY=python3
now=$(date +%s)
PIA_CLI_TIMEOUT="${PIA_CLI_TIMEOUT:-6}"
VPN_PROBE_TIMEOUT="${PIA_PROBE_TIMEOUT:-8}"

pia() { timeout "$PIA_CLI_TIMEOUT" piactl "$@" 2>/dev/null | tr -d '\r'; }

# Return a concise reason instead of only Connected/DOWN. Every external probe is
# bounded, and HTTPS is explicitly bound to tun0 so it cannot escape via the ISP.
vpn_state() {
    [ "$(pia get connectionstate)" = "Connected" ] || { echo piactl; return; }
    ip link show dev tun0 2>/dev/null | grep -q '<[^>]*UP[^>]*>' \
        || { echo tun0; return; }
    resolvectl query -i tun0 api.binance.com >/dev/null 2>&1 \
        || { echo dns; return; }
    curl -4 --interface tun0 --connect-timeout 4 --max-time "$VPN_PROBE_TIMEOUT" \
        --fail --silent --show-error https://api.binance.com/api/v3/time \
        >/dev/null 2>&1 || { echo https; return; }
    echo ok
}

push_ntfy() {
    local title="$1" body="$2"
    [ -n "${TOPIC:-}" ] || return 1
    curl --fail-with-body -sS -m 10 --retry 2 --retry-all-errors \
        -H "Title: $title" -d "$body" "https://ntfy.sh/$TOPIC" >/dev/null
}

# Starea unei linii: ok | absent | stopped | zombie | hung.
proc_state() {
    local pat="$1" dir="$2" hblog="$3" hbstale="$4"
    local pids states
    pids=$(pgrep -f "$pat") || { echo absent; return; }
    states=$(ps -o state= -p "$(echo "$pids" | paste -sd, -)" 2>/dev/null | tr -d ' ')
    echo "$states" | grep -q T && { echo stopped; return; }
    echo "$states" | grep -q Z && { echo zombie; return; }
    if [ -n "$hblog" ] && [ -n "$hbstale" ]; then
        local lp="$hblog"; case "$hblog" in /*) ;; *) lp="$dir/$hblog";; esac
        if [ -f "$lp" ]; then
            local age=$(( now - $(stat -c %Y "$lp") ))
            [ "$age" -ge "$hbstale" ] && { echo hung; return; }
        fi
    fi
    echo ok
}

# ===== MOD --check: preview READ-ONLY (nu atinge nimic) ====================
if [ "$1" = "--check" ]; then
    echo "=== CHECK (read-only) $(date '+%H:%M:%S') — sursa: $MANIFEST ==="
    vpn=$(vpn_state)
    [ "$vpn" = ok ] && echo "  VPN              ok (piactl + tun0 + DNS + Binance HTTPS)" \
        || echo "  VPN              FAULT ($vpn)"
    while IFS='|' read -r pat dir cmd label hblog hbstale role; do
        [ -z "$pat" ] && continue
        case "$pat" in \#*) continue;; esac
        dir=$(eval echo "$dir")
        st=$(proc_state "$pat" "$dir" "$hblog" "$hbstale")
        extra=""
        if [ -n "$hblog" ]; then
            lp="$hblog"; case "$hblog" in /*) ;; *) lp="$dir/$hblog";; esac
            [ -f "$lp" ] && extra="(heartbeat ${hblog}: $(( now - $(stat -c %Y "$lp") ))s/${hbstale}s)"
        fi
        act="-"
        [ "$st" != ok ] && { [ "$role" = bot ] && act="REPORNIRE" || act="alerta"; }
        printf '  %-16s %-6s %-7s %-10s %s\n' "$label" "$role" "$st" "$act" "$extra"
    done < "$MANIFEST"
    exit 0
fi

# ===== MOD --alert: DOAR alerta (ntfy) daca lipseste/e hung ceva ===========
if [ "$1" = "--alert" ]; then
    missing=""
    vpn=$(vpn_state)
    [ "$vpn" != ok ] && missing="$missing VPN($vpn)"
    while IFS='|' read -r pat dir cmd label hblog hbstale role; do
        [ -z "$pat" ] && continue
        case "$pat" in \#*) continue;; esac
        dir=$(eval echo "$dir")
        st=$(proc_state "$pat" "$dir" "$hblog" "$hbstale")
        [ "$st" != ok ] && missing="$missing $label($st)"
    done < "$MANIFEST"
    if [ -n "$missing" ]; then
        TOPIC=$(grep -hs NTFY_TOPIC "$ROOT/kraken/.env" "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '" ')
        push_ntfy "Procese pe server" \
            "Moarte/hung:$missing  -> verifica (./bots_start.sh / flota_start)" \
            || echo "$(date '+%H:%M') ALERTA NELIVRATA: eroare HTTP/retea ntfy"
        echo "$(date '+%H:%M') ALERTA: $missing"
    else
        echo "$(date '+%H:%M') OK (toate proceselele ruleaza)"
    fi
    exit 0
fi

# ===== MOD --supervise (cron */5): reporneste boti morti/inghetati + alerta flota =====
if [ "$1" = "--supervise" ]; then
    # Garda statie locala: --supervise PORNESTE boti cu cheile reale. Din checkout-ul
    # de dezvoltare (WSL, /home/mariusp) refuzam. Serverul (/home/predut) ramane activ.
    case "$ROOT" in
        /home/mariusp/*) echo "$(date '+%H:%M') supervise dezactivat pe statia locala ($ROOT)"; exit 0;;
    esac
    exec 8>/tmp/binance_supervise.lock
    flock -n 8 || { echo "$(date '+%H:%M') supervise deja ruleaza — sar (anti-dublare)"; exit 0; }
    SUP=/tmp/binance_sup; mkdir -p "$SUP"; WINDOW=1800; MAX=3
    TOPIC=$(grep -hs NTFY_TOPIC "$ROOT/kraken/.env" "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '" ')
    push(){ push_ntfy "$1" "$2"; }
    alert_miss=""
    vpn=$(vpn_state)
    [ "$vpn" != ok ] && alert_miss=" VPN($vpn)"
    while IFS='|' read -r pat dir cmd label hblog hbstale role; do
        [ -z "$pat" ] && continue
        case "$pat" in \#*) continue;; esac
        dir=$(eval echo "$dir")
        st=$(proc_state "$pat" "$dir" "$hblog" "$hbstale")
        if [ "$st" = ok ]; then
            [ "$role" = bot ] && rm -f "$SUP/$label" "$SUP/$label.esc"   # sanatos -> reset backoff
            continue
        fi
        if [ "$role" != bot ]; then          # flota: doar alerta (o tine flota_start)
            alert_miss="$alert_miss $label($st)"
            continue
        fi
        # role=bot, stare absent|hung
        if [ "$st" = hung ]; then
            echo "$(date '+%H:%M') $label HUNG (heartbeat vechi) -> kill"
            pkill -f "$pat" 2>/dev/null; sleep 2; pkill -9 -f "$pat" 2>/dev/null
        fi
        cnt=0; ws=$now
        [ -f "$SUP/$label" ] && read -r cnt ws < "$SUP/$label"
        [ $((now - ws)) -gt $WINDOW ] && { cnt=0; ws=$now; }   # fereastra noua
        if [ "$cnt" -ge "$MAX" ]; then
            [ -f "$SUP/$label.esc" ] || { push "Bot in CRASH-LOOP" "$label ($st) de ${cnt}x in 30min — NU mai repornesc, interventie manuala"; touch "$SUP/$label.esc"; }
            echo "$(date '+%H:%M') $label CRASH-LOOP (nu repornesc)"; continue
        fi
        # 8>&- : botul pornit NU mosteneste fd 8 (lock-ul supervise) -> fara scurgere de lock
        # (altfel viitoarele --supervise gasesc lock-ul tinut de bot si sar "deja ruleaza" la infinit).
        ( cd "$dir" && eval "$cmd" ) 8>&-                     # restart curat ($ROOT/$VENV expandate aici)
        cnt=$((cnt + 1)); echo "$cnt $ws" > "$SUP/$label"; rm -f "$SUP/$label.esc"
        push "Bot repornit" "$label ($st) -> REPORNIT (incercarea $cnt/$MAX)"
        echo "$(date '+%H:%M') $label REPORNIT ($st, incercarea $cnt)"
    done < "$MANIFEST"
    [ -n "$alert_miss" ] && { push "Procese de verificat" "Moarte/hung (nu le repornesc de aici):$alert_miss"; echo "$(date '+%H:%M') alerta flota:$alert_miss"; }
    [ -z "$alert_miss" ] && echo "$(date '+%H:%M') supervise: flota OK"
    exit 0
fi

echo "============ HEALTHCHECK $(date '+%Y-%m-%d %H:%M') ============"
echo "=== PROCESE (etime = de cat ruleaza) ==="
ps -eo etime,args | grep -E "dn_bot|kraken_bot|kraken_xstock_watch|t212_bot|ipo.py|trailing_stop|cacheManager|priceAnalysis|tradeall|rtrade|monitortrades|market_alerts|run_price_monitor|assetguardian" | grep -v grep

echo "=== HYPERLIQUID DN ==="
( cd "$ROOT/hyperliquid" && "$HLPY" dn_bot.py --status 2>&1 | grep -E "SPOT|PERP|DELTA|FUNDING|LICHIDARE|COLATERAL" )

echo "=== KRAKEN ==="
( cd "$ROOT/kraken" && python3 - <<'PY' 2>/dev/null
import sys, os; sys.path.insert(0, ".")
from common import load_dotenv
load_dotenv(".env"); load_dotenv("config.env")
from kraken_client import KrakenClient
try:
    from kraken_xstock_watch import yahoo_last
except Exception:
    yahoo_last = lambda s: None
c = KrakenClient(os.environ.get("KRAKEN_API_KEY"), os.environ.get("KRAKEN_API_SECRET"))
b = c.balance()
print("  cash ZUSD %.0f + USDC %.0f | HYPE %s @ %s" % (
    float(b.get("ZUSD", 0)), float(b.get("USDC", 0)), b.get("HYPE"), c.last_price("HYPEUSD")))
oo = c.open_orders()
print("  ordine: %d %s" % (len(oo), [o.get("descr", {}).get("order") for o in oo.values()]))
sp = float(b.get("SPCXx.T", 0))
if sp:
    px = yahoo_last("SPCX") or 0
    print("  SPCXx.T %.4f @ %.2f -> $%.0f" % (sp, px, sp * px))
PY
)

echo "=== T212 ==="
( cd "$ROOT/212trading" && python3 - <<'PY' 2>/dev/null
import sys, os, time; sys.path.insert(0, ".")
from ipo_common import load_dotenv
load_dotenv(".env")
from t212_client import T212Client
c = T212Client(os.environ["T212_API_KEY"], os.environ.get("T212_API_SECRET"), env="live")
pf = None
for _ in range(3):
    pf = c.get_portfolio()
    if pf:
        break
    time.sleep(2)
for p in (pf or []):
    if any(s in p.get("ticker", "") for s in ("NVDA", "SPCX")):
        print("  %s qty %s avg %s pret %s P&L %s" % (
            p.get("ticker"), p.get("quantity"), p.get("averagePrice"),
            p.get("currentPrice"), p.get("ppl")))
if not pf:
    print("  portofoliu indisponibil")
PY
)
echo "============ END ============"

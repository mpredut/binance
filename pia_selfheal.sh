#!/bin/bash
# pia_selfheal.sh — reparare automata a VPN-ului PIA + alerta care ajunge chiar
# daca defectul e "nu mai am internet".
#
# DE CE EXISTA: incident 1 sep 2026 — pia-daemon a crapat pe "Too many open files"
# si a ramas proces-fantoma; orice `piactl` returna "Timed out after 5 sec". Fara
# tun0, killswitch-ul PIA a taiat TOT traficul de iesire. Consecinte in lant:
#   - pia.service a intrat in bucla de restart (a ajuns la 6975 reporniri);
#   - binance.service are Requires=pia.service, deci flota_start.sh se bloca la
#     gardul "Verific conexiunea VPN" si NU pornea niciunul dintre cei 7 membri,
#     desi `systemctl is-active binance.service` raporta senin "active";
#   - NICIO alerta n-a ajuns la telefon: ntfy.sh se atinge tot prin internet, iar
#     internetul era exact ce lipsea (278 x "EROARE curl" in logs/deadman.log).
# A stat asa ~34 de zile fara ca cineva sa afle.
#
# PRINCIPIUL: intai repara, apoi raporteaza. Alertele se pun intr-un SPOOL pe disc
# si se golesc cand conectivitatea revine, deci povestea completa a caderii ajunge
# la telefon chiar daca in timpul ei nu putea iesi niciun pachet.
#
# Rulare (cron root, la 5 min):
#   */5 * * * * /home/predut/binance/pia_selfheal.sh >> /home/predut/binance/logs/pia_selfheal.log 2>&1
# Manual:
#   ./pia_selfheal.sh --check   # doar diagnostic, nu atinge nimic
#   ./pia_selfheal.sh --force   # forteaza scara de reparare chiar daca pare sanatos

set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="/var/lib/pia_selfheal"
SPOOL="$STATE_DIR/alert_spool"          # alerte nelivrate (internetul era jos)
OUTAGE_MARK="$STATE_DIR/outage_since"   # timestampul inceputului caderii
REINSTALL_MARK="$STATE_DIR/last_reinstall"
LOCK="/tmp/pia_selfheal.lock"

PIA_USER="${PIA_USER:-predut}"
PROBE_TIMEOUT="${PIA_PROBE_TIMEOUT:-8}"
CLI_TIMEOUT="${PIA_CLI_TIMEOUT:-6}"     # peste asta = daemon agatat
CONNECT_WAIT="${PIA_CONNECT_WAIT:-60}"  # cat asteptam un tunel dupa fiecare treapta
DIP_TOKEN="${PIA_DIP_TOKEN:-/home/$PIA_USER/piatoken.txt}"
FALLBACK_REGION="${PIA_FALLBACK_REGION:-auto}"
REINSTALL_COOLDOWN="${PIA_REINSTALL_COOLDOWN:-86400}"  # max o reinstalare/24h
# Endpoint-ul "latest" al PIA intoarce HTML, nu installer, iar pia-linux-latest.run
# da 403 — deci URL-ul trebuie versionat explicit. Bump-ul e o singura linie in .env.
PIA_VERSION="${PIA_VERSION:-3.7.2-08420}"
INSTALLER_URL="${PIA_INSTALLER_URL:-https://installers.privateinternetaccess.com/download/pia-linux-${PIA_VERSION}.run}"

MODE="${1:-}"
CHECK_ONLY=0; FORCE=0
case "$MODE" in
    --check) CHECK_ONLY=1 ;;
    --force) FORCE=1 ;;
    "") ;;
    *) echo "Folosire: $0 [--check|--force]" >&2; exit 2 ;;
esac

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# piactl trebuie chemat ca utilizatorul proprietar al sesiunii PIA, nu ca root.
# `timeout` este obligatoriu: cand daemonul e agatat, piactl blocheaza la nesfarsit.
pia() {
    if [ "$(id -un)" = "$PIA_USER" ]; then
        timeout "$CLI_TIMEOUT" piactl "$@" 2>/dev/null | tr -d '\r'
    else
        timeout "$CLI_TIMEOUT" runuser -u "$PIA_USER" -- piactl "$@" 2>/dev/null | tr -d '\r'
    fi
}

# ===== PROBE ==============================================================
# Internet brut, fara DNS si fara tunel: separa "VPN picat" de "netul e jos".
net_raw_ok() { curl -s -m "$PROBE_TIMEOUT" -o /dev/null https://1.1.1.1 2>/dev/null; }

# Daemonul raspunde? (starea patologica din incident: raspunde procesul, nu socketul)
daemon_responsive() { [ -n "$(pia get connectionstate)" ]; }

# Sanatate reala: nu ne multumim cu "Connected" — in timpul unui flap PIA raporteaza
# Connected desi tun0/DNS/HTTPS sunt deja moarte. Proba e legata explicit de tun0.
vpn_healthy() {
    [ "$(pia get connectionstate)" = "Connected" ] || return 1
    ip link show dev tun0 2>/dev/null | grep -q '<[^>]*UP[^>]*>' || return 1
    curl -4 --interface tun0 --connect-timeout 4 --max-time "$PROBE_TIMEOUT" \
        --fail --silent https://api.binance.com/api/v3/time >/dev/null 2>&1 || return 1
}

wait_healthy() {
    local waited=0
    while [ "$waited" -lt "$CONNECT_WAIT" ]; do
        sleep 5; waited=$((waited + 5))
        vpn_healthy && return 0
    done
    return 1
}

# ===== ALERTE =============================================================
# Nu incercam sa livram cu orice pret: daca netul e jos, punem in spool si mergem
# mai departe cu repararea. Spool-ul se goleste singur cand conectivitatea revine.
ntfy_topic() {
    local t
    t=$(grep -hs '^NTFY_TOPIC_ERROR=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '" ')
    [ -z "$t" ] && t=$(grep -hs '^NTFY_TOPIC=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '" ')
    echo "$t"
}

ntfy_push() {  # $1=titlu $2=corp -> 0 daca a plecat
    local topic; topic=$(ntfy_topic)
    [ -z "$topic" ] && return 1
    curl -s -m 15 --retry 2 --retry-delay 3 --retry-all-errors \
        -H "Title: $1" -d "$2" "https://ntfy.sh/$topic" >/dev/null 2>&1
}

alert() {  # $1=titlu $2=corp
    if ntfy_push "$1" "$2"; then
        log "alerta trimisa: $1"
    else
        printf '%s\t%s\t%s\n' "$(date '+%Y-%m-%d %H:%M')" "$1" "$2" >> "$SPOOL"
        log "alerta pusa in spool (fara conectivitate): $1"
    fi
}

flush_spool() {
    [ -s "$SPOOL" ] || return 0
    net_raw_ok || return 1
    local n body
    n=$(wc -l < "$SPOOL")
    body=$(cat "$SPOOL")
    if ntfy_push "PIA: $n alerte intarziate ($(hostname))" "$body"; then
        rm -f "$SPOOL"
        log "spool golit ($n alerte livrate retroactiv)"
    fi
}

# ===== TREPTELE DE REPARARE ==============================================
rung_connect() {
    log "treapta 1: piactl connect"
    pia background enable >/dev/null
    pia connect >/dev/null
}

rung_reconnect() {
    log "treapta 2: disconnect + connect (reset de sesiune)"
    pia disconnect >/dev/null
    sleep 4
    pia connect >/dev/null
}

# Treapta 3 = fix-ul care a rezolvat incidentul din 1 sep.
rung_restart_daemon() {
    log "treapta 3: restart piavpn.service (daemon agatat / stare corupta)"
    systemctl stop pia.service    >/dev/null 2>&1
    systemctl stop piavpn.service >/dev/null 2>&1
    sleep 3
    # Daemonul agatat nu moare la SIGTERM — ramane cu copii <defunct>.
    if pgrep -x pia-daemon >/dev/null; then
        log "   pia-daemon nu a murit la stop -> kill -9"
        pkill -9 -x pia-daemon
        sleep 2
    fi
    systemctl start piavpn.service >/dev/null 2>&1
    sleep 8
    # Fara asta `piactl connect` e ignorat IN TACERE cand nu ruleaza GUI-ul.
    pia background enable >/dev/null

    # Logout-ul/resetarea sterge inregistrarea IP-ului dedicat din daemon; regiunea
    # ramane setata pe una inexistenta -> "Unknown region" si conectare esuata.
    if ! pia get regions | grep -q '^dedicated-'; then
        # Verdictul il dam pe starea REALA de dupa (apare regiunea dedicata?), nu pe
        # textul intors de piactl: la succes nu tipareste nimic, deci un test pe
        # output ar raporta fals esec.
        [ -f "$DIP_TOKEN" ] && pia dedicatedip add "$DIP_TOKEN" >/dev/null
        if pia get regions | grep -q '^dedicated-'; then
            log "   IP dedicat re-adaugat din $DIP_TOKEN"
        else
            log "   ATENTIE: token IP dedicat lipsa/invalid -> cad pe regiunea $FALLBACK_REGION"
            alert "PIA fara IP dedicat ($(hostname))" \
"Tokenul $DIP_TOKEN e invalid sau lipseste, asa ca tunelul merge pe '$FALLBACK_REGION'.
Serverul iese pe un IP din pool, NU pe cel dedicat -> cheile Binance whitelist-uite
vor da -2015. Genereaza token nou din contul PIA (sectiunea Dedicated IP)."
        fi
    fi

    local dedicated
    dedicated=$(pia get regions | grep -m1 '^dedicated-')
    pia set protocol openvpn >/dev/null
    pia set region "${dedicated:-$FALLBACK_REGION}" >/dev/null
    pia set requestportforward true >/dev/null
    pia connect >/dev/null
}

# Treapta 4 = reinstalare. Best-effort si explicit ultima: installerul PIA refuza sa
# ruleze ca root si poate cere escaladare interactiva, deci poate esua legitim aici.
rung_reinstall() {
    local last=0
    [ -f "$REINSTALL_MARK" ] && last=$(cat "$REINSTALL_MARK" 2>/dev/null || echo 0)
    local age=$(( $(date +%s) - last ))
    if [ "$age" -lt "$REINSTALL_COOLDOWN" ]; then
        log "treapta 4: SARITA (reinstalare acum $((age/3600))h, cooldown $((REINSTALL_COOLDOWN/3600))h)"
        return 1
    fi
    if ! net_raw_ok; then
        log "treapta 4: SARITA (fara internet nu se poate descarca installerul)"
        return 1
    fi

    log "treapta 4: reinstalez PIA de la $INSTALLER_URL"
    local tmp
    tmp=$(mktemp -d /tmp/pia_reinstall.XXXXXX)
    if ! curl -fsSL -m 600 -o "$tmp/pia.run" "$INSTALLER_URL"; then
        log "   descarcare esuata"
        rm -rf "$tmp"
        return 1
    fi

    # Nu executam orice ne-a dat reteaua: un 403/pagina de eroare ar fi HTML de cativa KB.
    local size
    size=$(stat -c %s "$tmp/pia.run")
    if [ "$size" -lt 20000000 ] || ! head -c 100 "$tmp/pia.run" | grep -q '^#!/'; then
        log "   fisier descarcat suspect (size=$size, nu pare installer) -> NU il rulez"
        alert "PIA: installer invalid ($(hostname))" \
            "Descarcarea de la $INSTALLER_URL a dat $size octeti si nu arata a script .run. Reinstaleaza manual."
        rm -rf "$tmp"
        return 1
    fi

    date +%s > "$REINSTALL_MARK"
    chmod +x "$tmp/pia.run"
    chown -R "$PIA_USER" "$tmp"
    systemctl stop pia.service >/dev/null 2>&1
    if runuser -u "$PIA_USER" -- "$tmp/pia.run" >/tmp/pia_reinstall.out 2>&1; then
        log "   reinstalare reusita (versiune: $(pia -v))"
    else
        log "   reinstalare ESUATA (vezi /tmp/pia_reinstall.out) — probabil cere interactiv"
        alert "PIA: reinstalarea automata a esuat ($(hostname))" \
            "$(tail -5 /tmp/pia_reinstall.out 2>/dev/null). Reinstaleaza manual versiunea $PIA_VERSION."
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
    sleep 10
    pia background enable >/dev/null
    pia connect >/dev/null
}

# ===== EXECUTIE ===========================================================
mkdir -p "$STATE_DIR" 2>/dev/null

if [ "$CHECK_ONLY" = 1 ]; then
    echo "=== pia_selfheal --check (read-only) ==="
    echo "  versiune PIA   : $(pia -v)"
    echo "  daemon raspunde: $(daemon_responsive && echo DA || echo 'NU (agatat)')"
    echo "  stare          : $(pia get connectionstate)"
    echo "  regiune        : $(pia get region)"
    echo "  vpnip          : $(pia get vpnip)"
    echo "  tun0           : $(ip -brief addr show tun0 2>&1 | head -1)"
    echo "  IP dedicat     : $(pia get regions | grep -m1 '^dedicated-' || echo 'NEINREGISTRAT')"
    echo "  internet brut  : $(net_raw_ok && echo OK || echo PICAT)"
    echo "  vpn_healthy    : $(vpn_healthy && echo DA || echo NU)"
    echo "  alerte in spool: $([ -f "$SPOOL" ] && wc -l < "$SPOOL" || echo 0)"
    exit 0
fi

# O singura instanta: treptele dureaza minute, cronul e la 5 min.
exec 9>"$LOCK"
flock -n 9 || { log "deja ruleaza (lock $LOCK) — ies"; exit 0; }

if [ "$(id -u)" != 0 ]; then
    log "EROARE: treptele 3-4 cer root (systemctl). Ruleaza din cron-ul root."
    exit 1
fi

flush_spool

# Gratie la boot: imediat dupa pornire VPN-ul este LEGITIM jos (network-online.target,
# apoi PIA negociaza tunelul). Fara garda asta, primul cron de dupa reboot ar declara
# avarie si ar porni scara de reparare peste o conexiune care urca singura — oprind
# inclusiv pia.service in timp ce lucra. pia.service are Restart=always, deci lasam
# mecanismul normal sa incerce intai.
UPTIME=$(cut -d. -f1 /proc/uptime)
if [ "$UPTIME" -lt "${PIA_BOOT_GRACE:-300}" ] && [ "$FORCE" = 0 ]; then
    if vpn_healthy; then
        log "OK la $((UPTIME))s de la boot (regiune=$(pia get region) vpnip=$(pia get vpnip))"
    else
        log "boot recent (${UPTIME}s < ${PIA_BOOT_GRACE:-300}s) — las pia.service sa urce singur, nu escaladez"
    fi
    exit 0
fi

if vpn_healthy && [ "$FORCE" = 0 ]; then
    if [ -f "$OUTAGE_MARK" ]; then
        # Reparat intre timp: raportam abia acum, cu durata reala a caderii.
        mins=$(( ( $(date +%s) - $(cat "$OUTAGE_MARK") ) / 60 ))
        rm -f "$OUTAGE_MARK"
        alert "PIA restabilit ($(hostname))" \
"Tunelul functioneaza din nou dupa ${mins} min de cadere.
IP VPN: $(pia get vpnip) | regiune: $(pia get region)
Verifica flota: systemctl is-active binance.service (are Requires=pia.service)."
    fi
    log "OK (tun0 + HTTPS prin tunel), regiune=$(pia get region) vpnip=$(pia get vpnip)"
    exit 0
fi

[ -f "$OUTAGE_MARK" ] || date +%s > "$OUTAGE_MARK"
log "VPN NESANATOS (state=$(pia get connectionstate) daemon=$(daemon_responsive && echo ok || echo agatat)) — pornesc scara de reparare"

# Daemonul agatat nu se repara cu `connect`; sarim direct la restartul lui.
if daemon_responsive; then
    LADDER="rung_connect rung_reconnect rung_restart_daemon rung_reinstall"
else
    log "daemonul nu raspunde la piactl -> sar treptele 1-2"
    LADDER="rung_restart_daemon rung_reinstall"
fi

for rung in $LADDER; do
    "$rung" || continue
    if wait_healthy; then
        mins=$(( ( $(date +%s) - $(cat "$OUTAGE_MARK" 2>/dev/null || date +%s) ) / 60 ))
        rm -f "$OUTAGE_MARK"
        log "REPARAT la $rung (vpnip=$(pia get vpnip))"
        alert "PIA reparat automat ($(hostname))" \
"Tunelul a fost restabilit de $rung dupa ~${mins} min de cadere.
IP VPN: $(pia get vpnip) | regiune: $(pia get region)
Daca regiunea NU e cea dedicata, Binance va da -2015 pana repui tokenul DIP."
        # pia.service a fost oprit de treapta 3; il repunem, iar binance.service
        # (Requires=pia.service) porneste odata cu el.
        systemctl start pia.service     >/dev/null 2>&1
        systemctl start binance.service >/dev/null 2>&1
        exit 0
    fi
    log "$rung nu a rezolvat; escaladez"
done

log "ESEC: toate treptele epuizate, VPN-ul ramane jos"
alert "PIA NEREPARABIL automat ($(hostname))" \
"Am epuizat toate treptele (connect, reconnect, restart daemon, reinstalare) si tunelul tot nu urca.
Stare: $(pia get connectionstate) | regiune: $(pia get region) | internet brut: $(net_raw_ok && echo OK || echo PICAT)
ATENTIE: flota Binance e oprita cat timp pia.service e jos (binance.service are Requires=pia.service).
Cauze tipice: cont/abonament PIA expirat (AUTH_FAILED in /opt/piavpn/var/daemon.log) sau token DIP invalid."
exit 1

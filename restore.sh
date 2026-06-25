#!/usr/bin/env bash
# restore.sh — DISASTER RECOVERY: reface TOTUL pe o masina noua, dintr-o comanda.
#
# Presupune: repo deja clonat (ai nevoie de el ca sa rulezi scriptul) + folderul de
# SECRETE copiat de pe backup-ul tau (NU e in git — facut cu ./backup_secrets.sh).
#
#   git clone git@github.com:mpredut/binance.git ~/binance
#   cd ~/binance && ./restore.sh /cale/catre/binance-secrets-backup
#
# Folderul de secrete OGLINDESTE structura repo-ului (.env, hyperliquid/.env, keys/, ...).
# Cale repo presupusa: aceeasi ca productia (~/binance, user predut). Daca difera,
# editeaza systemd/*.service + crontab.txt inainte.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SECRETS="${1:-}"
fail() { echo "❌ $*" >&2; exit 1; }

# Fisiere care NU sunt in git (gitignored). Aceeasi lista ca in backup_secrets.sh.
SECRET_ITEMS=".env hyperliquid/.env kraken/.env 212trading/.env keys/apikeys.py keys/ed25519_private.pem keys/ed25519_public.pem"
STATE_ITEMS="hyperliquid/.state_dn_HYPE.json kraken/.state_HYPEUSD.json"   # optional (recuperare curata)

echo "===== RESTORE binance @ $ROOT ====="
[ -n "$SECRETS" ] || fail "Uz: $0 <folder_secrete>  (facut cu ./backup_secrets.sh)"
[ -d "$SECRETS" ] || fail "Folderul de secrete nu exista: $SECRETS"
command -v python3 >/dev/null || fail "python3 lipseste (apt install python3 python3-venv)"

echo "--- [1/5] restore secrete + stare din $SECRETS ---"
for rel in $SECRET_ITEMS $STATE_ITEMS; do
    if [ -f "$SECRETS/$rel" ]; then
        mkdir -p "$ROOT/$(dirname "$rel")"
        cp -p "$SECRETS/$rel" "$ROOT/$rel"
        echo "    ✔ $rel"
    else
        case " $STATE_ITEMS " in *" $rel "*) :;; *) echo "    ! lipseste din backup: $rel";; esac
    fi
done
[ -d "$SECRETS/cachedb" ] && cp -rp "$SECRETS/cachedb" "$ROOT/" && echo "    ✔ cachedb/"

echo "--- [2/5] venv (myenv) + dependinte ---"
[ -x "$ROOT/myenv/bin/python" ] || python3 -m venv "$ROOT/myenv" || fail "nu pot crea venv"
"$ROOT/myenv/bin/pip" install -q --upgrade pip
"$ROOT/myenv/bin/pip" install -q -r "$ROOT/requirements.txt" || fail "pip install esuat"
echo "    ✔ dependinte instalate"

echo "--- [3/5] unit-uri systemd (cere sudo) ---"
if sudo -v 2>/dev/null; then
    sudo cp "$ROOT/systemd/pia.service" "$ROOT/systemd/binance.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable pia.service binance.service
    echo "    ✔ unit-uri instalate + enabled"
else
    echo "    ! fara sudo — manual: sudo cp systemd/{pia,binance}.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable pia binance"
fi

echo "--- [4/5] crontab ---"
crontab "$ROOT/crontab.txt" && echo "    ✔ cron instalat (healthcheck --supervise porneste botii in <=5 min)"

echo "--- [5/5] GATA ---"
echo "Mai trebuie (o singura data): configureaza PIA/VPN (login), apoi:"
echo "    sudo systemctl start pia binance     # flota porneste; botii vin prin cron"
echo "    ./healthcheck.sh --check             # verifica ca toate sunt 'ok'"

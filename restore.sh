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
# seteaza TRADING_ROOT si adapteaza profilul systemd/ daca este necesar.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SECRETS="${1:-}"
fail() { echo "❌ $*" >&2; exit 1; }

echo "===== RESTORE binance @ $ROOT ====="
[ -n "$SECRETS" ] || fail "Uz: $0 <folder_secrete>  (facut cu ./backup_secrets.sh)"
[ -d "$SECRETS" ] || fail "Folderul de secrete nu exista: $SECRETS"
command -v python3 >/dev/null || fail "python3 lipseste (apt install python3 python3-venv)"

echo "--- [1/5] restore secrete + stare din $SECRETS ---"
# _machine contine date externe repo-ului si este restaurat separat.
tar cf - --exclude='./_machine' -C "$SECRETS" . | tar xf - -C "$ROOT"
if [ -f "$SECRETS/_machine/piatoken.txt" ]; then
    install -m 0600 "$SECRETS/_machine/piatoken.txt" "$HOME/piatoken.txt"
fi
echo "    ✔ restaurat (.env, keys/, .state*, cachedb/, token PIA)"

echo "--- [2/5] venv (myenv) + dependinte ---"
[ -x "$ROOT/myenv/bin/python" ] || python3 -m venv "$ROOT/myenv" || fail "nu pot crea venv"
"$ROOT/myenv/bin/pip" install -q --upgrade pip
"$ROOT/myenv/bin/pip" install -q -r "$ROOT/requirements.txt" || fail "pip install esuat"
echo "    ✔ dependinte instalate"

echo "--- [3/5] systemd + DNS + SSH + cron (cere sudo) ---"
if sudo -v 2>/dev/null; then
    sudo bash "$ROOT/systemd/install_prod.sh"
    echo "    ✔ profil PROD instalat"
else
    echo "    ! fara sudo — manual: sudo bash systemd/install_prod.sh"
fi

echo "--- [4/5] verificare cron ---"
crontab -l >/dev/null 2>&1 && echo "    ✔ cron instalat de profilul PROD"

echo "--- [5/5] GATA ---"
echo "Mai trebuie (o singura data): instaleaza aplicația PIA și autentifică-te, apoi:"
echo "    sudo systemctl start pia binance     # flota porneste; botii vin prin cron"
echo "    ./healthcheck.sh --check             # verifica ca toate sunt 'ok'"

#!/usr/bin/env bash
# restore.sh — DISASTER RECOVERY: reface TOTUL pe o masina noua, dintr-o comanda.
#
# Presupune: repo deja clonat (ai nevoie de el ca sa rulezi scriptul) + folderul de
# SECRETE copiat de pe backup-ul tau (NU e in git — facut cu ./backup_secrets.sh).
#
#   git clone <repository-url> /srv/trading/current
#   cd /srv/trading/current && ./restore.sh /path/to/secrets-backup
#
# Folderul de secrete OGLINDESTE structura repo-ului (.env, hyperliquid/.env, keys/, ...).
# The repository path and account name are detected and passed to the installer.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SECRETS="${1:-}"
fail() { echo "❌ $*" >&2; exit 1; }

echo "===== RESTORE binance @ $ROOT ====="
[ -n "$SECRETS" ] || fail "Uz: $0 <folder_secrete>  (facut cu ./backup_secrets.sh)"
[ -d "$SECRETS" ] || fail "Folderul de secrete nu exista: $SECRETS"
command -v python3 >/dev/null || fail "python3 lipseste (apt install python3 python3-venv)"

echo "--- [1/5] restore secrete + stare din $SECRETS ---"
# _machine holds data external to the repo and is restored separately.
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
    sudo env TRADING_ROOT="$ROOT" TRADING_USER="$(id -un)" \
        TRADING_PYTHON="$ROOT/myenv/bin/python" \
        bash "$ROOT/systemd/install_prod.sh"
    echo "    ✔ profil PROD instalat"
else
    echo "    ! fara sudo — manual: sudo bash systemd/install_prod.sh"
fi

echo "--- [4/5] verificare cron ---"
crontab -l >/dev/null 2>&1 && echo "    ✔ cron instalat de profilul PROD"

echo "--- [5/5] GATA ---"
echo "Still needed (once): install the PIA application and log in, then:"
echo "    sudo systemctl start pia binance     # the fleet starts; the bots arrive through cron"
echo "    ./healthcheck.sh --check             # check that everything is 'ok'"

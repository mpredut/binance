#!/usr/bin/env bash
# backup_secrets.sh — backup COMPLET al a tot ce NU e in git (secrete + stare boti/provideri),
# descoperit AUTOMAT din git (nimic hardcodat) minus regenerabilele (venv/log/pyc/lock/html).
# Prinde: .env-uri, keys/, TOATE .state_* (HL/Kraken/T212/xstock/trailing), cachedb/,
# .watchdog_state, trade_cooldown, priceanalysis.json etc. — si fisiere viitoare, automat.
# Rezultat: folder + tarball IN AFARA repo-ului. Copiaza tarball-ul OFF-MACHINE.
#
#   ./backup_secrets.sh                 # -> ~/<checkout>-secrets-backup/ + .tar.gz
#   ./backup_secrets.sh /media/usb/bk   # destinatie custom
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HOME/$(basename "$ROOT")-secrets-backup}"
case "$OUT" in "$ROOT"|"$ROOT"/*) echo "❌ destinatia NU poate fi in repo (s-ar comite secrete): $OUT"; exit 1;; esac

cd "$ROOT"
# Anything gitignored is not in git, so it needs a backup. Only regenerable files are excluded.
LIST="$(git ls-files --others --ignored --exclude-standard \
    | grep -vE '^(myenv|\.venv)/' \
    | grep -vE '(__pycache__|\.pyc$|\.log($|\.)|\.lock$|^index\.html$|^\.claude/)')"
[ -n "$LIST" ] || { echo "❌ nimic de salvat (git ls-files gol?)"; exit 1; }

rm -rf "$OUT"; mkdir -p "$OUT"
printf '%s\n' "$LIST" | tar cf - -C "$ROOT" -T - | tar xf - -C "$OUT"

# Date de masina aflate intentionat in afara repo-ului. Tokenul PIA este secret
# and it is required to restore the dedicated IP on a fresh install.
mkdir -p "$OUT/_machine"
PIA_TOKEN="${PIA_DIP_TOKEN:-$HOME/piatoken.txt}"
if [ -f "$PIA_TOKEN" ]; then
    install -m 0600 "$PIA_TOKEN" "$OUT/_machine/piatoken.txt"
fi

tar czf "$OUT.tar.gz" -C "$OUT" .   # latest, cale stabila pt pull-ul Windows
chmod -R go-rwx "$OUT" 2>/dev/null || true
chmod 600 "$OUT.tar.gz"

# HISTORY: also keep a DATED copy (the last KEEP days). Without history, a corruption or
# stergere de secrete intra in backup la 03:30 si SUPRASCRIE unica copie buna.
KEEP="${BACKUP_KEEP:-7}"
DATED="$OUT-$(date +%Y%m%d).tar.gz"
cp -p "$OUT.tar.gz" "$DATED" && chmod 600 "$DATED"
ls -1t "$OUT"-????????.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

N="$(find "$OUT" -type f | wc -l)"
echo "=== backup COMPLET: $N fisiere (secrete + stare) ==="
printf '%s\n' "$LIST" | sed 's/^/    /'
echo "Folder : $OUT"
echo "Tarball: $OUT.tar.gz (600)  + istoric: $DATED (pastrez ultimele $KEEP)"
echo "⚠ Copy the tarball OFF-MACHINE. It holds the HL wallet key + every API key. NOT in git!"

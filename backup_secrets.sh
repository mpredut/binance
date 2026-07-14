#!/usr/bin/env bash
# backup_secrets.sh — backup COMPLET al a tot ce NU e in git (secrete + stare boti/provideri),
# descoperit AUTOMAT din git (nimic hardcodat) minus regenerabilele (venv/log/pyc/lock/html).
# Prinde: .env-uri, keys/, TOATE .state_* (HL/Kraken/T212/xstock/trailing), cachedb/,
# .watchdog_state, trade_cooldown, priceanalysis.json etc. — si fisiere viitoare, automat.
# Rezultat: folder + tarball IN AFARA repo-ului. Copiaza tarball-ul OFF-MACHINE.
#
#   ./backup_secrets.sh                 # -> ~/binance-secrets-backup/ + .tar.gz
#   ./backup_secrets.sh /media/usb/bk   # destinatie custom
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HOME/binance-secrets-backup}"
case "$OUT" in "$ROOT"|"$ROOT"/*) echo "❌ destinatia NU poate fi in repo (s-ar comite secrete): $OUT"; exit 1;; esac

cd "$ROOT"
# Tot ce e gitignored = nu e in git = trebuie backup. Excludem doar regenerabilele.
LIST="$(git ls-files --others --ignored --exclude-standard \
    | grep -vE '^(myenv|\.venv)/' \
    | grep -vE '(__pycache__|\.pyc$|\.log($|\.)|\.lock$|^index\.html$|^\.claude/)')"
[ -n "$LIST" ] || { echo "❌ nimic de salvat (git ls-files gol?)"; exit 1; }

rm -rf "$OUT"; mkdir -p "$OUT"
printf '%s\n' "$LIST" | tar czf "$OUT.tar.gz" -C "$ROOT" -T -   # tarball "latest" (cale STABILA pt pull-ul Windows)
tar xzf "$OUT.tar.gz" -C "$OUT"                                  # si ca folder (de copiat)
chmod -R go-rwx "$OUT" 2>/dev/null || true
chmod 600 "$OUT.tar.gz"

# ISTORIC: pastreaza si o copie DATATA (ultimele KEEP zile). Fara istoric, o corupere/
# stergere de secrete intra in backup la 03:30 si SUPRASCRIE unica copie buna.
KEEP="${BACKUP_KEEP:-7}"
DATED="$OUT-$(date +%Y%m%d).tar.gz"
cp -p "$OUT.tar.gz" "$DATED" && chmod 600 "$DATED"
ls -1t "$OUT"-????????.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

N="$(printf '%s\n' "$LIST" | grep -c .)"
echo "=== backup COMPLET: $N fisiere (secrete + stare) ==="
printf '%s\n' "$LIST" | sed 's/^/    /'
echo "Folder : $OUT"
echo "Tarball: $OUT.tar.gz (600)  + istoric: $DATED (pastrez ultimele $KEEP)"
echo "⚠ Copiaza tarball-ul OFF-MACHINE. Contine cheia wallet HL + toate cheile API. NU in git!"

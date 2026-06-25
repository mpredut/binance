#!/usr/bin/env bash
# backup_secrets.sh — aduna TOATE secretele (gitignored) + starea botilor intr-un folder
# + tarball, IN AFARA repo-ului. NU comite nimic in git.
# Apoi COPIAZA tarball-ul OFF-MACHINE (USB / cloud privat / alta masina).
#
#   ./backup_secrets.sh                 # -> ~/binance-secrets-backup/ + .tar.gz
#   ./backup_secrets.sh /media/usb/bk   # destinatie custom
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HOME/binance-secrets-backup}"   # IMPLICIT in afara repo-ului (nu se comite)

# Aceeasi lista ca in restore.sh.
SECRET_ITEMS=".env hyperliquid/.env kraken/.env 212trading/.env keys/apikeys.py keys/ed25519_private.pem keys/ed25519_public.pem"
STATE_ITEMS="hyperliquid/.state_dn_HYPE.json kraken/.state_HYPEUSD.json"   # optional

case "$OUT" in "$ROOT"|"$ROOT"/*) echo "❌ Destinatia NU poate fi in repo (s-ar comite secrete): $OUT"; exit 1;; esac
rm -rf "$OUT"; mkdir -p "$OUT"

echo "=== copiez secrete + stare in $OUT (oglindeste structura repo) ==="
for rel in $SECRET_ITEMS $STATE_ITEMS; do
    if [ -f "$ROOT/$rel" ]; then
        mkdir -p "$OUT/$(dirname "$rel")"
        cp -p "$ROOT/$rel" "$OUT/$rel"
        echo "    ✔ $rel"
    else
        case " $STATE_ITEMS " in *" $rel "*) :;; *) echo "    ! lipseste (secret!): $rel";; esac
    fi
done
[ -d "$ROOT/cachedb" ] && cp -rp "$ROOT/cachedb" "$OUT/" && echo "    ✔ cachedb/"

chmod -R go-rwx "$OUT" 2>/dev/null || true
TAR="$OUT.tar.gz"
tar czf "$TAR" -C "$(dirname "$OUT")" "$(basename "$OUT")"
chmod 600 "$TAR"

echo "=== GATA ==="
echo "Folder : $OUT"
echo "Tarball: $TAR  (drepturi 600)"
echo "⚠ COPIAZA tarball-ul OFF-MACHINE (USB/cloud privat). Contine cheia wallet HL. NU in git!"

#!/usr/bin/env bash
# backup_remote.sh — backup local (backup_secrets.sh) + upload CRIPTAT off-site, descentralizat (Storj).
# Criptarea o face rclone (remote 'crypt' care impacheteaza remote-ul Storj) -> in Storj ajunge
# DOAR ciphertext. Parola de crypt o tii SEPARAT (off-server) ca sa poti decripta la restore.
# Suprascrie ultima versiune (fara bloat). Vezi DISASTER_RECOVERY.md pt config + restore.
#
# Cron (instalat la finalul setarii): 30 3 * * * cd ~/binance && ./backup_remote.sh >> logs/backup_remote.log 2>&1
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
RCLONE="${RCLONE:-$HOME/bin/rclone}"
command -v "$RCLONE" >/dev/null 2>&1 || RCLONE=rclone
REMOTE="${RCLONE_REMOTE:-storj-crypt:}"          # remote-ul crypt (peste Storj)
TAR="$HOME/binance-secrets-backup.tar.gz"
DEST="${REMOTE}binance-secrets-backup.tar.gz"

echo "$(date '+%F %T') === backup_remote ==="
# 1. backup local proaspat (folder + tarball) — refoloseste scriptul existent
"$ROOT/backup_secrets.sh" >/dev/null
[ -f "$TAR" ] || { echo "❌ tarball local lipsa: $TAR"; exit 1; }

# 2. upload CRIPTAT in Storj (suprascrie ultima versiune)
"$RCLONE" copyto "$TAR" "$DEST" --transfers 1
echo "$(date '+%F %T') ✔ urcat criptat -> $DEST  ($("$RCLONE" size "$DEST" 2>/dev/null | tr '\n' ' '))"

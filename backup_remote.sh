#!/usr/bin/env bash
# backup_remote.sh — backup local (backup_secrets.sh) + upload CRIPTAT off-site, descentralizat (Storj).
# rclone does the encryption (a 'crypt' remote wrapping the Storj remote) -> what reaches Storj is
# DOAR ciphertext. Parola de crypt o tii SEPARAT (off-server) ca sa poti decripta la restore.
# Suprascrie ultima versiune (fara bloat). Vezi docs/DISASTER_RECOVERY.md pt config + restore.
#
# The archive name follows the checkout directory unless BACKUP_NAME overrides it.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
RCLONE="${RCLONE:-$HOME/bin/rclone}"
command -v "$RCLONE" >/dev/null 2>&1 || RCLONE=rclone
REMOTE="${RCLONE_REMOTE:-storj-crypt:}"          # remote-ul crypt (peste Storj)
BACKUP_NAME="${BACKUP_NAME:-$(basename "$ROOT")-secrets-backup}"
TAR="${BACKUP_TAR:-$HOME/$BACKUP_NAME.tar.gz}"
DEST="${REMOTE}${BACKUP_NAME}.tar.gz"

echo "$(date '+%F %T') === backup_remote ==="
# 1. backup local proaspat (folder + tarball) — refoloseste scriptul existent
"$ROOT/backup_secrets.sh" >/dev/null
[ -f "$TAR" ] || { echo "❌ local tarball missing: $TAR"; exit 1; }

# 2. upload CRIPTAT in Storj (suprascrie ultima versiune)
"$RCLONE" copyto "$TAR" "$DEST" --transfers 1
echo "$(date '+%F %T') ✔ urcat criptat -> $DEST  ($("$RCLONE" size "$DEST" 2>/dev/null | tr '\n' ' '))"

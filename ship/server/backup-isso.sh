#!/bin/ksh
# Encrypted backup of the isso comment database (root; cron daily, and from ops/backup scripts).
#
# Usage: backup-isso.sh daily|manual|predeploy
#
# sqlite3 .backup makes a consistent copy while isso runs; PRAGMA integrity_check must pass;
# the copy is encrypted with age to BACKUP_AGE_PUBLIC_KEY (public key only on the server) and
# written to /var/ship/backups/comments-<label>-<UTC>.db.age. Retention: daily and manual
# BACKUP_KEEP_DAYS days, predeploy the newest 5. The last line is "BACKUP <path>".

PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
export PATH
ROOT=/var/ship
. "$ROOT/env_vars.sh"
DB=/var/isso/db/comments.db
DIR="$ROOT/backups"
LABEL="${1:-}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
case "$LABEL" in daily|manual|predeploy) ;; *) echo "Usage: $0 daily|manual|predeploy" >&2; exit 2 ;; esac
case "${BACKUP_AGE_PUBLIC_KEY:-}" in age1*) ;; *) echo "$(ts) backup: BACKUP_AGE_PUBLIC_KEY is not an age public key" >&2; exit 1 ;; esac
if [ ! -f "$DB" ]; then
    echo "$(ts) backup: no comment database yet ($DB); nothing to back up"
    exit 0
fi

umask 077
install -d -o root -g wheel -m 700 "$DIR"
OUT="$DIR/comments-${LABEL}-$(date -u +%Y%m%dT%H%M%SZ).db.age"
TMPDB="$(mktemp "$DIR/.tmp.XXXXXXXX")" || exit 1
trap 'rm -f "$TMPDB"' EXIT

sqlite3 "$DB" ".backup '$TMPDB'" || { echo "$(ts) backup: sqlite3 .backup failed" >&2; exit 1; }
[ "$(sqlite3 "$TMPDB" 'PRAGMA integrity_check;')" = ok ] || { echo "$(ts) backup: integrity check failed" >&2; exit 1; }
age -r "$BACKUP_AGE_PUBLIC_KEY" -o "$OUT" "$TMPDB" || { rm -f "$OUT"; echo "$(ts) backup: age failed" >&2; exit 1; }

find "$DIR" -name 'comments-daily-*.db.age' -mtime "+$KEEP_DAYS" -exec rm -f {} +
find "$DIR" -name 'comments-manual-*.db.age' -mtime "+$KEEP_DAYS" -exec rm -f {} +
ls -1t "$DIR"/comments-predeploy-*.db.age 2>/dev/null | tail -n +6 | while read -r _old; do rm -f "$_old"; done

echo "$(ts) backup: $(basename "$OUT") ($(wc -c < "$OUT" | tr -d ' ') bytes)"
echo "BACKUP $OUT"

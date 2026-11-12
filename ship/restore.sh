#!/usr/bin/env bash
# List, test (drill) or restore comment database backups. Decryption happens on this machine;
# the age private key never goes to the server.
#
# Usage (from the sites repo root):
#   bash ../bones/ship/restore.sh prod --list               backups on the server and in BACKUP_DIR
#   bash ../bones/ship/restore.sh prod drill [file]         decrypt + integrity check + comment count
#                                                           vs live (no file: takes a fresh backup)
#   bash ../bones/ship/restore.sh prod live <file>          replace the live database
#
# <file> is a local .db.age (or a plain .db, e.g. the database copied from the old server).
# Private key: BACKUP_AGE_PRIVATE_KEY, else age-backup.key in the secrets folder, else ~/age-sites-backup.key.
# `live` asks for confirmation, takes a safety backup (predeploy), stops isso, swaps the file,
# starts isso; the restart guard is paused meanwhile.
set -euo pipefail

SHIP_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=lib/common.sh
. "$SHIP_DIR/lib/common.sh"

USAGE_REST="--list | drill [file] | live <file>"
parse_env_arg "$@"
ACTION="${2:-}"; FILE="${3:-}"
resolve_sites_root
load_env

PY=""
for c in "$BONES_ROOT/venv/Scripts/python.exe" "$BONES_ROOT/venv/bin/python" "$(type -P python3 || true)" "$(type -P python || true)"; do
  if [ -n "$c" ] && [ -x "$c" ]; then PY="$c"; break; fi
done

WORK="$(mktemp -d)"; chmod 700 "$WORK"
cleanup() { remote_stage_cleanup; rm -rf "$WORK"; }
trap cleanup EXIT

age_key() {
  local k
  for k in "$(expand_tilde "${BACKUP_AGE_PRIVATE_KEY:-}")" "${SECRETS_DIR}/age-backup.key" "$HOME/age-sites-backup.key"; do
    if [ -n "$k" ] && [ -f "$k" ]; then printf '%s' "$k"; return 0; fi
  done
  log_error "No age private key (BACKUP_AGE_PRIVATE_KEY, ${SECRETS_DIR}/age-backup.key or ~/age-sites-backup.key)"
}

# plain_db <file> -> path of a decrypted copy in WORK
plain_db() {
  local f="$1"
  [ -f "$f" ] || log_error "No such file: $f"
  case "$f" in
    *.age) command -v age >/dev/null || log_error "age not found (winget install FiloSottile.age)"
           age -d -i "$(age_key)" -o "$WORK/restore.db" "$f" || log_error "Decryption failed (wrong key?)" ;;
    *.db)  cp "$f" "$WORK/restore.db" ;;
    *)     log_error "Expected a .db.age or .db file" ;;
  esac
  printf '%s' "$WORK/restore.db"
}

# check_db <file>: integrity + comment count (Python's sqlite3; no sqlite3 binary needed locally)
check_db() {
  [ -n "$PY" ] || log_error "Python is needed to check the database"
  "$PY" - "$1" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
ok = c.execute("pragma integrity_check").fetchone()[0]
n = c.execute("select count(*) from comments").fetchone()[0]
t = c.execute("select count(*) from threads").fetchone()[0]
print(f"integrity: {ok}; comments: {n}; threads: {t}")
sys.exit(0 if ok == "ok" else 1)
PY
}

live_count() { ssh_as "$SERVER_USER" "doas sqlite3 /var/isso/db/comments.db 'select count(*) from comments;'" 2>/dev/null || echo "?"; }

case "$ACTION" in
  --list)
    log_info "On the server (/var/ship/backups):"
    ssh_as "$SERVER_USER" "doas ls -lT /var/ship/backups" || true
    log_info "On this machine (${LOCAL_BACKUP_DIR}):"
    ls -l "$LOCAL_BACKUP_DIR" 2>/dev/null || echo "  (none)"
    ;;
  drill)
    if [ -z "$FILE" ]; then
      bash "$SHIP_DIR/backup.sh" "$DEPLOY_ENV"
      FILE="$(ls -1t "$LOCAL_BACKUP_DIR"/comments-*.db.age 2>/dev/null | head -1)"
      [ -n "$FILE" ] || log_error "No backup to drill"
    fi
    log_info "Drill with $(basename "$FILE")"
    db="$(plain_db "$FILE")"
    check_db "$db" || log_error "Backup failed the integrity check"
    log_info "Live database: $(live_count) comments"
    log_success "Drill passed (record the date)."
    ;;
  live)
    [ -n "$FILE" ] || log_error "Usage: restore.sh ${DEPLOY_ENV} live <file>"
    db="$(plain_db "$FILE")"
    check_db "$db" || log_error "The file failed the integrity check; not restoring"
    log_warning "This replaces the live comment database on ${SERVER_IP} ($(live_count) comments now)."
    read -r -p "Type 'restore' to continue: " answer
    [ "$answer" = restore ] || log_error "Cancelled"
    remote_stage_create
    scp_to "$SERVER_USER" "$REMOTE_STAGE" "$db"
    remote_root "Restore" STAGE="$REMOTE_STAGE" <<'EOF'
set -e
PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
touch /var/ship/maintenance
trap 'rm -f /var/ship/maintenance' EXIT
/var/ship/backup-isso.sh predeploy
rcctl stop isso >/dev/null || true
install -d -o _isso -g _isso -m 700 /var/isso/db
install -o _isso -g _isso -m 600 "$STAGE/restore.db" /var/isso/db/comments.db
rm -f /var/isso/db/comments.db-wal /var/isso/db/comments.db-shm
rcctl start isso >/dev/null
sleep 2
rcctl check isso >/dev/null && echo "isso running; $(sqlite3 /var/isso/db/comments.db 'select count(*) from comments;') comments"
EOF
    log_success "Restored. The previous database is in /var/ship/backups as comments-predeploy-*."
    ;;
  *) log_error "Usage: bash ../bones/ship/restore.sh ${DEPLOY_ENV} ${USAGE_REST}" ;;
esac

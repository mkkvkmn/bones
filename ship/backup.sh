#!/usr/bin/env bash
# Take a fresh encrypted backup of the comment database and download it.
#
# Usage (from the sites repo root):
#   bash ../bones/ship/backup.sh prod
#
# The server writes comments-manual-<UTC>.db.age (age-encrypted to BACKUP_AGE_PUBLIC_KEY; the
# server never has the private key). It is copied to BACKUP_DIR (default ~/.backups/sites/,
# mode 700, must be outside every repo). Local copies older than BACKUP_KEEP_DAYS are deleted.
# Same layout as core/ship (~/.backups/<app>/).
set -euo pipefail

SHIP_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=lib/common.sh
. "$SHIP_DIR/lib/common.sh"

parse_env_arg "$@"
resolve_sites_root
load_env

(umask 077; mkdir -p "$LOCAL_BACKUP_DIR")
chmod 700 "$LOCAL_BACKUP_DIR" 2>/dev/null || true
assert_outside_repos "BACKUP_DIR" "$LOCAL_BACKUP_DIR"

log_info "Taking a backup on the server..."
out="$(ssh_as "$SERVER_USER" "doas /var/ship/backup-isso.sh manual")" || { printf '%s\n' "$out"; log_error "Server backup failed"; }
printf '%s\n' "$out" | grep -v '^BACKUP ' || true
remote_file="$(printf '%s\n' "$out" | sed -n 's/^BACKUP //p' | tail -1)"
[ -n "$remote_file" ] || { log_warning "Nothing to back up yet (no comment database on the server)"; exit 0; }
[[ "$remote_file" =~ ^/var/ship/backups/comments-manual-[0-9TZ]+\.db\.age$ ]] || log_error "Unexpected backup path from server"

local_file="${LOCAL_BACKUP_DIR}/$(basename "$remote_file")"
# The backup dir is root-only on the server, so stream it through doas (the file is encrypted).
ssh_as "$SERVER_USER" "doas cat '${remote_file}'" > "${local_file}.part" || { rm -f "${local_file}.part"; log_error "Download failed"; }
[ -s "${local_file}.part" ] || { rm -f "${local_file}.part"; log_error "Downloaded file is empty"; }
mv "${local_file}.part" "$local_file"
chmod 600 "$local_file" 2>/dev/null || true
log_success "Saved ${local_file}"

find "$LOCAL_BACKUP_DIR" -maxdepth 1 -name 'comments-*.db.age' -mtime "+${BACKUP_KEEP_DAYS:-14}" -print -delete \
  | sed 's/^/[INFO] deleted old local backup: /' || true

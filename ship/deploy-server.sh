#!/usr/bin/env bash
# Set up (or re-converge) the OpenBSD server. Safe to re-run: the second run changes nothing.
#
# Usage (from the sites repo root, Git Bash):
#   bash ../bones/ship/deploy-server.sh prod
#
# Uploads server/setup.sh, config/, the server scripts, the sites config (ship/sites.conf and
# ship/<site>.nginx.extra|.csp) and env_vars as one bundle into a private (700) temp dir,
# runs setup.sh as root, and deletes the dir.
#
# First run on a fresh VPS: SERVER_USER does not exist yet, so it connects as root (with the key
# Vultr installed), creates SERVER_USER with the same key and passwordless doas, then checks that
# SERVER_USER can log in and run doas, and only then re-runs as SERVER_USER with root SSH off.
set -euo pipefail

SHIP_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=lib/common.sh
. "$SHIP_DIR/lib/common.sh"

parse_env_arg "$@"
resolve_sites_root
load_env
validate_sites_conf
assert_no_leaks

BUNDLE_DIR="$(mktemp -d)"
cleanup() { remote_stage_cleanup; rm -rf "$BUNDLE_DIR"; }
trap cleanup EXIT
chmod 700 "$BUNDLE_DIR"

# One tarball with LF line endings (a Windows checkout may have CRLF).
make_bundle() {
  local b="$BUNDLE_DIR/stage"
  mkdir -p "$b/config" "$b/server" "$b/sites"
  cp "$SHIP_DIR/server/setup.sh" "$b/"
  cp "$SHIP_DIR"/config/* "$b/config/"
  cp "$SHIP_DIR"/server/*.sh "$b/server/"
  cp "$SITES_CONF" "$b/sites/sites.conf"
  local f
  for f in "$SITES_ROOT"/ship/*.nginx.extra "$SITES_ROOT"/ship/*.csp; do
    [ -f "$f" ] && cp "$f" "$b/sites/"
  done
  cp "$ENV_FILE" "$b/env_vars.sh"
  find "$b" -type f -exec sed -i 's/\r$//' {} +
  tar -czf "$BUNDLE_DIR/bundle.tgz" -C "$b" .
}

# run_setup <ssh user> <lock root 0|1>
run_setup() {
  local user="$1" lock="$2" sudo=""
  [ "$user" = root ] || sudo="doas"
  remote_stage_create "$user"
  scp_to "$user" "$REMOTE_STAGE" "$BUNDLE_DIR/bundle.tgz"
  log_info "Running setup on ${SERVER_IP} as ${user} (LOCK_ROOT=${lock}); a fresh host takes a few minutes..."
  ssh_as "$user" "cd '${REMOTE_STAGE}' && tar -xzf bundle.tgz && rm -f bundle.tgz && ${sudo} env LOCK_ROOT=${lock} ksh '${REMOTE_STAGE}/setup.sh'" \
    || log_error "Server setup failed (see output above). Nothing after the failing step was changed."
  remote_stage_cleanup
}

make_bundle
ensure_known_host "$SERVER_USER"

if can_ssh "$SERVER_USER" "doas true"; then
  run_setup "$SERVER_USER" 1
elif can_ssh root; then
  log_info "First run: ${SERVER_USER} cannot log in yet, so this run connects as root."
  run_setup root 0
  can_ssh "$SERVER_USER" "doas true" \
    || log_error "${SERVER_USER} still cannot log in and run doas. Root SSH is left on; nothing is locked. See the output above."
  log_success "${SERVER_USER} can log in and use doas. Turning root SSH off..."
  run_setup "$SERVER_USER" 1
else
  log_error "Cannot SSH to ${SERVER_IP} as ${SERVER_USER} or root with ${SSH_KEY}. Check SERVER_IP, SSH_KEY_PATH and that the VPS has your public key. After a reinstall the server has a new host key: run  ssh-keygen -R ${SERVER_IP}  and try again."
fi

if can_ssh root; then
  log_warning "Root can still log in over SSH. Re-run this script; if it persists, check /etc/ssh/sshd_config on the server."
else
  log_success "Root SSH login is off; ${SERVER_USER} is the only SSH user."
fi
log_success "Server converged. Deploy the sites with: bash ../bones/ship/deploy-site.sh ${DEPLOY_ENV} <site>"

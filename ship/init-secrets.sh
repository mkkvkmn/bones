#!/usr/bin/env bash
# Create the server settings file for one environment, without printing a value.
#
# Usage (from the sites repo root, Git Bash on Windows):
#   bash ../bones/ship/init-secrets.sh prod
#
# Creates SHIP_SECRETS_DIR/env_vars.<env>.sh (default ~/.secrets/<sites repo folder>/, mode 700)
# from bones/ship/env/env_vars.example and generates ISSO_ADMIN_PASSWORD. An existing file is
# never overwritten: a re-run only lists the keys still to fill, and keys a newer bones version
# added to the example. Same behaviour as core's ship/scripts/init-secrets.sh.
set -euo pipefail

SHIP_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=lib/common.sh
. "$SHIP_DIR/lib/common.sh"

parse_env_arg "$@"
resolve_sites_root
command -v openssl >/dev/null 2>&1 || log_error "openssl not found (it ships with Git for Windows)"

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR" 2>/dev/null || true
assert_outside_repos "The secrets folder" "$SECRETS_DIR"
ENV_FILE="${SECRETS_DIR}/env_vars.${DEPLOY_ENV}.sh"

build_env_vars() {
  local out="$1"
  {
    printf '# Created by bones/ship/init-secrets.sh on %s.\n' "$(date -u +%Y-%m-%d)"
    printf '# Secret: never commit, paste into chats or share. Fill the values the script listed.\n\n'
    cat "$ENV_EXAMPLE"
  } > "$out"
  set_var "$out" DEPLOY_ENV "$DEPLOY_ENV"
  set_var "$out" ISSO_ADMIN_PASSWORD "$(rand_hex 24)"
}

if [ -f "$ENV_FILE" ]; then
  log_info "Kept existing $(basename "$ENV_FILE") (never overwritten)"
  # A rotation: the user deleted the ISSO_ADMIN_PASSWORD line, so generate a new one.
  if ! has_var "$ENV_FILE" ISSO_ADMIN_PASSWORD; then
    set_var "$ENV_FILE" ISSO_ADMIN_PASSWORD "$(rand_hex 24)"
    log_success "Generated a new ISSO_ADMIN_PASSWORD (run deploy-server.sh to apply it)"
  fi
else
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  chmod 600 "$tmp" 2>/dev/null || true
  if ! build_env_vars "$tmp"; then rm -f "$tmp"; log_error "Could not build $(basename "$ENV_FILE")"; fi
  chmod 600 "$tmp" 2>/dev/null || true
  mv "$tmp" "$ENV_FILE"
  log_success "Created ${ENV_FILE}"
fi

# Report key names only.
missing=(); todo=(); optional=()
while IFS= read -r key; do has_var "$ENV_FILE" "$key" || missing+=("$key"); done < <(env_keys "$ENV_EXAMPLE")
while IFS= read -r key; do todo+=("$key"); done < <(unfilled_required_keys "$ENV_FILE")
while IFS= read -r key; do
  [[ "$key" =~ $OPTIONAL_EMPTY_KEYS_RE ]] && optional+=("$key")
done < <(empty_keys "$ENV_FILE")

echo
log_info "$(basename "$ENV_FILE") (key names only; values are never shown):"
[ "${#todo[@]}" -eq 0 ]     || printf '  fill in:  %s\n' "${todo[*]}"
[ "${#missing[@]}" -eq 0 ]  || printf '  missing (copy from bones/ship/env/env_vars.example): %s\n' "${missing[*]}"
[ "${#optional[@]}" -eq 0 ] || printf '  optional, empty: %s\n' "${optional[*]}"
if [ "${#todo[@]}" -eq 0 ] && [ "${#missing[@]}" -eq 0 ]; then printf '  complete\n'; fi
echo
log_info "Edit it with:  notepad \"$(cygpath -w "$ENV_FILE" 2>/dev/null || printf '%s' "$ENV_FILE")\""
log_info "Each key is explained in the file. MAIL_RELAY=none makes MAIL_FROM and TEM_* unnecessary."

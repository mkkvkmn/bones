# shellcheck shell=bash disable=SC2034
# Shared helpers for the bones/ship scripts that run on your machine (Git Bash on Windows,
# or bash on Linux/macOS). Derived from core/ship/scripts/lib/deploy-common.sh; trimmed to
# what static sites + isso need.
#
# Layout these scripts expect:
#   <somewhere>/bones/ship/          this tooling (public repo: no hostnames, no secrets)
#   <sites repo>/ship/sites.conf     which sites go on the server (run the scripts from here)
#   ~/.secrets/<sites repo name>/    env_vars.<env>.sh (secrets; outside every repo)
#
# Usage in a script:
#   SHIP_DIR=...; . "$SHIP_DIR/lib/common.sh"
#   parse_env_arg "$@"; resolve_sites_root; load_env
#
# Secrets rule: values are never printed. Uploads go to a private (mode 700) temp dir.

# shellcheck source=env-file.sh
. "$(dirname "${BASH_SOURCE[0]}")/env-file.sh"

# -------------------------
# Logging
# -------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { printf "${BLUE}[INFO]${NC} %s\n" "$*"; }
log_success() { printf "${GREEN}[OK]${NC} %s\n" "$*"; }
log_warning() { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
log_error()   { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; exit 1; }

# WSL bash on a Windows checkout half-works (see core). Stop early with the right command.
if [ -n "${WSL_DISTRO_NAME:-}" ] || [ -e /proc/sys/fs/binfmt_misc/WSLInterop ]; then
  case "$PWD" in
    /mnt/*) log_error "This is WSL bash. Use Git Bash: \"C:\\Program Files\\Git\\bin\\bash.exe\" ../bones/ship/$(basename -- "$0") ..." ;;
  esac
fi

SHIP_DIR="${SHIP_DIR:-$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
BONES_ROOT="$(CDPATH='' cd -- "$SHIP_DIR/.." && pwd)"

# Keys that may stay empty in env_vars (everything else must be filled).
OPTIONAL_EMPTY_KEYS_RE='^(MONITOR_HEARTBEAT_URL|ISSO_ADMIN_ALLOW_IP|BACKUP_AGE_PRIVATE_KEY)$'

# -------------------------
# Arguments and locations
# -------------------------
parse_env_arg() {
  case "${1:-}" in
    prod|test) DEPLOY_ENV="$1" ;;
    *)
      # A site name in the env slot is the common mix-up: say the right order.
      local hint=""
      if [ -n "${1:-}" ] && known_sites | grep -qx -- "$1"; then
        hint=" \"$1\" is a site: the environment comes first: ship sites $(basename -- "$0" .sh) prod $1."
      fi
      log_error "Usage: ship sites $(basename -- "$0" .sh) prod|test ${USAGE_REST:-}.${hint}$(sites_hint)"
      ;;
  esac
}

# Site names from ship/sites.conf of the sites repo (or the current folder), for hints; works
# before resolve_sites_root, so a usage error can name them.
known_sites() {
  local conf="${SITES_ROOT:-$PWD}/ship/sites.conf"
  [ -f "$conf" ] && grep -E -v '^[[:space:]]*(#|$)' "$conf" | tr -d '\r' | awk '{print $1}'
  return 0
}

sites_hint() {
  local names
  names="$(known_sites | paste -sd ' ' -)"
  [ -n "$names" ] && printf ' Sites: %s' "$names"
  return 0
}

# The sites repo is the current directory (or SITES_ROOT). It must hold ship/sites.conf, which
# also stops the scripts from running in the wrong repo and creating ~/.secrets/<wrong name>.
resolve_sites_root() {
  SITES_ROOT="$(CDPATH='' cd -- "${SITES_ROOT:-$PWD}" && pwd)"
  SITES_CONF="${SITES_ROOT}/ship/sites.conf"
  if [ ! -f "$SITES_CONF" ]; then
    local hint="" sib
    for sib in "$(dirname "$BONES_ROOT")"/*/ship/sites.conf; do
      [ -f "$sib" ] && hint=" For example: cd \"$(cygpath -w "$(dirname "$(dirname "$sib")")" 2>/dev/null || dirname "$(dirname "$sib")")\" && bash ../$(basename "$BONES_ROOT")/ship/$(basename -- "$0") ${DEPLOY_ENV:-prod}" && break
    done
    log_error "No ship/sites.conf in ${SITES_ROOT}. Run this from the sites repo root, not from bones.${hint}"
  fi
  case "$SITES_ROOT/" in
    "$BONES_ROOT/"*) log_error "Run from the sites repo, not from bones." ;;
  esac
  SECRETS_DIR="${SHIP_SECRETS_DIR:-${HOME}/.secrets/$(basename "$SITES_ROOT")}"
  ENV_EXAMPLE="${SHIP_DIR}/env/env_vars.example"
}

# Fail if a path is inside the bones or sites repo (secrets and backups must stay outside).
assert_outside_repos() {
  local what="$1" dir="$2" real
  real="$(CDPATH='' cd -- "$dir" 2>/dev/null && pwd || printf '%s' "$dir")"
  case "$real/" in
    "$SITES_ROOT/"*|"$BONES_ROOT/"*) log_error "${what} (${real}) is inside a repository. Use a folder outside it." ;;
  esac
}

expand_tilde() {
  local p="$1"
  # shellcheck disable=SC2088 # matching a literal "~/" written in env_vars on purpose
  if [[ "$p" == '~/'* ]]; then p="$HOME/${p:2}"; fi
  printf '%s' "$p"
}

# -------------------------
# Settings (env_vars.<env>.sh)
# -------------------------
# Keys that hold a placeholder or are empty but required, honouring MAIL_RELAY=none.
unfilled_required_keys() {
  local file="$1" key value relay
  relay="$(get_var "$file" MAIL_RELAY)"
  while IFS= read -r key; do
    if [ "$relay" = "none" ] && [[ "$key" =~ ^(TEM_SMTP_USER|TEM_SMTP_PASSWORD|MAIL_FROM)$ ]]; then continue; fi
    value="$(get_var "$file" "$key")"
    if [[ "$value" =~ $PLACEHOLDER_VALUE_RE ]]; then printf '%s\n' "$key"; continue; fi
    if [ -z "$value" ] && ! [[ "$key" =~ $OPTIONAL_EMPTY_KEYS_RE ]]; then printf '%s\n' "$key"; fi
  done < <(env_keys "$file")
  return 0
}

load_env() {
  ENV_FILE="${SECRETS_DIR}/env_vars.${DEPLOY_ENV}.sh"
  [ -f "$ENV_FILE" ] || log_error "No ${ENV_FILE}. Create it with: bash ../bones/ship/init-secrets.sh ${DEPLOY_ENV}"
  assert_outside_repos "The secrets folder" "$SECRETS_DIR"
  assert_env_keys_present "$ENV_EXAMPLE" "$ENV_FILE" "env_vars.${DEPLOY_ENV}.sh"
  local left=()
  while IFS= read -r k; do left+=("$k"); done < <(unfilled_required_keys "$ENV_FILE")
  [ "${#left[@]}" -eq 0 ] || log_error "env_vars.${DEPLOY_ENV}.sh still needs: ${left[*]}. Fill them in ${ENV_FILE} (values are not shown here)."
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
  case "$MAIL_RELAY" in tem|none) ;; *) log_error "MAIL_RELAY must be tem or none" ;; esac
  [[ "$SERVER_USER" =~ ^[a-z_][a-z0-9_-]{0,30}$ ]] && [ "$SERVER_USER" != root ] \
    || log_error "SERVER_USER must be a normal user name (not root)"
  [[ "$SERVER_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || log_error "SERVER_IP must be an IPv4 address"
  # Goes into nginx as "allow <value>;": an IPv4/IPv6 address or CIDR range, or empty (admin off).
  [ -z "${ISSO_ADMIN_ALLOW_IP:-}" ] || [[ "$ISSO_ADMIN_ALLOW_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$|^[0-9A-Fa-f:]+(/[0-9]{1,3})?$ ]]     || log_error "ISSO_ADMIN_ALLOW_IP must be an IP address (or CIDR range), or empty"
  SSH_KEY="$(expand_tilde "${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519}")"
  [ -f "$SSH_KEY" ] || log_error "SSH key ${SSH_KEY} not found (SSH_KEY_PATH)"
  LOCAL_BACKUP_DIR="$(expand_tilde "${BACKUP_DIR:-$HOME/.backups/sites}")"
  KEEP_RELEASES="${KEEP_RELEASES:-3}"
}

# -------------------------
# sites.conf: "<name> <domain> <comments yes|no>" per line, # comments allowed
# -------------------------
sites_lines() { grep -E -v '^[[:space:]]*(#|$)' "$SITES_CONF" | tr -d '\r'; }

site_field() { # site_field <name> <1-based field>
  sites_lines | awk -v n="$1" -v f="$2" '$1 == n { print $f; found=1 } END { exit !found }'
}

assert_site() {
  local site="$1"
  [[ "$site" =~ ^[a-z0-9][a-z0-9-]*$ ]] || log_error "Bad site name '${site}'"
  site_field "$site" 1 >/dev/null || log_error "Site '${site}' is not in ship/sites.conf.$(sites_hint)"
}

validate_sites_conf() {
  local name domain comments rest n_comments=0
  while read -r name domain comments rest; do
    [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]] || log_error "sites.conf: bad site name '${name}'"
    [[ "$domain" =~ ^[a-z0-9.-]+\.[a-z]+$ ]] || log_error "sites.conf: bad domain '${domain}' for ${name}"
    case "$comments" in yes) n_comments=$((n_comments + 1)) ;; no) ;; *) log_error "sites.conf: comments must be yes or no for ${name}" ;; esac
    [ -z "$rest" ] || log_error "sites.conf: extra fields on the ${name} line"
  done < <(sites_lines)
  [ "$n_comments" -le 1 ] || log_error "sites.conf: only one site can have comments (one isso instance)"
}

# Public bones/ship must never contain this deployment's hostnames, IP or email.
assert_no_leaks() {
  local needle hits=()
  local needles=("$SERVER_IP" "$ADMIN_EMAIL")
  while read -r _ domain _; do needles+=("$domain"); done < <(sites_lines)
  for needle in "${needles[@]}"; do
    [ -n "$needle" ] || continue
    if grep -r -F -q -- "$needle" "$SHIP_DIR"; then hits+=("$(grep -r -F -l -- "$needle" "$SHIP_DIR" | head -3 | tr '\n' ' ')"); fi
  done
  [ "${#hits[@]}" -eq 0 ] || log_error "bones/ship is public but contains this deployment's hostname, IP or email in: ${hits[*]}"
}

# -------------------------
# SSH (retries on transport errors only; see core)
# -------------------------
SSH_RETRY_ATTEMPTS="${SSH_RETRY_ATTEMPTS:-4}"
SSH_BIN="$(type -P ssh)"
SCP_BIN="$(type -P scp)"
ssh_opts() {
  printf '%s\n' -o BatchMode=yes -o ConnectTimeout=12 -o ConnectionAttempts=1 -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 -o AddressFamily=inet -o PreferredAuthentications=publickey -i "$SSH_KEY"
}

# _retry <cmd...>: rerun only on exit 255 (ssh transport error).
_retry() {
  local attempt=1 rc
  while :; do
    rc=0; "$@" || rc=$?
    [ "$rc" -eq 255 ] && [ "$attempt" -lt "$SSH_RETRY_ATTEMPTS" ] || return "$rc"
    log_warning "SSH transport error (attempt ${attempt}/${SSH_RETRY_ATTEMPTS}), retrying..."
    sleep $((3 * attempt)); attempt=$((attempt + 1))
  done
}

# ssh_as <user> <remote command...>   (no stdin)
ssh_as() { local u="$1"; shift; local o; mapfile -t o < <(ssh_opts); _retry "$SSH_BIN" -n "${o[@]}" "${u}@${SERVER_IP}" "$@"; }
# ssh_as_in <user> <remote command...> (stdin passed through, for `ksh -s` heredocs)
ssh_as_in() { local u="$1"; shift; local o; mapfile -t o < <(ssh_opts); _retry "$SSH_BIN" "${o[@]}" "${u}@${SERVER_IP}" "$@"; }
scp_to() { # scp_to <user> <remote dir> <local files...>
  local u="$1" dest="$2"; shift 2; local o; mapfile -t o < <(ssh_opts)
  ( exec </dev/null; _retry "$SCP_BIN" -q -r "${o[@]}" "$@" "${u}@${SERVER_IP}:${dest}/" ) || log_error "Upload failed"
}

# Run a root ksh script (stdin) as SERVER_USER via doas. Abort with $1 on failure.
remote_root() {
  local what="$1"; shift
  ssh_as_in "$SERVER_USER" doas env "$@" ksh -s || log_error "${what} failed on the server (see above)"
}

# Trust the host key on first contact (Git Bash ssh has its own known_hosts quirks; see core).
ensure_known_host() {
  local u="$1" o; mapfile -t o < <(ssh_opts)
  if "$SSH_BIN" -n "${o[@]}" -o StrictHostKeyChecking=yes "${u}@${SERVER_IP}" true 2>/dev/null; then return 0; fi
  if ssh-keygen -F "$SERVER_IP" >/dev/null 2>&1; then
    return 0 # key known; the failure is about the user/key, the caller handles it
  fi
  log_info "Adding ${SERVER_IP} to ~/.ssh/known_hosts..."
  mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh" 2>/dev/null || true
  ssh-keyscan -T 10 "$SERVER_IP" >> "$HOME/.ssh/known_hosts" 2>/dev/null || log_error "ssh-keyscan ${SERVER_IP} failed"
}

can_ssh() { local o; mapfile -t o < <(ssh_opts); "$SSH_BIN" -n "${o[@]}" -o StrictHostKeyChecking=yes "${1}@${SERVER_IP}" "${2:-true}" >/dev/null 2>&1; }

# Private staging dir on the server, owned by SERVER_USER, mode 700. Sets REMOTE_STAGE.
REMOTE_STAGE=""
remote_stage_create() {
  local u="${1:-$SERVER_USER}"
  REMOTE_STAGE="$(ssh_as "$u" "umask 077; mktemp -d /tmp/ship.XXXXXXXXXX")" || log_error "Could not create a staging dir on the server"
  [[ "$REMOTE_STAGE" =~ ^/tmp/ship\.[A-Za-z0-9]+$ ]] || log_error "Unexpected staging path '${REMOTE_STAGE}'"
  STAGE_USER="$u"
}
remote_stage_cleanup() {
  if [ -n "${REMOTE_STAGE:-}" ]; then
    ssh_as "$STAGE_USER" "rm -rf '${REMOTE_STAGE}'" >/dev/null 2>&1 \
      || ssh_as "$STAGE_USER" "doas rm -rf '${REMOTE_STAGE}'" >/dev/null 2>&1 || true
    REMOTE_STAGE=""
  fi
}

# -------------------------
# bones Python (its venv when there is one)
# -------------------------
find_python() {
  local c
  for c in "$BONES_ROOT/venv/Scripts/python.exe" "$BONES_ROOT/venv/bin/python" "$(type -P python3 || true)" "$(type -P python || true)"; do
    if [ -n "$c" ] && [ -x "$c" ]; then printf '%s' "$c"; return 0; fi
  done
  log_error "No Python found (expected bones/venv)"
}

# -------------------------
# Git gate (as in core): publish only what is committed, build output included.
# ALLOW_DIRTY=1 publishes anyway and marks the release -dirty.
# -------------------------
git_dirty() { # git_dirty <repo dir> -> porcelain status lines (empty when clean)
  git -C "$1" status --porcelain --untracked-files=normal -- . || log_error "Not a git repo: $1"
}

wait_for_clean() { # wait_for_clean <repo dir>...: show changes, let the user review and commit them
  local dir status pending
  while :; do
    pending=0
    for dir in "$@"; do
      status="$(git_dirty "$dir")"
      [ -n "$status" ] || continue
      pending=1
      log_warning "$(basename "$dir") has uncommitted changes:"
      printf '%s
' "$status" | head -40
      [ "$(printf '%s
' "$status" | wc -l)" -le 40 ] || printf '  ... (git -C %s status for all)
' "$dir"
    done
    [ "$pending" = 1 ] || return 0
    if [ "${ALLOW_DIRTY:-0}" = 1 ]; then
      log_warning "Publishing uncommitted changes (ALLOW_DIRTY=1)"
      return 0
    fi
    [ -t 0 ] || log_error "Uncommitted changes and no terminal to confirm them. Commit first (or ALLOW_DIRTY=1)."
    local ans
    read -r -p "Changes OK? Commit them, then press Enter to publish [Y/n] " ans
    case "$ans" in [nN]*) log_error "Aborted; nothing was published." ;; esac
  done
}

git_rev() { # git_rev <repo dir> -> short sha (+ -dirty)
  local rev
  rev="$(git -C "$1" rev-parse --short=10 HEAD)" || log_error "Not a git repo: $1"
  [ -z "$(git_dirty "$1")" ] || rev="${rev}-dirty"
  printf '%s' "$rev"
}

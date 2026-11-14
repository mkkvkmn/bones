#!/usr/bin/env bash
# Day-to-day server commands. Reads the server from env_vars on every run.
# Same command names as core's ship/scripts/ops.sh where the meaning is the same.
#
# Usage:
#   ops <site> <prod|test> <command> [args]             (from anywhere, with the ops launcher;
#                                                        any site: all share this server)
#   bash ../bones/ship/ops.sh <prod|test> <command> [args]  (sites repo root, Git Bash)
#
# Look:
#   info                       Server, user, sites and paths (no secrets)
#   status [--test-alert]      Run the monitor now (or send a test alert by mail + heartbeat)
#   check                      Are nginx and isso running, which release each site serves, maintenance
#   logs [monitor|nginx|access|daemon|maillog] [-n LINES] [-f]
#                              Tail a log (default: monitor, 100 lines; -f follows)
#   backups                    List the encrypted backups on the server
#
# Act:
#   ssh [command...]           Shell (or one command) on the server as SERVER_USER
#   restart nginx|isso         Restart a service
#   maintenance on|off|status  Pause / resume the restart guard and auto-update
#   tls-renew                  Renew certificates now (reloads nginx if one changed)
#   patch                      Run the nightly auto-update now (may reboot after base patches)
#   import-cert <domain> <user@old-host>
#                              Copy a live certificate + key from the old server, so a site
#                              keeps HTTPS while DNS moves; then run deploy-server.sh
#
# Examples:
#   ops <site> prod check
#   ops <site> prod logs access -f
#
# Related: deploy-site.sh <env> <site> (publish a site), rollback.sh <env> <site>, backup.sh / restore.sh.
set -euo pipefail

SHIP_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"

# Help is printed from this header, so the list cannot drift from the comment.
usage() { sed -n '/^# Usage/,/^# Related/p' "$0" | sed 's/^# \{0,1\}//' >&2; exit "${1:-2}"; }
# Asked for help: no environment needed (the ship launcher shows this list).
case "${1:-}" in -h | --help | help) usage 0 ;; '') usage ;; esac
case "${2:-}" in -h | --help | help) usage 0 ;; '') usage ;; esac

# shellcheck source=lib/common.sh
. "$SHIP_DIR/lib/common.sh"

USAGE_REST="<command> (ops.sh help lists them)"
parse_env_arg "$@"
shift
CMD="${1:-}"; [ -n "$CMD" ] && shift
resolve_sites_root
load_env

root_cmd() { ssh_as "$SERVER_USER" "doas $*"; }

case "$CMD" in
  info)
    echo "environment: ${DEPLOY_ENV}"
    echo "server:      ${SERVER_USER}@${SERVER_IP} (key ${SSH_KEY})"
    echo "settings:    ${ENV_FILE}"
    echo "backups:     ${LOCAL_BACKUP_DIR} (local), /var/ship/backups (server)"
    echo "mail:        ${MAIL_RELAY}; heartbeat: $([ -n "${MONITOR_HEARTBEAT_URL:-}" ] && echo set || echo not set)"
    echo "sites:"
    sites_lines | while read -r s d c; do printf '  %-12s https://%s  comments=%s  -> /var/www/sites/%s/current\n' "$s" "$d" "$c" "$s"; done
    ;;
  status)
    if [ "${1:-}" = --test-alert ]; then root_cmd /var/ship/monitor.sh --test-alert
    else root_cmd /var/ship/monitor.sh || true; fi
    ;;
  logs)
    which="monitor"; n=100; follow=0
    while [ $# -gt 0 ]; do
      case "$1" in
        -n) n="${2:-}"; shift ;;
        -f) follow=1 ;;
        monitor|nginx|access|daemon|maillog) which="$1" ;;
        *) log_error "logs: unknown argument $1" ;;
      esac
      shift
    done
    [[ "$n" =~ ^[0-9]+$ ]] || log_error "-n needs a number"
    case "$which" in
      monitor) f=/var/log/ship/monitor.log ;;
      nginx)   f=/var/log/nginx/error.log ;;
      access)  f=/var/log/nginx/access.log ;;
      daemon)  f=/var/log/daemon ;;
      maillog) f=/var/log/maillog ;;
    esac
    if [ "$follow" = 1 ]; then
      mapfile -t o < <(ssh_opts); "$SSH_BIN" -t "${o[@]}" "${SERVER_USER}@${SERVER_IP}" "doas tail -n $n -f $f"
    else
      root_cmd tail -n "$n" "$f"
    fi
    ;;
  restart)
    case "${1:-}" in nginx|isso) root_cmd rcctl restart "$1" ;; *) log_error "restart nginx|isso" ;; esac
    ;;
  maintenance)
    case "${1:-}" in
      on)  root_cmd touch /var/ship/maintenance && log_success "Maintenance on: restart guard and auto-update paused" ;;
      off) root_cmd rm -f /var/ship/maintenance && log_success "Maintenance off" ;;
      status) root_cmd test -e /var/ship/maintenance && echo on || echo off ;;
      *) log_error "maintenance on|off|status" ;;
    esac
    ;;
  check)
    # isso only runs when a site has comments, so a stopped isso is shown, not an error.
    root_cmd rcctl check nginx || true
    root_cmd rcctl check isso || true
    sites_lines | while read -r s _; do
      printf '%-12s %s\n' "$s" "$(root_cmd readlink "/var/www/sites/${s}/current" </dev/null || echo '(not deployed)')"
    done
    printf 'maintenance: %s\n' "$(root_cmd test -e /var/ship/maintenance </dev/null && echo on || echo off)"
    ;;
  tls-renew) root_cmd /var/ship/renew-certs.sh ;;
  patch)     root_cmd /var/ship/auto-update.sh ;;
  backups)   root_cmd ls -lT /var/ship/backups ;;
  import-cert)
    domain="${1:-}"; old="${2:-}"
    [[ "$domain" =~ ^[a-z0-9.-]+\.[a-z]+$ ]] && [[ "$old" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$ ]] \
      || log_error "Usage: ops.sh ${DEPLOY_ENV} import-cert <domain> <user@old-host>"
    sites_lines | awk -v d="$domain" '$2 == d { f = 1 } END { exit !f }' || log_error "${domain} is not in ship/sites.conf"
    WORK="$(mktemp -d)"; chmod 700 "$WORK"; trap 'rm -rf "$WORK"; remote_stage_cleanup' EXIT
    mapfile -t o < <(ssh_opts)
    log_info "Reading the certificate and key from ${old} (kept in memory/temp only)..."
    "$SSH_BIN" -n "${o[@]}" "$old" "doas cat '/etc/ssl/${domain}:443.crt'" > "$WORK/crt" || log_error "Could not read the certificate on ${old}"
    "$SSH_BIN" -n "${o[@]}" "$old" "doas cat '/etc/ssl/private/${domain}:443.key'" > "$WORK/key" || log_error "Could not read the key on ${old}"
    openssl x509 -noout -checkend 86400 -in "$WORK/crt" >/dev/null || log_error "That certificate expires within a day"
    [ "$(openssl x509 -noout -pubkey -in "$WORK/crt" | openssl sha256)" = "$(openssl pkey -pubout -in "$WORK/key" | openssl sha256)" ] \
      || log_error "Certificate and key do not match"
    remote_stage_create
    scp_to "$SERVER_USER" "$REMOTE_STAGE" "$WORK/crt" "$WORK/key"
    remote_root "Installing the certificate" STAGE="$REMOTE_STAGE" DOMAIN="$domain" <<'EOF'
set -e
install -o root -g wheel -m 444 "$STAGE/crt" "/etc/ssl/$DOMAIN:443.crt"
install -o root -g wheel -m 400 "$STAGE/key" "/etc/ssl/private/$DOMAIN:443.key"
EOF
    remote_stage_cleanup
    log_success "Installed. Run deploy-server.sh to switch ${domain} to HTTPS. acme-client replaces it with a fresh certificate once DNS points here."
    ;;
  ssh)
    mapfile -t o < <(ssh_opts)
    if [ $# -gt 0 ]; then "$SSH_BIN" "${o[@]}" "${SERVER_USER}@${SERVER_IP}" "$@"
    else "$SSH_BIN" -t "${o[@]}" "${SERVER_USER}@${SERVER_IP}"; fi
    ;;
  *) log_error "Unknown command: ${CMD}. Run: ops <site>" ;;
esac

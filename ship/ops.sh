#!/usr/bin/env bash
# Day-to-day server commands. Reads the server from env_vars on every run.
#
# Usage:
#   bash ../bones/ship/ops.sh <prod|test> <command> [args]  (from the folder that holds your sites, Git Bash)
#
# Look:
#   info                       Server, user, sites and paths (no secrets)
#   status [--test-alert]      Run the monitor now (or send a test alert by mail + heartbeat)
#   check                      Are nginx and isso running, which release each site serves, maintenance
#   logs [monitor|nginx|access|daemon|maillog] [-n LINES] [-f]
#                              Tail a log (default: monitor, 100 lines; -f follows)
#   backups                    List the encrypted backups on the server
#   stats [DAYS] [--check] [--no-pull]
#                              Visitor dashboard of the site (default 30 days): pulls the new
#                              logs (no IP addresses), then readers, pages, referrers, downloads, errors.
#                              --check shows how requests were counted (people, bots, scans)
#   errors [DAYS]              5xx, broken links, 404s, nginx error.log, monitor (default 7 days)
#
# Act:
#   ssh [command...]           Shell (or one command) on the server as SERVER_USER
#   restart nginx|isso         Restart a service
#   maintenance on|off|status  Pause / resume the restart guard and auto-update
#   tls-renew                  Renew certificates now (reloads nginx if one changed)
#   patch                      Run the nightly auto-update now (may reboot after base patches)
#   import-cert <domain> <user@from-host>
#                              Copy a live certificate + key from the server a site moves from, so it
#                              keeps HTTPS while DNS moves; then run deploy-server.sh
#
# Examples:
#   bash ../bones/ship/ops.sh prod check
#   bash ../bones/ship/ops.sh prod logs access -f
#   SHIP_SITE=<site> bash ../bones/ship/ops.sh prod stats 90
#
# stats and errors act on the site you name (set SHIP_SITE=<site>). Data stays
# on this machine in STATS_DIR (env_vars, optional; default ~/.stats/<sites folder>).
#
# Related: deploy-site.sh <env> <site> (publish a site), rollback.sh <env> <site>, backup.sh / restore.sh.
set -euo pipefail

SHIP_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"

# Help is printed from this header, so the list cannot drift from the comment.
usage() { sed -n '/^# Usage/,/^# Related/p' "$0" | sed 's/^# \{0,1\}//' >&2; exit "${1:-2}"; }
# Asked for help: no environment needed.
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

# -------------------------
# stats and errors
# -------------------------
stats_site() {
  STATS_SITE="${SHIP_SITE:-}"
  [ -n "$STATS_SITE" ] || log_error "${CMD} needs the site: SHIP_SITE=<site> bash ../bones/ship/ops.sh ${DEPLOY_ENV} ${CMD}"
  assert_site "$STATS_SITE"
  STATS_DIR="$(expand_tilde "${STATS_DIR:-$HOME/.stats/$(basename "$SITES_ROOT")}")"
  assert_outside_repos "STATS_DIR" "$STATS_DIR"
  RAW_DIR="$STATS_DIR/raw/$DEPLOY_ENV"
  mkdir -p "$RAW_DIR"
  PYTHON=""
  local p
  for p in python3 python; do
    if command -v "$p" >/dev/null && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then PYTHON="$p"; break; fi
  done
  [ -n "$PYTHON" ] || log_error "${CMD} needs Python 3.8 or newer on this machine"
}

# Ctrl-C while pulling: an async job ignores it in a script, so stop the ssh job and its ssh
# child (Git Bash has no pkill) and drop the partial tar.
stats_pull_abort() { # stats_pull_abort <job pid> <1 if the counter line is showing>
  local c
  for c in $(ps -ef | awk -v p="$1" '$3 == p { print $2 }'); do kill "$c" 2>/dev/null || true; done
  kill "$1" 2>/dev/null || true
  rm -f "$RAW_DIR/.pull.tar"
  [ "$2" = 1 ] && printf '\n'
  exit 130
}

# Copy the new nginx and monitor logs into RAW_DIR. Rotated files are fetched once (named by
# their time and size); the current ones every time. Client addresses are removed on
# the server, so they never reach this machine.
# Progress: the server prints one line per file to stderr while it builds the tar. The tar is sent
# only when it is complete, so the first byte received marks the end of the server work. This side
# shows the bytes received (one updating line on a terminal, a plain line every 5 s otherwise).
stats_pull() {
  local have pid rc=0 size kb t0 t_first=-1 t_last=0 live=0 shown=0
  have="$(cd "$RAW_DIR" && ls 2>/dev/null | paste -sd, -)"
  [ -t 1 ] && live=1
  t0=$SECONDS
  log_info "Pulling new logs into ${RAW_DIR}..."
  ssh_as_in "$SERVER_USER" doas env "HAVE=,${have}," ksh -s > "$RAW_DIR/.pull.tar" <<'EOF' &
set -e
T=$(mktemp -d /tmp/stats.XXXXXXXX)
trap 'rm -rf "$T"' EXIT
noip() { sed -E 's/^[^ ]+ /- /'; }
# error.log: no client address, no referrer, no query string in the request or upstream URL
noclient() {
  sed -E -e 's/, client: [^,]+/, client: -/' -e 's/, referrer: "[^"]*"//' \
    -e 's/(request: "[A-Z]+ [^ ?"]*)\?[^ "]*/\1/' -e 's/(upstream: "[^?"]*)\?[^"]*/\1/'
}
cd /var/log/nginx
# Rotated files not on the other side yet: "<file> <name there>" per line.
for f in stats.log.*.gz access.log.*.gz error.log.*.gz; do
  [ -e "$f" ] || continue
  k="${f%%.log.*}-$(stat -f '%m-%z' "$f").log.gz"
  case "$HAVE" in *",$k,"*) continue ;; esac
  echo "$f $k"
done > "$T/.todo"
# Files to send: the new rotated ones and the three current logs.
total=$(( $(wc -l < "$T/.todo") + 3 ))
n=0
step() { n=$((n + 1)); echo "  $n/$total $1 (${SECONDS}s)" >&2; }
while read -r f k; do
  step "$k"
  case "$f" in
    stats.*) cp "$f" "$T/$k" ;;
    access.*) gzip -dc "$f" | noip | gzip > "$T/$k" ;;
    error.*) gzip -dc "$f" | noclient | gzip > "$T/$k" ;;
  esac
done < "$T/.todo"
rm -f "$T/.todo"
# The combined log is needed only until the stats log exists (stats.py drops the overlap).
if [ -e stats.log ]; then step stats-current.log.gz; gzip -c stats.log > "$T/stats-current.log.gz"
else step access-current.log.gz; noip < access.log | gzip > "$T/access-current.log.gz"; fi
step error-current.log.gz; noclient < error.log | gzip > "$T/error-current.log.gz"
step monitor-current.log.gz; gzip -c /var/log/ship/monitor.log > "$T/monitor-current.log.gz"
echo "  ${total} files ready after ${SECONDS}s, sending" >&2
cd "$T" && tar cf - .
EOF
  pid=$!
  # An async job ignores Ctrl-C in a script: stop the ssh job (and its ssh child) and drop the partial tar.
  trap 'stats_pull_abort "$pid" "$shown"' INT TERM
  while kill -0 "$pid" 2>/dev/null; do
    size="$(stat -c %s "$RAW_DIR/.pull.tar" 2>/dev/null || echo 0)"
    [ "$size" -gt 0 ] && [ "$t_first" -lt 0 ] && t_first=$((SECONDS - t0))
    kb=$((size / 1024))
    if [ "$size" -eq 0 ]; then :  # server still working: its progress lines are on screen
    elif [ "$live" = 1 ]; then
      printf '\r  received %s KB (%ss)   ' "$kb" "$((SECONDS - t0))"; shown=1
    elif [ $((SECONDS - t_last)) -ge 5 ]; then
      echo "  received ${kb} KB ($((SECONDS - t0))s)"; t_last=$SECONDS
    fi
    sleep 1
  done
  wait "$pid" || rc=$?
  trap - INT TERM
  [ "$shown" = 1 ] && printf '\n'
  [ "$rc" -eq 0 ] || { rm -f "$RAW_DIR/.pull.tar"; log_error "Pulling the logs failed"; }
  size="$(stat -c %s "$RAW_DIR/.pull.tar")"
  # The tar is sent after the server work, so the time to the first byte is the server work.
  log_success "Received $((size / 1024)) KB in $((SECONDS - t0))s (server work and connection $([ "$t_first" -ge 0 ] && echo "$t_first" || echo "$((SECONDS - t0))")s)"
  t0=$SECONDS
  (cd "$RAW_DIR" && tar xf .pull.tar && rm -f .pull.tar) || log_error "Could not unpack the pulled logs in ${RAW_DIR}"
  log_success "Unpacked in $((SECONDS - t0))s"
}

stats_run() { # stats_run <dashboard|check|errors> <days>
  local domain comments others=() s
  domain="$(site_field "$STATS_SITE" 2)"
  comments="$(site_field "$STATS_SITE" 3)"
  while read -r s _; do
    [ "$s" = "$STATS_SITE" ] || others+=(--other-build "$(cygpath -w "$SITES_ROOT/$s/z-public/prod" 2>/dev/null || echo "$SITES_ROOT/$s/z-public/prod")")
  done < <(sites_lines)
  local build="$SITES_ROOT/$STATS_SITE/z-public/prod"
  [ -d "$build" ] || log_warning "No build in ${build}: page titles and page checks are missing (bones ${STATS_SITE} prod)"
  PYTHONUTF8=1 "$PYTHON" "$SHIP_DIR/stats.py" "$1" "$STATS_DIR" --site "$STATS_SITE" --domain "$domain" \
    $([ "$comments" = yes ] && echo "--comments-host comments.$domain") \
    --build "$(cygpath -w "$build" 2>/dev/null || echo "$build")" "${others[@]}" --days "$2"
}

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
  stats|errors)
    days=""; view=dashboard; pull=1
    [ "$CMD" = errors ] && view=errors
    while [ $# -gt 0 ]; do
      case "$1" in
        --check) view=check ;;
        --no-pull) pull=0 ;;
        *) [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -gt 0 ] || log_error "${CMD}: unknown argument $1 (${CMD} [DAYS]$([ "$CMD" = stats ] && echo ' [--check] [--no-pull]'))"
           days="$1" ;;
      esac
      shift
    done
    [ -n "$days" ] || { [ "$CMD" = errors ] && days=7 || days=30; }
    stats_site
    [ "$pull" = 1 ] && stats_pull
    t0=$SECONDS
    stats_run "$view" "$days"
    log_success "Analysis took $((SECONDS - t0))s"
    ;;
  tls-renew) root_cmd /var/ship/renew-certs.sh ;;
  patch)     root_cmd /var/ship/auto-update.sh ;;
  backups)   root_cmd ls -lT /var/ship/backups ;;
  import-cert)
    domain="${1:-}"; from="${2:-}"
    [[ "$domain" =~ ^[a-z0-9.-]+\.[a-z]+$ ]] && [[ "$from" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$ ]] \
      || log_error "Usage: ops.sh ${DEPLOY_ENV} import-cert <domain> <user@from-host>"
    sites_lines | awk -v d="$domain" '$2 == d { f = 1 } END { exit !f }' || log_error "${domain} is not in ship/sites.conf"
    WORK="$(mktemp -d)"; chmod 700 "$WORK"; trap 'rm -rf "$WORK"; remote_stage_cleanup' EXIT
    mapfile -t o < <(ssh_opts)
    log_info "Reading the certificate and key from ${from} (kept in memory/temp only)..."
    "$SSH_BIN" -n "${o[@]}" "$from" "doas cat '/etc/ssl/${domain}:443.crt'" > "$WORK/crt" || log_error "Could not read the certificate on ${from}"
    "$SSH_BIN" -n "${o[@]}" "$from" "doas cat '/etc/ssl/private/${domain}:443.key'" > "$WORK/key" || log_error "Could not read the key on ${from}"
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
  *) log_error "Unknown command: ${CMD}. Run: bash ../bones/ship/ops.sh help" ;;
esac

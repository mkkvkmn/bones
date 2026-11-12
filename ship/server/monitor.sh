#!/bin/ksh
# Server health monitor for bones/ship (root cron). Adapted from core/ship/scripts/server/monitor.sh.
#
#   monitor.sh               run all checks (cron, every 15 min); exit 0 ok, 1 errors, 2 warnings
#   monitor.sh --guard       restart guard (cron, every minute): start nginx/isso if they died,
#                            unless /var/ship/maintenance exists
#   monitor.sh --test-alert  send a test alert by mail and to the heartbeat, then exit
#
# Mails ADMIN_EMAIL when the state changes (OK <-> FAILING) and every ALERT_REPEAT_MINUTES while
# failing; pings MONITOR_HEARTBEAT_URL (healthchecks.io) on every run, /fail with the errors.

PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
export PATH
ROOT=/var/ship
[ -f "$ROOT/env_vars.sh" ] || { echo "ERROR: $ROOT/env_vars.sh missing (re-run deploy-server.sh)"; exit 1; }
. "$ROOT/env_vars.sh"
SITES="$ROOT/sites.conf"
HAS_COMMENTS=0
awk '$3 == "yes" { f = 1 } END { exit !f }' "$SITES" && HAS_COMMENTS=1

ALERT_REPEAT_MINUTES="${ALERT_REPEAT_MINUTES:-360}"
BACKUP_MAX_AGE_MINUTES="${BACKUP_MAX_AGE_MINUTES:-1560}"
STATE_FILE=/var/db/ship-monitor.state
UPDATE_STATUS=/var/db/ship-auto-update.status
HOST="$(hostname)"
ERRORS=""
WARNINGS=""

ts()   { date '+%Y-%m-%d %H:%M:%S'; }
ok()   { echo "$(ts) OK    $1"; }
warn() { echo "$(ts) WARN  $1"; WARNINGS="${WARNINGS}- $1
"; }
fail() { echo "$(ts) ERROR $1"; ERRORS="${ERRORS}- $1
"; }

services() { echo nginx; [ "$HAS_COMMENTS" = 1 ] && echo isso; }

if [ "${1:-}" = "--guard" ]; then
    [ -e "$ROOT/maintenance" ] && exit 0
    for _svc in $(services); do
        if ! rcctl check "$_svc" >/dev/null 2>&1; then
            echo "$(ts) guard: $_svc was down, starting it"
            rcctl start "$_svc" >/dev/null 2>&1 || echo "$(ts) guard: $_svc did not start"
        fi
    done
    exit 0
fi

send_mail() {
    if [ "${MAIL_RELAY:-none}" != tem ]; then
        echo "$(ts) mail is off (MAIL_RELAY=none): $1"
        return 0
    fi
    if ! printf 'From: %s\nTo: %s\nSubject: %s\n\n%s\n' "$MAIL_FROM" "$ADMIN_EMAIL" "$1" "$2" \
        | sendmail -t -f "$MAIL_FROM"; then
        echo "$(ts) ERROR could not hand the alert to sendmail"
    fi
}

# The URL is never printed. A failed ping only logs: the service alerts on missing pings.
ping_heartbeat() {
    [ -n "${MONITOR_HEARTBEAT_URL:-}" ] || return 0
    printf '%s\n' "$2" | curl -fsS -o /dev/null -m 10 --retry 3 --data-binary @- "${MONITOR_HEARTBEAT_URL}$1" \
        || echo "$(ts) ERROR could not reach the heartbeat service"
}

if [ "${1:-}" = "--test-alert" ]; then
    send_mail "[sites] test alert from ${HOST}" "Test alert sent at $(ts). If you can read this, monitor alerts reach you."
    ping_heartbeat "/fail" "Test alert from ${HOST} at $(ts). The next clean monitor run marks the check up again."
    echo "Test alert sent (mail: ${MAIL_RELAY:-none}; heartbeat: $([ -n "${MONITOR_HEARTBEAT_URL:-}" ] && echo yes || echo not set))."
    echo "Mail log: /var/log/maillog"
    exit 0
fi

has_cert() { [ -s "/etc/ssl/$1:443.crt" ]; }

# http_code <url> [curl args...]
http_code() { _u="$1"; shift; curl -s -o /dev/null -w '%{http_code}' -m 10 "$@" "$_u"; }

check_services() {
    for _svc in $(services); do
        if rcctl check "$_svc" >/dev/null 2>&1; then ok "service $_svc running"; else fail "service $_svc is not running"; fi
    done
}

# Each site through nginx with its real host name, the way visitors reach it.
check_sites() {
    while read -r _site _domain _comments; do
        if has_cert "$_domain"; then
            _c=$(http_code "https://$_domain/" --resolve "$_domain:443:127.0.0.1")
            [ "$_c" = 200 ] && ok "https://$_domain/" || fail "https://$_domain/ returned HTTP ${_c:-none}"
            _c=$(http_code "https://www.$_domain/" --resolve "www.$_domain:443:127.0.0.1")
            [ "$_c" = 301 ] && ok "www.$_domain redirects" || warn "https://www.$_domain/ returned HTTP ${_c:-none} (expected 301)"
            if [ "$_comments" = yes ]; then
                _c=$(http_code "https://comments.$_domain/js/embed.min.js" --resolve "comments.$_domain:443:127.0.0.1")
                [ "$_c" = 200 ] && ok "comments.$_domain" || fail "https://comments.$_domain/js/embed.min.js returned HTTP ${_c:-none}"
            fi
        else
            _c=$(http_code "http://127.0.0.1/" -H "Host: $_domain")
            [ "$_c" = 200 ] && ok "http://$_domain/ (no certificate yet)" || fail "http://$_domain/ returned HTTP ${_c:-none}"
            warn "$_domain has no TLS certificate yet"
        fi
    done < "$SITES"
}

check_certificates() {
    while read -r _site _domain _comments; do
        has_cert "$_domain" || continue
        _crt="/etc/ssl/$_domain:443.crt"
        if ! openssl x509 -checkend $((7 * 86400)) -noout -in "$_crt" >/dev/null; then
            fail "TLS certificate for $_domain expires within 7 days (renewal broken?)"
        elif ! openssl x509 -checkend $((20 * 86400)) -noout -in "$_crt" >/dev/null; then
            warn "TLS certificate for $_domain expires within 20 days"
        else
            ok "TLS certificate for $_domain valid for more than 20 days"
        fi
    done < "$SITES"
}

check_disk() {
    for _mnt in / /var /usr/local; do
        _pct=$(df -k "$_mnt" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5}')
        [ -n "$_pct" ] || continue
        if [ "$_pct" -gt 90 ]; then fail "disk $_mnt at ${_pct}%"
        elif [ "$_pct" -gt 80 ]; then warn "disk $_mnt at ${_pct}%"
        else ok "disk $_mnt at ${_pct}%"; fi
    done
}

# Free RAM means little on OpenBSD (buffer cache); sustained swap use does.
check_swap() {
    _line=$(swapctl -sk 2>/dev/null | tail -1)
    _used=$(printf '%s' "$_line" | awk '{print $(NF-3)}')
    _total=$(printf '%s' "$_line" | awk '{print $2}')
    if [ -z "$_total" ] || [ "$_total" -eq 0 ] 2>/dev/null; then ok "swap not configured"; return; fi
    _pct=$(( _used * 100 / _total ))
    [ "$_pct" -gt 50 ] && warn "swap ${_pct}% used (memory pressure)" || ok "swap ${_pct}% used"
}

check_backup_freshness() {
    [ "$HAS_COMMENTS" = 1 ] || return 0
    [ -f /var/isso/db/comments.db ] || { ok "no comment database yet"; return; }
    _recent=$(find "$ROOT/backups" -name 'comments-*.db.age' -mmin "-${BACKUP_MAX_AGE_MINUTES}" 2>/dev/null | head -1)
    [ -n "$_recent" ] && ok "comment backup fresh" || fail "no comment backup in the last $((BACKUP_MAX_AGE_MINUTES / 60)) hours"
}

# auto-update.sh writes "<epoch> ok|failed|skipped <message>".
check_auto_update() {
    if [ ! -f "$UPDATE_STATUS" ]; then warn "auto-update has not run yet (ops.sh <env> patch runs it now)"; return; fi
    read -r _when _result _msg < "$UPDATE_STATUS"
    _age=$(( $(date +%s) - ${_when:-0} ))
    if [ "$_result" = failed ]; then fail "auto-update failed: $_msg"
    elif [ "$_age" -gt $((3 * 86400)) ]; then fail "auto-update has not run for $((_age / 86400)) days"
    else ok "auto-update $_result"; fi
}

notify() {
    _state=OK
    [ -n "$ERRORS" ] && _state=FAILING
    _prev_state=""; _prev_sent=0
    [ -f "$STATE_FILE" ] && read -r _prev_state _prev_sent < "$STATE_FILE"
    _now=$(date +%s); _send=0
    if [ "$_state" != "${_prev_state:-OK}" ]; then _send=1
    elif [ "$_state" = FAILING ] && [ $(( _now - ${_prev_sent:-0} )) -ge $(( ALERT_REPEAT_MINUTES * 60 )) ]; then _send=1; fi
    if [ "$_send" = 1 ]; then
        if [ "$_state" = FAILING ]; then
            send_mail "[sites] FAILING on ${HOST}" "Errors:
${ERRORS}
Warnings:
${WARNINGS:-none}
Checked at $(ts). Log: /var/log/ship/monitor.log"
        else
            send_mail "[sites] recovered on ${HOST}" "All checks passed at $(ts).
Warnings:
${WARNINGS:-none}"
        fi
        _prev_sent=$_now
    fi
    printf '%s %s\n' "$_state" "${_prev_sent:-0}" > "$STATE_FILE"
}

echo "$(ts) ---- monitor ----"
check_services
check_sites
check_certificates
check_disk
check_swap
check_backup_freshness
check_auto_update
notify

if [ -n "$ERRORS" ]; then
    ping_heartbeat "/fail" "[sites] FAILING on ${HOST}
Errors:
${ERRORS}
Warnings:
${WARNINGS:-none}"
    exit 1
fi
ping_heartbeat "" "OK on ${HOST} at $(ts)
Warnings:
${WARNINGS:-none}"
[ -n "$WARNINGS" ] && exit 2
exit 0

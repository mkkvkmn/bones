#!/bin/ksh
# Nightly OpenBSD updates within the installed release (root cron 04:30; `ops.sh <env> patch`
# runs it on demand). Trimmed from core/ship/scripts/server/auto-update.sh.
#
#   1. syspatch: base security patches. Anything installed means a reboot (1 minute later).
#   2. pkg_add -u: -stable package fixes. nginx is restarted if it changed; isso's venv is
#      rebuilt if python changed, and isso restarted if any python package changed.
#   3. fw_update: firmware, used at the next boot.
# Never runs sysupgrade: moving to the next OpenBSD release stays a manual step.
# Writes "<epoch> ok|failed|skipped <message>" to STATUS_FILE; monitor.sh alerts on a failure
# or when no run happened for 3 days. Skips while /var/ship/maintenance exists.

PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
export PATH
STATUS_FILE=/var/db/ship-auto-update.status
LOCK_DIR=/var/run/ship-auto-update.lock
ROOT=/var/ship

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) auto-update: $1"; }
status() { printf '%s %s %s\n' "$(date +%s)" "$1" "$2" > "$STATUS_FILE"; }
fail_run() { log "FAILED: $1"; status failed "$1"; exit 1; }

mkdir "$LOCK_DIR" 2>/dev/null || { log "another run holds the lock; skipping"; exit 0; }
trap 'rmdir "$LOCK_DIR"' EXIT

if [ -e "$ROOT/maintenance" ]; then
    log "maintenance mode; skipping"
    status skipped "maintenance mode"
    exit 0
fi

# 1. Base patches. syspatch can update itself first and then needs a second run.
_reboot=0
for _i in 1 2; do
    _avail="$(syspatch -c 2>&1)" || fail_run "syspatch -c: $(echo "$_avail" | tail -1)"
    [ -n "$_avail" ] || break
    log "installing base patches: $(echo $_avail)"
    syspatch || fail_run "syspatch failed"
    _reboot=1
done

# 2. Packages
_work="$(mktemp -d /tmp/ship-au.XXXXXXXX)" || fail_run "mktemp failed"
trap 'rmdir "$LOCK_DIR"; rm -rf "$_work"' EXIT
pkg_info -q | sort > "$_work/before"
pkg_add -u -I >/dev/null 2>&1 || fail_run "pkg_add -u failed"
pkg_info -q | sort > "$_work/after"
# Package names (with versions) that are new after the update.
_updated="$(comm -13 "$_work/before" "$_work/after")"
if [ -n "$_updated" ]; then
    log "packages updated: $(echo $_updated)"
    if echo "$_updated" | grep -q '^nginx-'; then
        rcctl restart nginx >/dev/null || fail_run "nginx restart after update failed"
    fi
    if [ -f /etc/rc.d/isso ] && echo "$_updated" | grep -qE '^(python-|py3-)'; then
        _py="$(command -v python3 || ls /usr/local/bin/python3.[0-9]* 2>/dev/null | sort -t. -k2 -n | tail -1)"
        _reqs="$ROOT/isso-requirements.txt"
        _want="$($_py -V 2>&1) reqs:$(cksum < "$_reqs" | cut -d' ' -f1)"
        if [ "$(cat /var/isso/venv/.ship-version 2>/dev/null)" != "$_want" ]; then
            log "python changed; rebuilding the isso venv"
            rm -rf /var/isso/venv
            "$_py" -m venv --system-site-packages /var/isso/venv \
                && /var/isso/venv/bin/pip install -q --disable-pip-version-check -r "$_reqs" \
                || fail_run "isso venv rebuild failed"
            echo "$_want" > /var/isso/venv/.ship-version
            chown -R root:_isso /var/isso/venv; chmod -R g+rX,o-rwx /var/isso/venv
        fi
        rcctl restart isso >/dev/null || fail_run "isso restart after update failed"
    fi
else
    log "packages up to date"
fi

# 3. Firmware
fw_update >/dev/null 2>&1 || log "fw_update failed (not fatal)"

if [ "$_reboot" = 1 ]; then
    status ok "base patches installed; rebooting"
    log "rebooting in 1 minute to load the patched system"
    shutdown -r +1 "auto-update: base patches installed" >/dev/null 2>&1 &
    exit 0
fi
status ok "up to date"
log "done"
exit 0

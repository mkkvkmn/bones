#!/bin/ksh
# OpenBSD web server converge for bones/ship (static bones sites + optional isso comments).
#
# The server half of deploy-server.sh: never run it by hand. deploy-server.sh uploads it with
# config/, the server scripts, the sites config and env_vars.sh into a private (700) temp dir,
# runs it as root, and deletes the dir.
#
# Idempotent: every file is rendered to a temp copy and only replaced when it differs; daemons
# are reloaded only when their config changed; users, packages, cron and newsyslog entries are
# added once. The last lines list what changed. Running it twice in a row changes nothing.
#
# Environment from deploy-server.sh:
#   LOCK_ROOT=1     turn root SSH off (only after SERVER_USER login + doas were proven)
set -u

STAGE="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$STAGE/config"
PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
export PATH

SITES_BASE=/var/www/sites
SHIP_HOME=/var/ship
SHIP_LOG=/var/log/ship
NGX_DIR=/etc/nginx/ship

CHANGED=""
UNCHANGED=""
WARNINGS=""

info() { echo "[setup] $*"; }
warn() { echo "[setup] WARNING: $*"; WARNINGS="${WARNINGS}
  - $*"; }
die()  { echo "[setup] ERROR: $*" >&2; exit 1; }
changed()   { CHANGED="$CHANGED $1"; }
unchanged() { UNCHANGED="$UNCHANGED $1"; }

[ "$(uname -s)" = OpenBSD ] || die "This runs on the OpenBSD server. Use: bash ../bones/ship/deploy-server.sh prod"
[ "$(id -u)" = 0 ] || die "Must run as root (deploy-server.sh runs it through doas)"
for _f in env_vars.sh sites/sites.conf config/nginx.conf server/monitor.sh; do
    [ -f "$STAGE/$_f" ] || die "Missing $STAGE/$_f (run deploy-server.sh, not this script)"
done

set -a
. "$STAGE/env_vars.sh"
set +a
LOCK_ROOT="${LOCK_ROOT:-0}"
ISSO_REQS="$CONFIG/isso-requirements.txt"
[ -n "${SERVER_USER:-}" ] && [ "$SERVER_USER" != root ] || die "SERVER_USER must be a normal user"

TMP="$(mktemp -d /tmp/ship-setup.XXXXXXXXXX)" || die "mktemp failed"
trap 'rm -rf "$TMP"' EXIT

# sites.conf lines: "<site> <domain> <yes|no>"
SITES_LIST="$TMP/sites"
grep -E -v '^[[:space:]]*(#|$)' "$STAGE/sites/sites.conf" | tr -d '\r' > "$SITES_LIST"
COMMENTS_SITE="$(awk '$3 == "yes" { print $1; exit }' "$SITES_LIST")"
COMMENTS_DOMAIN="$(awk '$3 == "yes" { print $2; exit }' "$SITES_LIST")"

# -------------------------
# Helpers
# -------------------------
# install_if_changed <src> <dest> <owner:group> <mode> <label>   -> 0 when the file changed
install_if_changed() {
    if [ -f "$2" ] && cmp -s "$1" "$2"; then
        chown "$3" "$2"; chmod "$4" "$2"
        unchanged "$5"
        return 1
    fi
    install -o "${3%%:*}" -g "${3##*:}" -m "$4" "$1" "$2" || die "install $2 failed"
    changed "$5"
    return 0
}

# render <template>: replace {{NAME}} with the exported variable NAME (empty when unset).
render() {
    awk '{
        out = ""; rest = $0
        while (match(rest, /\{\{[A-Z_]+\}\}/)) {
            key = substr(rest, RSTART + 2, RLENGTH - 4)
            out = out substr(rest, 1, RSTART - 1) ENVIRON[key]
            rest = substr(rest, RSTART + RLENGTH)
        }
        print out rest
    }' "$1"
}

has_cert() { [ -s "/etc/ssl/$1:443.crt" ] && [ -s "/etc/ssl/private/$1:443.key" ]; }

# Replace the block between "# BEGIN ship" and "# END ship" in a text stream with file $2.
replace_block() { # replace_block <current file> <block file> <comment char>
    awk -v blockfile="$2" -v c="$3" '
        $0 == c " BEGIN ship" { skip = 1; next }
        skip && $0 == c " END ship" { skip = 0; next }
        skip { next }
        { print }
        END {
            print c " BEGIN ship"
            while ((getline line < blockfile) > 0) print line
            print c " END ship"
        }' "$1"
}

# -------------------------
# Packages
# -------------------------
setup_packages() {
    _missing=""
    for _p in nginx curl sqlite3 age; do
        pkg_info -q -e "${_p}-*" || _missing="$_missing $_p"
    done
    if [ -n "$COMMENTS_SITE" ]; then
        ls /usr/local/bin/python3.[0-9]* >/dev/null 2>&1 || _missing="$_missing python%3"
        pkg_info -q -e "py3-cffi-*" || _missing="$_missing py3-cffi"
    fi
    if [ -n "$_missing" ]; then
        info "Installing packages:$_missing"
        pkg_add -I $_missing || die "pkg_add failed"
        changed "packages"
    else
        unchanged "packages"
    fi
}

# -------------------------
# Users, doas, sshd
# -------------------------
setup_users() {
    if ! id "$SERVER_USER" >/dev/null 2>&1; then
        useradd -m -G wheel -s /bin/ksh -L staff "$SERVER_USER" || die "useradd $SERVER_USER failed"
        changed "user:$SERVER_USER"
    else
        unchanged "user:$SERVER_USER"
    fi
    _home="$(awk -F: -v u="$SERVER_USER" '$1 == u { print $6 }' /etc/passwd)"
    _ak="$_home/.ssh/authorized_keys"
    if [ ! -s "$_ak" ]; then
        [ -s /root/.ssh/authorized_keys ] || die "No SSH key for $SERVER_USER and none in /root/.ssh to copy"
        install -d -o "$SERVER_USER" -g "$SERVER_USER" -m 700 "$_home/.ssh"
        install -o "$SERVER_USER" -g "$SERVER_USER" -m 600 /root/.ssh/authorized_keys "$_ak"
        changed "ssh-key:$SERVER_USER"
    fi

    if [ -n "$COMMENTS_SITE" ] && ! id _isso >/dev/null 2>&1; then
        install -d -o root -g wheel -m 750 /var/isso
        useradd -d /var/isso -s /sbin/nologin -L daemon -c "isso comments" _isso || die "useradd _isso failed"
        changed "user:_isso"
    fi

    printf 'permit nopass %s\n' "$SERVER_USER" > "$TMP/doas.conf"
    doas -C "$TMP/doas.conf" >/dev/null 2>&1 || die "Generated doas.conf does not parse"
    install_if_changed "$TMP/doas.conf" /etc/doas.conf root:wheel 600 "doas.conf" || true
}

# Core's sshd renderer (core/ship/scripts/server/setup.sh): managed settings in a marked block at
# the top (sshd uses the first value it reads); other global lines for those keywords commented out.
render_sshd_config() {
    _current="$1"
    _managed="$2"
    awk -v managed="$_managed" '
        function keyword(s) { s = tolower(s); sub(/=.*/, "", s); return s }
        BEGIN {
            print "# BEGIN ship managed (config/sshd_config). sshd uses the first value it reads, so these win."
            while ((getline line < managed) > 0) {
                if (line ~ /^[ \t]*(#|$)/) continue
                print line
                split(line, f, /[ \t=]+/)
                want[keyword(f[1])] = 1
            }
            print "# END ship managed"
        }
        /^# BEGIN ship managed/ { skip = 1; next }
        skip { if ($0 ~ /^# END ship managed/) skip = 0; next }
        keyword($1) == "match" { in_match = 1 }
        !in_match && $1 !~ /^#/ && (keyword($1) in want) { print "# ship: " $0; next }
        { print }
    ' "$_current"
}

setup_sshd() {
    if [ "$LOCK_ROOT" = 1 ]; then
        export SSH_PERMIT_ROOT=no SSH_ALLOW_USERS="$SERVER_USER"
    else
        export SSH_PERMIT_ROOT=prohibit-password SSH_ALLOW_USERS="$SERVER_USER root"
    fi
    render "$CONFIG/sshd_config" > "$TMP/sshd_managed"
    [ -f /etc/ssh/sshd_config.orig ] || cp -p /etc/ssh/sshd_config /etc/ssh/sshd_config.orig
    render_sshd_config /etc/ssh/sshd_config "$TMP/sshd_managed" > "$TMP/sshd_config"
    sshd -t -f "$TMP/sshd_config" || die "New sshd_config fails sshd -t; live config unchanged"
    if install_if_changed "$TMP/sshd_config" /etc/ssh/sshd_config root:wheel 644 "sshd_config"; then
        rcctl reload sshd >/dev/null || warn "sshd reload failed"
    fi
}

# -------------------------
# Firewall
# -------------------------
setup_pf() {
    pfctl -nf "$CONFIG/pf.conf" || die "pf.conf does not parse"
    if install_if_changed "$CONFIG/pf.conf" /etc/pf.conf root:wheel 600 "pf.conf"; then
        pfctl -f /etc/pf.conf || die "pfctl -f failed"
    fi
    pfctl -e >/dev/null 2>&1 || true
}

# -------------------------
# Directories
# -------------------------
setup_dirs() {
    install -d -o root -g wheel -m 755 "$SITES_BASE" /var/www/acme /var/log/nginx /etc/nginx "$NGX_DIR"
    install -d -o root -g wheel -m 700 "$SHIP_HOME" "$SHIP_HOME/backups" /etc/acme
    install -d -o root -g wheel -m 750 "$SHIP_LOG"
    while read -r _site _domain _c; do
        install -d -o root -g wheel -m 755 "$SITES_BASE/$_site" "$SITES_BASE/$_site/releases" \
            "$SITES_BASE/$_site/shared" "$SITES_BASE/$_site/shared/pub"
    done < "$SITES_LIST"
}

# -------------------------
# nginx
# -------------------------
BASE_CSP="default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"

# csp_for <site> <domain> <comments>: the strict default plus the comments host and the site's
# optional sites/ship/<site>.csp lines ("<directive> <source>...").
csp_for() {
    {
        if [ "$3" = yes ]; then
            echo "script-src https://comments.$2"
            echo "connect-src https://comments.$2"
        fi
        [ -f "$STAGE/sites/$1.csp" ] && tr -d '\r' < "$STAGE/sites/$1.csp"
    } | awk -v base="$BASE_CSP" '
        BEGIN {
            n = split(base, parts, /; */)
            for (i = 1; i <= n; i++) { split(parts[i], w, " "); order[i] = w[1]; val[w[1]] = parts[i] }
            cnt = n
        }
        /^[ \t]*(#|$)/ { next }
        /[";{}$\\]/ { print "bad CSP line ignored: " $0 > "/dev/stderr"; next }
        {
            d = $1; src = ""
            for (j = 2; j <= NF; j++) src = src " " $j
            if (d in val) val[d] = val[d] src
            else { cnt++; order[cnt] = d; val[d] = d src }
        }
        END { out = ""; for (i = 1; i <= cnt; i++) out = out (i > 1 ? "; " : "") val[order[i]]; print out }'
}

HSTS='add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;'

# Render every nginx file into $1 (a fresh directory mirroring $NGX_DIR).
render_nginx_into() {
    _out="$1"
    cp "$CONFIG/default.conf" "$CONFIG/acme.inc" "$CONFIG/static.inc" "$_out/"
    while read -r _site _domain _comments; do
        export SITE="$_site" DOMAIN="$_domain" SITE_ROOT="$SITES_BASE/$_site"
        _csp="$(csp_for "$_site" "$_domain" "$_comments")"
        export CSP="$_csp"
        if has_cert "$_domain"; then _mode=tls; export HSTS_LINE="$HSTS"; else _mode=http; export HSTS_LINE="# no HSTS until the certificate exists"; fi
        export EXTRA_INCLUDE=""
        if [ -f "$STAGE/sites/$_site.nginx.extra" ]; then
            tr -d '\r' < "$STAGE/sites/$_site.nginx.extra" > "$TMP/extra.tmpl"
            render "$TMP/extra.tmpl" > "$_out/$_site.extra.inc"
            export EXTRA_INCLUDE="include $NGX_DIR/$_site.extra.inc;"
        fi
        render "$CONFIG/headers.conf.tmpl" > "$_out/$_site.headers"
        render "$CONFIG/site-$_mode.conf.tmpl" > "$_out/$_site.conf"
        if [ "$_comments" = yes ]; then
            if [ -n "${ISSO_ADMIN_ALLOW_IP:-}" ]; then
                export ISSO_ADMIN_RULE="    allow ${ISSO_ADMIN_ALLOW_IP};
    deny all;"
            else
                export ISSO_ADMIN_RULE="    return 404;"
            fi
            render "$CONFIG/comments.inc.tmpl" > "$_out/comments.inc"
            render "$CONFIG/comments.headers.tmpl" > "$_out/comments.headers"
            render "$CONFIG/comments-$_mode.conf.tmpl" > "$_out/comments.conf"
        fi
    done < "$SITES_LIST"
}

# Install the rendered tree; test with nginx -t and roll back on failure. Returns 0 when changed.
apply_nginx() {
    rm -rf "$TMP/ngx" "$TMP/ngx-old"
    mkdir -p "$TMP/ngx" "$TMP/ngx-old"
    render_nginx_into "$TMP/ngx"
    cp -p /etc/nginx/nginx.conf "$TMP/ngx-old.nginx.conf" 2>/dev/null || true
    cp -p "$NGX_DIR"/* "$TMP/ngx-old/" 2>/dev/null || true

    _ngx_changed=1
    install_if_changed "$CONFIG/nginx.conf" /etc/nginx/nginx.conf root:wheel 644 "nginx.conf" && _ngx_changed=0
    for _f in "$TMP/ngx"/*; do
        install_if_changed "$_f" "$NGX_DIR/$(basename "$_f")" root:wheel 644 "nginx/$(basename "$_f")" && _ngx_changed=0
    done
    for _f in "$NGX_DIR"/*; do
        [ -e "$_f" ] || continue
        if [ ! -e "$TMP/ngx/$(basename "$_f")" ]; then rm -f "$_f"; changed "nginx/$(basename "$_f") (removed)"; _ngx_changed=0; fi
    done

    if ! nginx -t -q; then
        rm -f "$NGX_DIR"/*
        cp -p "$TMP/ngx-old"/* "$NGX_DIR/" 2>/dev/null || true
        [ -f "$TMP/ngx-old.nginx.conf" ] && cp -p "$TMP/ngx-old.nginx.conf" /etc/nginx/nginx.conf
        die "New nginx config fails nginx -t; the previous config was restored"
    fi
    rcctl enable nginx >/dev/null
    if ! rcctl check nginx >/dev/null; then
        rcctl start nginx >/dev/null || die "nginx did not start"
    elif [ "$_ngx_changed" = 0 ]; then
        rcctl reload nginx >/dev/null || die "nginx reload failed"
    fi
    return "$_ngx_changed"
}

# -------------------------
# Certificates (Let's Encrypt via acme-client)
# -------------------------
setup_acme_conf() {
    cp "$CONFIG/acme-client.conf.head" "$TMP/acme-client.conf"
    while read -r _site _domain _comments; do
        _alt="www.$_domain"
        [ "$_comments" = yes ] && _alt="$_alt comments.$_domain"
        export DOMAIN="$_domain" ALT_NAMES="$_alt"
        render "$CONFIG/acme-domain.tmpl" >> "$TMP/acme-client.conf"
    done < "$SITES_LIST"
    acme-client -n -f "$TMP/acme-client.conf" "$(awk 'NR == 1 { print $2 }' "$SITES_LIST")" >/dev/null \
        || die "acme-client.conf does not parse"
    install_if_changed "$TMP/acme-client.conf" /etc/acme-client.conf root:wheel 644 "acme-client.conf" || true
    if [ ! -f /etc/acme/letsencrypt-privkey.pem ]; then
        openssl genrsa -out /etc/acme/letsencrypt-privkey.pem 4096 >/dev/null 2>&1
        chmod 600 /etc/acme/letsencrypt-privkey.pem
        changed "acme account key"
    fi
}

# Does <name> reach this server over HTTP? A random file under the challenge path answers that
# without needing DNS tools: if DNS points elsewhere, the token does not come back.
reaches_here() {
    _tok="ship-probe-$(openssl rand -hex 8)"
    echo "$_tok" > "/var/www/acme/$_tok"
    _got="$(curl -s -m 8 "http://$1/.well-known/acme-challenge/$_tok" 2>/dev/null)"
    rm -f "/var/www/acme/$_tok"
    [ "$_got" = "$_tok" ]
}

# Returns 0 when any certificate was created or renewed.
setup_certs() {
    _new=1
    while read -r _site _domain _comments; do
        _names="$_domain www.$_domain"
        [ "$_comments" = yes ] && _names="$_names comments.$_domain"
        _ready=1
        for _n in $_names; do reaches_here "$_n" || { _ready=0; break; }; done
        if [ "$_ready" != 1 ]; then
            if has_cert "$_domain"; then
                info "DNS for $_domain does not point here yet; using the existing certificate"
            else
                warn "DNS for $_names does not point here yet: $_domain is served over HTTP only. Re-run deploy-server.sh after switching DNS."
            fi
            continue
        fi
        acme-client "$_domain"
        case $? in
            0) changed "certificate:$_domain"; _new=0 ;;
            2) unchanged "certificate:$_domain" ;;
            *) warn "acme-client failed for $_domain (see output above)" ;;
        esac
    done < "$SITES_LIST"
    return "$_new"
}

# -------------------------
# isso
# -------------------------
setup_isso() {
    if [ -z "$COMMENTS_SITE" ]; then
        if [ -f /etc/rc.d/isso ]; then
            rcctl stop isso >/dev/null 2>&1; rcctl disable isso >/dev/null 2>&1
            changed "isso (disabled: no site has comments)"
        fi
        return 0
    fi
    install -d -o root -g _isso -m 750 /var/isso
    install -d -o _isso -g _isso -m 700 /var/isso/db

    _py="$(command -v python3 || ls /usr/local/bin/python3.[0-9]* 2>/dev/null | sort -t. -k2 -n | tail -1)"
    [ -x "$_py" ] || die "python3 not found"
    _want="$($_py -V 2>&1) reqs:$(cksum < "$ISSO_REQS" | cut -d' ' -f1)"
    _restart=0
    if [ "$(cat /var/isso/venv/.ship-version 2>/dev/null)" != "$_want" ]; then
        info "Building the isso venv ($_want)..."
        rm -rf /var/isso/venv
        "$_py" -m venv --system-site-packages /var/isso/venv || die "python3 -m venv failed"
        /var/isso/venv/bin/pip install -q --disable-pip-version-check -r "$ISSO_REQS" || die "pip install isso failed"
        echo "$_want" > /var/isso/venv/.ship-version
        chown -R root:_isso /var/isso/venv; chmod -R g+rX,o-rwx /var/isso/venv
        changed "isso venv"; _restart=1
    else
        unchanged "isso venv"
    fi

    export DOMAIN="$COMMENTS_DOMAIN"
    if [ "$MAIL_RELAY" = tem ]; then export ISSO_NOTIFY=smtp; else export ISSO_NOTIFY=stdout; fi
    if [ -n "${ISSO_ADMIN_ALLOW_IP:-}" ]; then export ISSO_ADMIN_ENABLED=true; else export ISSO_ADMIN_ENABLED=false; fi
    render "$CONFIG/isso.conf.tmpl" > "$TMP/isso.conf"
    install_if_changed "$TMP/isso.conf" /etc/isso.conf root:_isso 640 "isso.conf" && _restart=1
    install_if_changed "$CONFIG/rc.d.isso" /etc/rc.d/isso root:wheel 555 "rc.d/isso" && _restart=1

    rcctl enable isso >/dev/null
    if [ "$_restart" = 1 ] || ! rcctl check isso >/dev/null; then
        if ! rcctl restart isso >/dev/null; then
            warn "isso did not start; its last log lines follow"
            grep -i isso /var/log/daemon | tail -15 | sed 's/^/[setup]   isso: /'
        fi
    fi
}

# -------------------------
# Outgoing mail
# -------------------------
setup_mail() {
    if [ "$MAIL_RELAY" = tem ]; then
        printf 'tem %s:%s\n' "$TEM_SMTP_USER" "$TEM_SMTP_PASSWORD" > "$TMP/ship-secrets"
        _sec_changed=1
        install_if_changed "$TMP/ship-secrets" /etc/mail/ship-secrets root:_smtpd 640 "mail secrets" && _sec_changed=0
        export MAIL_FROM
        render "$CONFIG/smtpd.conf.tmpl" > "$TMP/smtpd.conf"
    else
        printf '%s\n' "# bones/ship MAIL_RELAY=none: local delivery only" \
            "table aliases file:/etc/mail/aliases" "listen on socket" "listen on lo0" \
            "action \"local\" mbox alias <aliases>" "match from local for local action \"local\"" > "$TMP/smtpd.conf"
        rm -f /etc/mail/ship-secrets
        _sec_changed=1
    fi
    smtpd -n -f "$TMP/smtpd.conf" >/dev/null 2>&1 || { smtpd -n -f "$TMP/smtpd.conf"; die "smtpd.conf does not parse"; }
    if install_if_changed "$TMP/smtpd.conf" /etc/mail/smtpd.conf root:wheel 644 "smtpd.conf" || [ "$_sec_changed" = 0 ]; then
        rcctl restart smtpd >/dev/null || warn "smtpd restart failed"
    fi
}

# -------------------------
# Ops scripts, cron, log rotation
# -------------------------
setup_ops() {
    for _s in monitor.sh backup-isso.sh auto-update.sh renew-certs.sh; do
        install_if_changed "$STAGE/server/$_s" "$SHIP_HOME/$_s" root:wheel 700 "$_s" || true
    done
    install_if_changed "$STAGE/env_vars.sh" "$SHIP_HOME/env_vars.sh" root:wheel 600 "server env_vars" || true
    install_if_changed "$SITES_LIST" "$SHIP_HOME/sites.conf" root:wheel 600 "server sites.conf" || true
    install_if_changed "$ISSO_REQS" "$SHIP_HOME/isso-requirements.txt" root:wheel 600 "isso-requirements.txt" || true
    rm -f "$SHIP_HOME/isso.version"

    _l="$SHIP_LOG/monitor.log"
    {
        echo "*/15 * * * * $SHIP_HOME/monitor.sh >> $_l 2>&1"
        echo "* * * * * $SHIP_HOME/monitor.sh --guard >> $_l 2>&1"
        [ -n "$COMMENTS_SITE" ] && echo "30 3 * * * $SHIP_HOME/backup-isso.sh daily >> $_l 2>&1"
        echo "11 3 * * * $SHIP_HOME/renew-certs.sh >> $_l 2>&1"
        echo "30 4 * * * $SHIP_HOME/auto-update.sh >> $_l 2>&1"
        echo "*/10 * * * * /sbin/pfctl -t bruteforce -T expire 3600 >/dev/null 2>&1"
    } > "$TMP/cron.block"
    crontab -l -u root > "$TMP/cron.now" 2>/dev/null || : > "$TMP/cron.now"
    replace_block "$TMP/cron.now" "$TMP/cron.block" "#" > "$TMP/cron.new"
    if cmp -s "$TMP/cron.now" "$TMP/cron.new"; then unchanged "root crontab"; else crontab -u root "$TMP/cron.new" && changed "root crontab"; fi

    grep -v '^#' "$CONFIG/newsyslog.conf" | grep . > "$TMP/newsyslog.block"
    replace_block /etc/newsyslog.conf "$TMP/newsyslog.block" "#" > "$TMP/newsyslog.conf"
    install_if_changed "$TMP/newsyslog.conf" /etc/newsyslog.conf root:wheel 644 "newsyslog.conf" || true
}

# -------------------------
# Main
# -------------------------
info "Converging $(hostname) (LOCK_ROOT=$LOCK_ROOT)"
setup_packages
setup_users
setup_sshd
setup_pf
setup_dirs
setup_acme_conf
apply_nginx || true
if setup_certs; then
    apply_nginx || true    # a new certificate switches that site to its TLS config
fi
setup_isso
setup_mail
setup_ops

echo
echo "[setup] changed:  ${CHANGED:- (nothing)}"
echo "[setup] unchanged:${UNCHANGED:- (nothing)}"
[ -z "$WARNINGS" ] || echo "[setup] warnings:$WARNINGS"
sshd -T | grep -Ei '^(permitrootlogin|allowusers|passwordauthentication) ' | sed 's/^/[setup] sshd: /'
exit 0

#!/bin/ksh
# Renew Let's Encrypt certificates (root cron, daily). acme-client only renews a certificate
# that expires within 30 days or whose names changed, so a daily run is cheap.
# nginx is reloaded only after a renewal and only if `nginx -t` passes; unlike relayd, a failed
# nginx reload keeps the old workers serving.

PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
export PATH
ts() { date '+%Y-%m-%d %H:%M:%S'; }

_renewed=0
while read -r _site _domain _comments; do
    [ -s "/etc/ssl/$_domain:443.crt" ] || continue
    acme-client "$_domain"
    case $? in
        0) echo "$(ts) renew: new certificate for $_domain"; _renewed=1 ;;
        2) ;;
        *) echo "$(ts) renew: acme-client failed for $_domain (monitor alerts before it expires)" ;;
    esac
done < /var/ship/sites.conf

if [ "$_renewed" = 1 ]; then
    if nginx -t -q; then
        rcctl reload nginx >/dev/null && echo "$(ts) renew: nginx reloaded"
    else
        echo "$(ts) renew: nginx -t failed; not reloading"
        exit 1
    fi
fi
exit 0

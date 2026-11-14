#!/usr/bin/env bash
# Build one bones site and publish it as a new release (atomic switch, automatic rollback).
#
# Usage (from the sites repo root, Git Bash):
#   bash ../bones/ship/deploy-site.sh prod blog
#
# Gates: the site is in ship/sites.conf, settings are complete and the build succeeds. The build
# runs on a dirty tree; afterwards the changes in bones and sites (build output included) are
# shown and the script waits until they are committed (ALLOW_DIRTY=1 publishes them uncommitted).
# Then: tar z-public/prod -> /var/www/sites/<site>/releases/<UTC>-b<bones sha>-s<sites sha>/
# (root-owned, read-only for nginx) -> `current` symlink switched in one step. The site's
# <site>/pub/ folder (if any) replaces shared/pub, outside the releases, once a smoke test (/ and
# /feed.xml through nginx with the real host name) passed; if it fails, `current` goes back.
# The newest KEEP_RELEASES releases are kept for rollback.sh.
set -euo pipefail

SHIP_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=lib/common.sh
. "$SHIP_DIR/lib/common.sh"

USAGE_REST="<site>"
parse_env_arg "$@"
SITE="${2:-}"
[ -n "$SITE" ] || log_error "Usage: ship <site> ${DEPLOY_ENV} deploy-site.$(sites_hint)"
resolve_sites_root
load_env
validate_sites_conf
assert_site "$SITE"
DOMAIN="$(site_field "$SITE" 2)"

OUT_DIR="${SITES_ROOT}/${SITE}/z-public/prod"

WORK="$(mktemp -d)"
cleanup() { remote_stage_cleanup; rm -rf "$WORK"; }
trap cleanup EXIT

log_info "Building ${SITE}..."
touch "$WORK/started"
(cd "$SITES_ROOT" && bash "$BONES_ROOT/build.sh" "$SITE" prod) || log_error "bones build failed"
[ -f "$OUT_DIR/index.html" ] || log_error "Build produced no ${OUT_DIR}/index.html"
[ "$OUT_DIR/index.html" -nt "$WORK/started" ] || log_error "${OUT_DIR}/index.html was not rebuilt; check the bones output path"
log_success "Built $(find "$OUT_DIR" -type f | wc -l | tr -d ' ') files"

# The build output is committed in the sites repo: publish only once it (and the source) is.
wait_for_clean "$BONES_ROOT" "$SITES_ROOT"
BONES_REV="$(git_rev "$BONES_ROOT")" || exit 1
SITES_REV="$(git_rev "$SITES_ROOT")" || exit 1
RELEASE_ID="$(date -u +%Y%m%d%H%M%S)-b${BONES_REV}-s${SITES_REV}"
log_info "Release ${RELEASE_ID}"

tar -czf "$WORK/site.tgz" -C "$OUT_DIR" .
HAS_PUB=0
if [ -d "${SITES_ROOT}/${SITE}/pub" ]; then
  tar -czf "$WORK/pub.tgz" -C "${SITES_ROOT}/${SITE}/pub" .
  HAS_PUB=1
fi

ensure_known_host "$SERVER_USER"
remote_stage_create
files=("$WORK/site.tgz"); [ "$HAS_PUB" = 1 ] && files+=("$WORK/pub.tgz")
scp_to "$SERVER_USER" "$REMOTE_STAGE" "${files[@]}"

log_info "Publishing on ${SERVER_IP}..."
remote_root "Publishing ${SITE}" SITE="$SITE" DOMAIN="$DOMAIN" RELEASE_ID="$RELEASE_ID" STAGE="$REMOTE_STAGE" \
  KEEP="$KEEP_RELEASES" HAS_PUB="$HAS_PUB" <<'EOF'
set -e
PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
base="/var/www/sites/$SITE"
[ -d "$base/releases" ] || { echo "No $base: run deploy-server.sh first"; exit 1; }
rel="$base/releases/$RELEASE_ID"
rm -rf "$rel"; mkdir -p "$rel"
tar -xzf "$STAGE/site.tgz" -C "$rel"
chown -R root:wheel "$rel"; chmod -R u=rwX,go=rX "$rel"

prev="$(readlink "$base/current" 2>/dev/null || true)"
ln -sfh "releases/$RELEASE_ID" "$base/current"

smoke() {
    if [ -s "/etc/ssl/$DOMAIN:443.crt" ]; then
        curl -s -o /dev/null -w '%{http_code}' -m 10 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN$1"
    else
        curl -s -o /dev/null -w '%{http_code}' -m 10 -H "Host: $DOMAIN" "http://127.0.0.1$1"
    fi
}
paths="/"
[ -f "$rel/feed.xml" ] && paths="$paths /feed.xml"
for p in $paths; do
    code="$(smoke "$p")"
    if [ "$code" != 200 ]; then
        echo "Smoke test failed: $p returned HTTP ${code:-none}"
        if [ -n "$prev" ]; then ln -sfh "$prev" "$base/current"; echo "Switched back to $prev"; else rm -f "$base/current"; fi
        rm -rf "$rel"
        exit 1
    fi
    echo "smoke $p: 200"
done

# The site's pub/ folder (outside the releases) is replaced only once the release passed.
if [ "$HAS_PUB" = 1 ]; then
    rm -rf "$base/shared/pub.new" "$base/shared/pub.old"
    mkdir -p "$base/shared/pub.new"
    tar -xzf "$STAGE/pub.tgz" -C "$base/shared/pub.new"
    chown -R root:wheel "$base/shared/pub.new"; chmod -R u=rwX,go=rX "$base/shared/pub.new"
    if [ -d "$base/shared/pub" ]; then mv "$base/shared/pub" "$base/shared/pub.old"; fi
    mv "$base/shared/pub.new" "$base/shared/pub"
    rm -rf "$base/shared/pub.old"
fi

cur="$(readlink "$base/current")"
ls -1 "$base/releases" | sort -r | tail -n "+$((KEEP + 1))" | while read -r old; do
    [ "releases/$old" = "$cur" ] || rm -rf "$base/releases/$old"
done
echo "LIVE $RELEASE_ID"
EOF
log_success "${SITE} is live: release ${RELEASE_ID}"

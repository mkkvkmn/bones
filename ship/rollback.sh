#!/usr/bin/env bash
# Switch a site to an earlier kept release.
#
# Usage (from the sites repo root):
#   bash ../bones/ship/rollback.sh prod blog --list     # releases on the server, live one marked
#   bash ../bones/ship/rollback.sh prod blog            # the release before the live one
#   bash ../bones/ship/rollback.sh prod blog <id>       # a specific release
set -euo pipefail

SHIP_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=lib/common.sh
. "$SHIP_DIR/lib/common.sh"

USAGE_REST="<site> [--list|<release id>]"
parse_env_arg "$@"
SITE="${2:-}"; TARGET="${3:-}"
[ -n "$SITE" ] || log_error "Usage: bash ../bones/ship/rollback.sh ${DEPLOY_ENV} <site> [--list|<release id>]"
resolve_sites_root
load_env
assert_site "$SITE"
[ -z "$TARGET" ] || [ "$TARGET" = --list ] || [[ "$TARGET" =~ ^[0-9]{14}-b[0-9a-f]+(-dirty)?-s[0-9a-f]+(-dirty)?$ ]] \
  || log_error "Bad release id '${TARGET}' (see --list)"
[ "$TARGET" = --list ] && MODE=list || MODE=switch

remote_root "Rollback of ${SITE}" SITE="$SITE" TARGET="${TARGET#--list}" MODE="$MODE" <<'EOF'
set -e
base="/var/www/sites/$SITE"
cur="$(readlink "$base/current" 2>/dev/null | sed 's#^releases/##')"
if [ "$MODE" = list ]; then
    ls -1 "$base/releases" | sort -r | while read -r r; do
        [ "$r" = "$cur" ] && echo "* $r (live)" || echo "  $r"
    done
    exit 0
fi
if [ -z "$TARGET" ]; then
    TARGET="$(ls -1 "$base/releases" | sort -r | awk -v c="$cur" 'found { print; exit } $0 == c { found = 1 }')"
    [ -n "$TARGET" ] || { echo "No release older than $cur"; exit 1; }
fi
[ -d "$base/releases/$TARGET" ] || { echo "No release $TARGET"; exit 1; }
ln -sfh "releases/$TARGET" "$base/current"
echo "$SITE: $cur -> $TARGET"
EOF

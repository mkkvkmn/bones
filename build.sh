#!/usr/bin/env bash
# Build a bones site locally, optionally serving it and rebuilding on changes.
#
# Usage (from the sites repo root, Git Bash; the `build` command does this for you):
#   bash ../bones/build.sh                   list sites
#   bash ../bones/build.sh <site>            dev build
#   bash ../bones/build.sh <site> serve      dev build, then serve it (envs.dev.url in build.yml)
#   bash ../bones/build.sh <site> watch      dev build, serve, rebuild when the site or theme changes
#   bash ../bones/build.sh <site> prod       prod build (what deploy-site.sh publishes)
set -euo pipefail

SHIP_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/ship" && pwd)"
# shellcheck source=ship/lib/common.sh
. "$SHIP_DIR/lib/common.sh"

SITES_ROOT="$(CDPATH='' cd -- "${SITES_ROOT:-$PWD}" && pwd)"
case "$SITES_ROOT/" in
  "$BONES_ROOT/"*) log_error "Run from the sites repo, not from bones." ;;
esac
# bones resolves sites relative to its own folder, so the two must be side by side.
[ "$(dirname "$SITES_ROOT")" = "$(dirname "$BONES_ROOT")" ] \
  || log_error "Run from a sites repo next to bones ($(dirname "$BONES_ROOT")/<sites>), not ${SITES_ROOT}"
SITES_FOLDER_REL="../$(basename "$SITES_ROOT")"

list_sites() {
  local d
  for d in "$SITES_ROOT"/*/config/build.yml; do
    [ -f "$d" ] && printf '  %s\n' "$(basename "$(dirname "$(dirname "$d")")")"
  done
}

usage() {
  printf 'Usage: build <site> [serve|watch|prod]\nSites:\n'
  list_sites
  exit "${1:-0}"
}

SITE="${1:-}"
MODE="${2:-}"
[ -n "$SITE" ] || usage
[ -f "$SITES_ROOT/$SITE/config/build.yml" ] || { printf 'No site "%s" in %s\n' "$SITE" "$SITES_ROOT"; usage 1; }
[ $# -le 2 ] || usage 1

PY="$(find_python)"
gen() { # gen <env> [gen.py args...]
  local env="$1"; shift
  (cd "$BONES_ROOT" && SITES_FOLDER="$SITES_FOLDER_REL" SITE_NAME="$SITE" BUILD_ENV="$env" \
    "$PY" gen.py --env "$env" --site-name "$SITE" --sites-folder "$SITES_FOLDER_REL" "$@")
}
dev_server() { (cd "$BONES_ROOT" && "$PY" dev.py --sites-folder "$SITES_FOLDER_REL" --site-name "$SITE" "$@"); }

case "$MODE" in
  "")    gen dev ;;
  prod)  gen prod ;;
  serve) gen dev && dev_server ;;
  watch) dev_server --watch ;;
  *)     printf 'Not a mode: %s\n' "$MODE"; usage 1 ;;
esac

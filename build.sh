#!/usr/bin/env bash
# Build a bones site locally, optionally serving it and rebuilding on changes.
#
# Usage (from the sites repo root, Git Bash; the `bones` command runs this from any folder):
#   bash ../bones/build.sh                   list sites
#   bash ../bones/build.sh <site>            dev build
#   bash ../bones/build.sh <site> serve      dev build, then serve it (envs.dev.url in build.yml)
#   bash ../bones/build.sh <site> watch      dev build, serve, rebuild when the site or theme changes
#   bash ../bones/build.sh <site> prod       prod build (what deploy-site.sh publishes)
#   bash ../bones/build.sh <site> publish <draft>
#                                            move content/drafts/*<draft>* to content/posts/<year>/<today>-<slug>/,
#                                            dated today (a draft is one .md file, or a folder with one .md in it)
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
  printf 'Usage: bones <site> [serve|watch|prod|publish <draft>]\nSites:\n'
  list_sites
  exit "${1:-0}"
}

SITE="${1:-}"
MODE="${2:-}"
[ -n "$SITE" ] || usage
[ -f "$SITES_ROOT/$SITE/config/build.yml" ] || { printf 'No site "%s" in %s\n' "$SITE" "$SITES_ROOT"; usage 1; }
if [ "$MODE" = publish ]; then [ $# -eq 3 ] || usage 1; else [ $# -le 2 ] || usage 1; fi

publish() { # publish <part of a draft's name>
  local drafts="$SITES_ROOT/$SITE/content/drafts" m matches=()
  for m in "$drafts"/*"$1"*; do
    if [ -d "$m" ] || { [ -f "$m" ] && [[ "$m" == *.md ]]; }; then matches+=("$m"); fi
  done
  [ ${#matches[@]} -eq 1 ] \
    || log_error "Expected one draft matching \"$1\" in ${drafts}, found ${#matches[@]}: ${matches[*]##*/}"

  local src="${matches[0]}" md="${matches[0]}"
  if [ -d "$src" ]; then
    local mds=("$src"/*.md)
    [ ${#mds[@]} -eq 1 ] && [ -f "${mds[0]}" ] || log_error "Expected one .md file in ${src}"
    md="${mds[0]}"
  fi
  [ "$(head -n 1 "$md" | tr -d '\r')" = "---" ] \
    && sed -n '2,/^---[[:space:]]*$/p' "$md" | grep -q '^date:' \
    || log_error "No \"date:\" line in the frontmatter of ${md}"

  local slug today dest file
  slug="$(basename "$src" .md | sed -E 's/^[0-9]{4}(-[0-9]{2}){0,2}-//')"
  [[ "$slug" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || log_error "Draft name gives a bad slug: \"${slug}\". Rename the draft."
  today="$(date +%F)"
  dest="$SITES_ROOT/$SITE/content/posts/${today%%-*}/${today}-${slug}"
  file="${today}-${slug}.md"
  [ ! -e "$dest" ] || log_error "Already exists: ${dest}"

  mkdir -p "$(dirname "$dest")"
  if [ -d "$src" ]; then
    mv "$src" "$dest"
    [ "$(basename "$md")" = "${file}" ] || mv "$dest/$(basename "$md")" "$dest/${file}"
  else
    mkdir "$dest"
    mv "$src" "$dest/${file}"
  fi
  mkdir -p "$dest/images"
  sed -b -i "1,/^date:/ s/^date:[^\r]*/date: $(date +'%Y-%m-%d %H:%M:%S %z')/" "$dest/${file}"
  log_success "Published ${dest#"$SITES_ROOT"/}/${file}"
}

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
  publish) publish "$3" ;;
  *)     printf 'Not a mode: %s\n' "$MODE"; usage 1 ;;
esac

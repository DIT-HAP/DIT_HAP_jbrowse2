#!/usr/bin/env bash
# DIT-HAP genome browser -- build driver.
#
#   ./build.sh data     rebuild the data layer (reference, tracks)
#   ./build.sh site     assemble site/ from the current data + config
#   ./build.sh all      both, in order
#   ./build.sh serve    serve site/ locally on :8080
#   ./build.sh clean    remove site/ and .build/
#
# `data` is slow and its outputs are committed, so it only needs re-running when
# the upstream analysis changes. `site` is fast and runs on every deploy; CI
# runs `site` only, which keeps bioinformatics tooling off the deploy path.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export REPO_ROOT

# shellcheck source=scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

# Data stages run in filename order. 99_* is the site assembly and is excluded,
# so dropping in a new NN_*.sh is all it takes to extend the pipeline.
data_stages() {
  find "$REPO_ROOT/scripts" -maxdepth 1 -name '[0-9][0-9]_*' ! -name '99_*' \
    | LC_ALL=C sort
}

cmd_data() {
  local s
  for s in $(data_stages); do
    log "$(basename "$s")"
    bash "$s"
    echo
  done
}

cmd_site() {
  bash "$REPO_ROOT/scripts/99_assemble_site.sh" "$@"
}

cmd_serve() {
  [ -d "$REPO_ROOT/site" ] || die "site/ does not exist. Run ./build.sh site first."
  local port="${PORT:-8080}"
  log "serving $REPO_ROOT/site on http://127.0.0.1:$port/"
  note "Ctrl-C to stop"
  cd "$REPO_ROOT/site"
  "$(require_tool python3)" -I -m http.server "$port" --bind 127.0.0.1
}

cmd_clean() {
  log "cleaning"
  rm -rf "$REPO_ROOT/site" "$REPO_ROOT/.build"
  note "removed site/ and .build/"
}

usage() {
  sed -n '2,14p' "$REPO_ROOT/build.sh" | sed 's/^# \{0,1\}//'
}

case "${1:-}" in
  data)  shift; cmd_data "$@" ;;
  site)  shift; cmd_site "$@" ;;
  all)   shift; cmd_data; cmd_site "$@" ;;
  serve) shift; cmd_serve "$@" ;;
  clean) shift; cmd_clean "$@" ;;
  ""|-h|--help|help) usage ;;
  *) die "unknown command '$1' (try ./build.sh --help)" ;;
esac

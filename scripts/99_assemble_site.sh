#!/usr/bin/env bash
# Assemble site/ -- the directory that is uploaded to GitHub Pages.
#
# site/ is a build artefact and is gitignored. It contains the unmodified
# JBrowse 2 web release, plus three things layered on top of it:
#   config.json  generated from config/*.yaml
#   plugins/     vendored JBrowse plugins (committed under vendor/)
#   data/        the reference layer and the tracks (committed under data/)
#
# The JBrowse version is pinned: an unpinned `jbrowse create` would silently
# change the site under any config that depends on renderer behaviour.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

JBROWSE_VERSION="v4.3.0"
JBROWSE="$(require_tool jbrowse)"
PYTHON="$(require_tool python3)"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
  esac
done

[ -f "$DATA_DIR/assembly/pombe.fa.gz.fai" ] || \
  die "reference layer missing. Run ./build.sh data first."

# --- 1. the JBrowse 2 web app -----------------------------------------------
if [ "$FORCE" = 1 ] || [ ! -f "$SITE_DIR/index.html" ]; then
  log "jbrowse create $JBROWSE_VERSION"
  rm -rf "$SITE_DIR"
  "$JBROWSE" create "$SITE_DIR" -t "$JBROWSE_VERSION" --force
else
  log "jbrowse app present ($(cat "$SITE_DIR/version.txt" 2>/dev/null || echo 'version unknown'))"
  note "pass --force to re-download"
fi

# --- 2. config.json from the YAML sources -----------------------------------
log "generating config.json"
"$PYTHON" -I "$REPO_ROOT/scripts/lib/yamlspec.py" --out "$SITE_DIR/config.json"

# --- 3. plugins --------------------------------------------------------------
if [ -d "$REPO_ROOT/vendor/plugins" ]; then
  mkdir -p "$SITE_DIR/plugins"
  cp -f "$REPO_ROOT"/vendor/plugins/*.js "$SITE_DIR/plugins/"
  log "plugins: $(cd "$SITE_DIR/plugins" && ls *.js 2>/dev/null | tr '\n' ' ')"
fi

# --- 4. data -----------------------------------------------------------------
# Copied rather than symlinked: the Pages artifact action does not carry
# symlinks through, and a symlinked tree would deploy as broken links.
log "copying data/"
rm -rf "$SITE_DIR/data"
mkdir -p "$SITE_DIR/data"
for sub in assembly annotation tracks; do
  [ -d "$DATA_DIR/$sub" ] || continue
  mkdir -p "$SITE_DIR/data/$sub"
  cp -R "$DATA_DIR/$sub/." "$SITE_DIR/data/$sub/"
done

# --- 5. landing page ---------------------------------------------------------
if [ -f "$REPO_ROOT/docs/index_fragment.html" ]; then
  log "applying landing page fragment"
  "$PYTHON" -I "$REPO_ROOT/scripts/lib/inject_landing.py" \
    --site "$SITE_DIR" --fragment "$REPO_ROOT/docs/index_fragment.html"
fi

# --- report ------------------------------------------------------------------
log "site assembled"
note "path:    $SITE_DIR"
note "size:    $(du -sh "$SITE_DIR" | cut -f1)"
note "data:    $(du -sh "$SITE_DIR/data" | cut -f1)"
note "config:  $(wc -c < "$SITE_DIR/config.json") bytes"

# GitHub documents a 1 GiB ceiling on a published Pages site. Anything that has
# to be committed also has to clear Git's own 100 MiB per-file block, which is
# what the data/ warning below is about -- site/ itself is never committed.
SIZE_MB="$(du -sm "$SITE_DIR" | cut -f1)"
if [ "$SIZE_MB" -gt 1024 ]; then
  warn "site/ is ${SIZE_MB} MB, over the 1 GiB published-site limit"
fi

# Files in data/ are committed, and Git blocks anything over 100 MiB outright.
BIG="$(find "$DATA_DIR" -type f -size +100M -printf '%s\t%p\n' 2>/dev/null || true)"
if [ -n "$BIG" ]; then
  warn "committed files over Git's 100 MiB block limit (cannot be pushed):"
  printf '      %s\n' $BIG
fi

#!/usr/bin/env bash
# Assemble site/ -- the directory that is uploaded to GitHub Pages.
#
# site/ is a build artefact and is gitignored. It contains the unmodified
# JBrowse 2 web release, plus four things layered on top of it:
#   config.json  generated from config/*.yaml, then extended with the search index
#   plugins/     vendored JBrowse plugins (committed under vendor/)
#   data/        the reference layer, the tracks, and the mirrored third-party
#                datasets (all committed under data/)
#   trix/        the search index, rebuilt from data/annotation on every assembly
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

# `jbrowse create` ships test_data/ -- the human and yeast sample files its demo
# tracks point at, about 55 MB. Our generated config.json replaces the demo
# config wholesale, so nothing references them and they would only be published.
# Removed unconditionally: this has to happen whether or not the app was
# re-downloaded just now.
if [ -d "$SITE_DIR/test_data" ]; then
  rm -rf "$SITE_DIR/test_data"
fi

# --- 2. config.json from the YAML sources -----------------------------------
log "generating config.json"
"$PYTHON" -I "$REPO_ROOT/scripts/lib/yamlspec.py" --out "$SITE_DIR/config.json"

# --- 3. plugins --------------------------------------------------------------
# Two sources, one destination. vendor/plugins/ is upstream bundles, committed
# byte-for-byte as downloaded; plugins/ is our own hand-written JS, no build
# step. Both are served as-is and both are named by config.json's `plugins`.
mkdir -p "$SITE_DIR/plugins"
for src in "$REPO_ROOT/vendor/plugins" "$REPO_ROOT/plugins"; do
  [ -d "$src" ] || continue
  cp -f "$src"/*.js "$SITE_DIR/plugins/"
done
log "plugins: $(cd "$SITE_DIR/plugins" && ls *.js 2>/dev/null | tr '\n' ' ')"

# --- 4. data -----------------------------------------------------------------
# Copied rather than symlinked: the Pages artifact action does not carry
# symlinks through, and a symlinked tree would deploy as broken links.
log "copying data/"
rm -rf "$SITE_DIR/data"
mkdir -p "$SITE_DIR/data"
for sub in assembly annotation tracks external; do
  [ -d "$DATA_DIR/$sub" ] || continue
  mkdir -p "$SITE_DIR/data/$sub"
  cp -R "$DATA_DIR/$sub/." "$SITE_DIR/data/$sub/"
done

# --- 5. search index ---------------------------------------------------------
# The search box needs a trix index of the annotation, and JBrowse only shows
# the box when an assembly has one.
#
# Built here rather than committed: it is 3.7 MB derived entirely from
# data/annotation/pombe.gff.gz, so rebuilding it on every site assembly keeps
# it in step with the GFF3 instead of letting a committed copy rot. It costs
# about a second.
#
# The CLI also appends `aggregateTextSearchAdapters` to config.json. Letting it
# do so is deliberate -- it owns that schema, so a JBrowse upgrade cannot leave
# this repository maintaining a stale copy of it.
#
# Runs AFTER the data copy above so the adapter's relative GFF3 path resolves
# whether the CLI reads it from the repo root or from beside config.json, and
# after yamlspec.py so it is appending to a config that already exists.
log "indexing pombe_genes for search"
rm -rf "$SITE_DIR/trix"
(cd "$REPO_ROOT" && "$JBROWSE" text-index \
  --out "$SITE_DIR/config.json" \
  --tracks pombe_genes \
  -a pombe \
  --attributes Name,ID \
  --force --quiet) \
  || die "text-index failed. The site would build, but its search box would be dead."
note "trix: $(du -sh "$SITE_DIR/trix" 2>/dev/null | cut -f1) in $(ls "$SITE_DIR/trix" 2>/dev/null | tr '\n' ' ')"

# --- 6. landing page ---------------------------------------------------------
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

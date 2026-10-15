#!/usr/bin/env bash
# Mirror the third-party datasets the browser serves.
#
# The Kuang et al. 2017 tracks used to link straight to PomBase. They are
# downloaded here instead because PomBase delivers at 70-130 kB/s: one 1 MB
# range read measured 8-14 s, and an AlignmentsTrack issues several per view.
# Serving the same bytes from the site's own origin removes that wait.
#
# Mirrored bytes are committed under data/, like the reference layer and the
# DIT-HAP tracks, so the deployed site stays self-contained.
#
# Idempotent: a file already present with the expected checksum is left alone,
# so re-running `./build.sh data` does not re-fetch 164 MB. A file that is
# missing or does not match is (re-)downloaded -- a truncated transfer would
# otherwise ship a track that silently renders nothing.
#
# To add a dataset, leave its `sha256` empty in config/external.yaml: the file
# is downloaded and its checksum printed, ready to paste back in.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

CURL="$(require_tool curl)"
PYTHON="$(require_tool python3)"

SPEC="$CONFIG_DIR/external.yaml"
OUT_ROOT="$DATA_DIR/external"

[ -f "$SPEC" ] || die "missing $SPEC"

# Emits: dir \t filename \t url \t sha256 \t bytes
manifest() {
  "$PYTHON" -I -c '
import sys, yaml
spec = yaml.safe_load(open(sys.argv[1])) or {}
for key, ds in (spec.get("external") or {}).items():
    for f in ds["files"]:
        print("\t".join([ds["dir"], f["name"], ds["base_url"] + f["name"],
                         f.get("sha256") or "", str(f.get("bytes") or "")]))
' "$SPEC"
}

log "external datasets"
FAILED=0
while IFS=$'\t' read -r outdir name url want_sha want_bytes; do
  dest="$OUT_ROOT/$outdir/$name"
  mkdir -p "$OUT_ROOT/$outdir"

  if [ -f "$dest" ]; then
    got="$(sha256sum "$dest" | cut -d' ' -f1)"
    # An empty sha256 means "not recorded yet" -- accept what is there so the
    # bootstrap run does not re-fetch, and print the checksum below.
    if [ -z "$want_sha" ] || [ "$got" = "$want_sha" ]; then
      note "present: $outdir/$name"
      continue
    fi
    note "checksum differs, re-fetching: $outdir/$name"
  fi

  log "fetching $outdir/$name"
  # Staged in .build/ rather than beside the destination: that is the repo's
  # scratch space and is already gitignored, so an interrupted 164 MB transfer
  # is resumed by the next run without leaving a stray file in data/ for the
  # site build to publish and `git add -A` to stage.
  tmp="$BUILD_DIR/external/$outdir/$name.part"
  mkdir -p "$(dirname "$tmp")"
  if ! "$CURL" -sS -L --fail -C - --retry 5 --retry-delay 5 --max-time 7200 \
       -o "$tmp" "$url"; then
    warn "download failed: $url  (partial kept at ${tmp#"$REPO_ROOT"/})"
    FAILED=1
    continue
  fi

  got="$(sha256sum "$tmp" | cut -d' ' -f1)"
  if [ -n "$want_sha" ] && [ "$got" != "$want_sha" ]; then
    rm -f "$tmp"
    die "checksum mismatch for $outdir/$name
      want: $want_sha
      got:  $got
    The upstream file changed. Confirm it still shows the dataset the tracks
    claim to, then update config/external.yaml."
  fi

  mv "$tmp" "$dest"
  if [ -z "$want_sha" ]; then
    note "record in config/external.yaml:"
    printf '        sha256: %s\n        bytes: %s\n' "$got" "$(wc -c < "$dest")"
  else
    note "ok: $outdir/$name  ($(du -h "$dest" | cut -f1))"
  fi
done < <(manifest)

[ "$FAILED" = 0 ] || die "some external files could not be fetched (see above)"

log "mirrored"
note "$(du -sh "$OUT_ROOT" | cut -f1) under ${OUT_ROOT#"$REPO_ROOT"/}"

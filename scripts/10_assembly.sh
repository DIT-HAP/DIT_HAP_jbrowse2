#!/usr/bin/env bash
# Build the reference layer: bgzipped FASTA, bgzipped GFF3, and their indexes.
#
# Outputs land in data/ and are committed, so the deployed site never reaches
# back to PomBase. Both upstream files are checksummed against config/assembly.yaml
# before use -- if PomBase reissues a release under the same path, this fails
# loudly instead of silently changing the coordinate system under every track.
#
# bgzip (not plain gzip) is required: the browser fetches these over HTTP range
# requests, which only works on block-gzipped files with an index.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

BGZIP="$(require_tool bgzip)"
TABIX="$(require_tool tabix)"
SAMTOOLS="$(require_tool samtools)"

SRC_DIR="$(cfg sources base_dir)"
FASTA_NAME="$(cfg sources genome_fasta file)"
GFF_NAME="$(cfg sources annotation_gff3 file)"
FASTA_WANT="$(cfg sources genome_fasta sha256)"
GFF_WANT="$(cfg sources annotation_gff3 sha256)"

FASTA="$SRC_DIR/$FASTA_NAME"
GFF="$SRC_DIR/$GFF_NAME"

OUT_ASM="$DATA_DIR/assembly"
OUT_ANN="$DATA_DIR/annotation"
mkdir -p "$OUT_ASM" "$OUT_ANN"

log "reference layer"
note "source: $SRC_DIR"

# --- verify upstream inputs --------------------------------------------------
verify_sha() {
  local path="$1" want="$2" label="$3"
  [ -f "$path" ] || die "$label not found: $path"
  local got
  got="$(sha256sum "$path" | cut -d' ' -f1)"
  if [ "$got" != "$want" ]; then
    die "$label checksum mismatch
      path: $path
      want: $want
      got:  $got
    PomBase may have reissued this release. Update config/assembly.yaml only
    after confirming the coordinates still match the analysis."
  fi
  note "sha256 ok: $label"
}

verify_sha "$FASTA" "$FASTA_WANT" "genome FASTA"
verify_sha "$GFF" "$GFF_WANT" "annotation GFF3"

# --- FASTA: block-gzip + index ----------------------------------------------
# faidx on the bgzipped file emits .fai and .gzi, both of which the
# BgzipFastaAdapter needs to seek.
log "assembly: $FASTA_NAME"
"$BGZIP" -c "$FASTA" > "$OUT_ASM/pombe.fa.gz"
"$SAMTOOLS" faidx "$OUT_ASM/pombe.fa.gz"
note "sequences in index:"
while IFS=$'\t' read -r name len _; do
  printf '      %-24s %10s bp\n' "$name" "$len"
done < "$OUT_ASM/pombe.fa.gz.fai"

# --- GFF3: block-gzip + tabix ------------------------------------------------
# tabix requires records sorted by position within each sequence, and a stable
# sort matters: PomBase emits gene before mRNA before exon, often all at the
# same start, and a non-stable sort could put a child ahead of its parent.
log "annotation: $GFF_NAME"
if LC_ALL=C sort -k1,1 -k4,4n -k5,5n -c "$GFF" 2>/dev/null; then
  note "upstream GFF3 is already position-sorted; using it unchanged"
  SORTED="$GFF"
  SORT_TMP=""
else
  note "upstream GFF3 is not position-sorted; sorting stably to a temp file"
  SORT_TMP="$BUILD_DIR/pombe.sorted.gff3"
  LC_ALL=C sort -s -k1,1 -k4,4n -k5,5n "$GFF" > "$SORT_TMP"
  SORTED="$SORT_TMP"
fi

"$BGZIP" -c "$SORTED" > "$OUT_ANN/pombe.gff.gz"
"$TABIX" -f -p gff "$OUT_ANN/pombe.gff.gz"
[ -n "$SORT_TMP" ] && rm -f "$SORT_TMP"

note "sequences in GFF index:"
"$TABIX" -l "$OUT_ANN/pombe.gff.gz" | sed 's/^/      /'

# --- report ------------------------------------------------------------------
log "artefacts"
for f in "$OUT_ASM/pombe.fa.gz" "$OUT_ASM/pombe.fa.gz.fai" "$OUT_ASM/pombe.fa.gz.gzi" \
         "$OUT_ANN/pombe.gff.gz" "$OUT_ANN/pombe.gff.gz.tbi"; do
  printf '    %-46s %s\n' "${f#"$REPO_ROOT"/}" "$(du -h "$f" | cut -f1)"
done

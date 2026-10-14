#!/usr/bin/env bash
# Shared helpers for the DIT-HAP JBrowse 2 build.
#
# Sourced, never executed. Provides REPO_ROOT, logging, and resolve_tool().
#
# Tool resolution matters here: bgzip and tabix ship with htslib but are NOT
# always on PATH (e.g. a samtools-only install, or a conda env that is not
# activated). Rather than hard-coding one machine's layout, resolve_tool()
# searches PATH, $CONDA_PREFIX, and the usual conda env directories, and any
# tool can be overridden with an env var of its uppercase name (BGZIP=... ).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export REPO_ROOT

# --- logging -----------------------------------------------------------------
if [ -t 1 ]; then
  _C_DIM=$'\033[2m'; _C_RED=$'\033[31m'; _C_GREEN=$'\033[32m'; _C_OFF=$'\033[0m'
else
  _C_DIM=''; _C_RED=''; _C_GREEN=''; _C_OFF=''
fi

log()  { printf '%s==>%s %s\n' "$_C_GREEN" "$_C_OFF" "$*"; }
note() { printf '%s    %s%s\n' "$_C_DIM" "$*" "$_C_OFF"; }
warn() { printf '%swarn:%s %s\n' "$_C_RED" "$_C_OFF" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$_C_RED" "$_C_OFF" "$*" >&2; exit 1; }

# --- tool resolution ---------------------------------------------------------
# resolve_tool <name> [extra candidate paths...]
# Prints the resolved path, or returns 1. Honours an uppercase env override.
resolve_tool() {
  local name="$1"; shift
  local override
  override="$(printf '%s' "$name" | tr '[:lower:]-' '[:upper:]_')"
  local from_env="${!override:-}"
  if [ -n "$from_env" ]; then
    [ -x "$from_env" ] && { printf '%s\n' "$from_env"; return 0; }
    die "$override is set to '$from_env' but that is not executable"
  fi
  local p
  p="$(command -v "$name" 2>/dev/null || true)"
  if [ -n "$p" ]; then printf '%s\n' "$p"; return 0; fi
  local cand
  for cand in "$@"; do
    if [ -x "$cand" ]; then printf '%s\n' "$cand"; return 0; fi
  done
  for p in \
    "${CONDA_PREFIX:-/nonexistent}/bin/$name" \
    "$HOME"/miniforge3/bin/"$name" \
    "$HOME"/miniconda3/bin/"$name" \
    "$HOME"/miniforge3/envs/*/bin/"$name" \
    "$HOME"/miniconda3/envs/*/bin/"$name" \
    /usr/local/bin/"$name" ; do
    if [ -x "$p" ]; then printf '%s\n' "$p"; return 0; fi
  done
  return 1
}

require_tool() {
  local name="$1"; shift
  local path
  if ! path="$(resolve_tool "$name" "$@")"; then
    die "required tool '$name' not found. Install it (htslib provides bgzip/tabix;
        'conda install -c bioconda htslib') or point $name's env var at it."
  fi
  printf '%s\n' "$path"
}

# --- shared configuration ----------------------------------------------------
# All paths derive from config/assembly.yaml so there is one source of truth.
CONFIG_DIR="$REPO_ROOT/config"
DATA_DIR="$REPO_ROOT/data"
SITE_DIR="$REPO_ROOT/site"
BUILD_DIR="$REPO_ROOT/.build"

# Read a top-level scalar from a config YAML. Requires PyYAML.
cfg() {
  "$(require_tool python3)" -I -c '
import sys, yaml
path, *keys = sys.argv[1:]
node = yaml.safe_load(open(path))
for k in keys:
    node = node[k]
print(node)
' "$CONFIG_DIR/assembly.yaml" "$@"
}

# Canonical chromosome ordering for S. pombe, matching the PomBase FASTA.
# tabix requires features sorted in this order (not lexicographic, which would
# put "III" before "II" and scatter the small sequences unpredictably).
POMBE_CHROMS=(I II III chr_II_telomeric_gap mating_type_region mitochondrial)
export POMBE_CHROMS

mkdir -p "$BUILD_DIR"

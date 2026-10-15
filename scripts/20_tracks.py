#!/usr/bin/env python3
"""Turn the DIT-HAP release tables into bgzipped, tabixed BED files.

Reads config/datasets.yaml and writes, for every dataset listed there, one BED per
quantity under data/tracks/<dataset>/ :

    fitting_results.tsv ->  DR.bed.gz      depletion rate
                            DL.bed.gz      depletion lag
    LFC.tsv             ->  LFC_<tp>.bed.gz

Each row is a single-base feature at the insertion site:

    chrom  start  end  name  score  strand

Three details here are easy to get wrong, and each was settled against the data
rather than assumed:

  * COORDINATES ARE 0-BASED. The pipeline sets Coordinate = R1_Ref_Start + 4 for
    the + strand and R1_Ref_End for the -, both straight out of pysam's
    read.reference_start / read.reference_end, which are 0-based. So the TTAA
    motif occupies [Coordinate-4, Coordinate). Verified by pulling those four
    bases from data/assembly/pombe.fa.gz: 96.2% of sampled rows read TTAA, which
    matches the TTAA share of the Target column, while a 1-based reading scores
    1.3%. BED is 0-based half-open, so the feature is [Coordinate, Coordinate+1)
    -- no +/-1 fudge anywhere in this file.

  * STRAND IS WRITTEN AS THE CHARACTER. The browser needs +/- here; the adapter
    converts it to the number 1/-1 for the renderer.

  * TABIX NEEDS A SORT. Records are sorted by the assembly's own chromosome
    order, not lexicographically -- C locale would put III before II and the
    index would still be self-consistent but the file would be needlessly
    awkward to diff.

Outputs are committed, so this runs only when the upstream analysis changes.

Usage:
    python3 -I scripts/20_tracks.py [--only DATASET] [--emit-tracks]
"""

from __future__ import annotations

import argparse
import csv
import os
import pathlib
import shutil
import statistics
import subprocess
import sys
import tempfile

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Placeholder for a missing value in the release tables. A blank cell means "no
# usable fit for this insertion", which is a real case: DR/DL are empty for a
# handful of rows in every fitting table.
EMPTY = ""


def resolve_tool(name: str) -> str:
    """BGZIP=/path/to/bgzip, else $PATH. Mirrors scripts/lib/common.sh."""
    override = os.environ.get(name.upper())
    if override:
        if not os.access(override, os.X_OK):
            raise SystemExit(f"{name.upper()} is set to {override!r} but that is not executable")
        return override
    found = shutil.which(name)
    if not found:
        raise SystemExit(
            f"required tool {name!r} not found.\n"
            "  htslib provides bgzip and tabix: conda install -c bioconda htslib\n"
            f"  or point {name.upper()} at it, e.g. {name.upper()}=~/miniforge3/envs/bioinformatics/bin/{name}"
        )
    return found


def fmt_score(value: float) -> str:
    """Fixed-point with no trailing noise: 4 dp is far finer than a 160 px axis."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def read_table(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open() as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def write_bed(
    rows: list[tuple[str, int, str, float]],
    chrom_rank: dict[str, int],
    out_base: pathlib.Path,
    bgzip: str,
    tabix: str,
) -> int:
    """Write, block-gzip and index one BED. Returns the compressed size in bytes."""
    rows.sort(key=lambda r: (chrom_rank.get(r[0], len(chrom_rank)), r[1]))

    out_base.parent.mkdir(parents=True, exist_ok=True)
    gz_path = out_base.with_suffix(".bed.gz")

    with tempfile.NamedTemporaryFile("w", suffix=".bed", delete=False) as tmp:
        tmp_path = pathlib.Path(tmp.name)
        for chrom, coord, strand, score in rows:
            # start = coord, end = coord + 1: see the 0-based note in the docstring.
            tmp.write(f"{chrom}\t{coord}\t{coord + 1}\t{coord}{strand}\t{fmt_score(score)}\t{strand}\n")

    try:
        with gz_path.open("wb") as fh:
            subprocess.run([bgzip, "-c", str(tmp_path)], stdout=fh, check=True)
        # -p bed, and -f because the index is rebuilt in place on every run.
        subprocess.run([tabix, "-f", "-p", "bed", str(gz_path)], check=True)
    finally:
        tmp_path.unlink(missing_ok=True)

    return gz_path.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="restrict to a single dataset id")
    ap.add_argument(
        "--emit-tracks",
        action="store_true",
        help="also print the config/tracks.yaml entries for what was written",
    )
    args = ap.parse_args()

    bgzip = resolve_tool("bgzip")
    tabix = resolve_tool("tabix")

    cfg = yaml.safe_load((REPO_ROOT / "config" / "datasets.yaml").read_text())
    asm = yaml.safe_load((REPO_ROOT / "config" / "assembly.yaml").read_text())["assembly"]
    chrom_rank = {name: i for i, name in enumerate(asm["chrom_order"])}

    source = cfg["source"]
    root = pathlib.Path(source["root"])
    release = source["release_subdir"]
    out_root = REPO_ROOT / "data" / "tracks"

    datasets = cfg["datasets"]
    if args.only:
        datasets = [d for d in datasets if d["id"] == args.only]
        if not datasets:
            raise SystemExit(f"no dataset with id {args.only!r} in config/datasets.yaml")

    print(f"{'dataset':<18} {'track':<12} {'points':>9} {'size':>9}   {'range (min .. max)':<24} p01/p50/p99")
    print("-" * 104)

    unknown_chroms: set[str] = set()
    skipped: list[str] = []
    produced: list[dict] = []

    for ds in datasets:
        ds_id, label = ds["id"], ds.get("label", ds["id"])
        src = root / ds_id / release
        if not src.is_dir():
            raise SystemExit(f"{ds_id}: no {release}/ under {root / ds_id}")

        out_dir = out_root / ds_id.lower()
        # Rebuilt from scratch every run. Without this, a quantity that is
        # dropped upstream -- or a time point that stops getting a track -- is
        # left behind on disk, still committed, still counted in the site size,
        # and still named by any stale tracks.yaml entry.
        shutil.rmtree(out_dir, ignore_errors=True)
        quantities: list[tuple[str, list[tuple[str, int, str, float]]]] = []

        if ds.get("fitting", True):
            fitting = src / source["fitting_file"]
            if not fitting.exists():
                raise SystemExit(
                    f"{ds_id}: config/datasets.yaml says fitting: true but {fitting.name} is missing"
                )
            table = read_table(fitting)
            for column in ("DR", "DL"):
                quantities.append(
                    (
                        column,
                        [
                            (r["Chr"], int(r["Coordinate"]), r["Strand"], float(r[column]))
                            for r in table
                            if r.get(column, EMPTY).strip() != EMPTY
                        ],
                    )
                )

        lfc = src / source["lfc_file"]
        if not lfc.exists():
            raise SystemExit(f"{ds_id}: {lfc.name} is missing")
        lfc_rows = read_table(lfc)
        timepoints = [c for c in lfc_rows[0] if c not in ("Chr", "Coordinate", "Strand", "Target")]
        for tp in timepoints:
            rows = [
                (r["Chr"], int(r["Coordinate"]), r["Strand"], float(r[tp]))
                for r in lfc_rows
                if r.get(tp, EMPTY).strip() != EMPTY
            ]
            # The time point the fold changes are measured against has an LFC of
            # exactly zero at every insertion -- YES0 for most datasets, "0h" for
            # LD_haploid, Spikein0 for the control. It is a flat line by
            # construction, not a result, so it gets no track. Tested as "every
            # value identical" rather than "== 0" so a renamed baseline column
            # still gets skipped instead of shipping a blank track.
            if rows and min(v for *_, v in rows) == max(v for *_, v in rows):
                skipped.append(f"{ds_id} {tp} (constant {fmt_score(rows[0][3])})")
                continue
            quantities.append((f"LFC_{tp}", rows))

        for name, rows in quantities:
            unknown_chroms |= {c for c, _, _, _ in rows} - set(chrom_rank)
            size = write_bed(rows, chrom_rank, out_dir / name, bgzip, tabix)
            values = sorted(v for _, _, _, v in rows)
            rel = (out_dir / f"{name}.bed.gz").relative_to(REPO_ROOT)
            produced.append(
                {
                    "dataset": ds_id,
                    "label": label,
                    "track": name,
                    "path": rel.as_posix(),
                    "points": len(rows),
                    "lo": values[0],
                    "hi": values[-1],
                    "p01": percentile(values, 0.01),
                    "p99": percentile(values, 0.99),
                }
            )
            print(
                f"{ds_id:<18} {name:<12} {len(rows):>9,} {size / 1e6:>8.2f}M   "
                f"{values[0]:>+8.3f} .. {values[-1]:>+8.3f}    "
                f"{percentile(values, .01):>+7.2f}/{percentile(values, .5):>+7.2f}/{percentile(values, .99):>+7.2f}"
            )
        print(f"{'':<18} {'':<12} {'':>9}  ({label})")

    if unknown_chroms:
        print(
            f"\nwarning: chromosomes absent from assembly chrom_order: {sorted(unknown_chroms)}\n"
            "         they were written last and will not appear in refNames",
            file=sys.stderr,
        )

    for s in skipped:
        print(f"skipped: {s} -- no track (see the note above the LFC loop)")

    total = sum((REPO_ROOT / p["path"]).stat().st_size for p in produced)
    print(f"\n{len(produced)} tracks, {total / 1e6:.1f} MB compressed, under data/tracks/")

    if args.emit_tracks:
        sys.stdout.write("\n" + emit_tracks(produced))
    return 0


# Axis ranges, one per quantity and SHARED BY EVERY DATASET.
#
# Two rules decide these, and the measurement is in the report above the table:
#
#   * Fixed, never autoscaled. A time point autoscaled to its own data would look
#     equally severe at YES1 and YES4, which is exactly the comparison these
#     tracks exist to support.
#   * Spend the height on where the points are. The LFC distribution is a tight
#     bulk around 0 (p50 ~ -0.05, p99 ~ +0.6) with a long negative tail to -12;
#     a domain of [-12, +2.6] would collapse 99% of the data into a band at the
#     top of the track. -8 keeps p01..p99 well inside, so the tail above the
#     floor is the ~1% that is genuinely extreme.
#
# DR and DL use their full observed range: DR spans only ~3 units and DL's mass
# is at 0 with a tail to 16, so neither needs clipping.
AXIS = {
    "DR": (-2.2, 1.1),
    "DL": (0.0, 16.2),
    "LFC": (-8.0, 3.0),
}

QUANTITY_LABEL = {"DR": "Depletion rate (DR)", "DL": "Depletion lag (DL)"}


def emit_tracks(produced: list[dict]) -> str:
    """Track entries for config/tracks.yaml, matching the BEDs just written."""
    out = [""]
    for p in produced:
        is_lfc = p["track"].startswith("LFC_")
        point = p["track"][4:] if is_lfc else p["track"]
        lo, hi = AXIS["LFC" if is_lfc else p["track"]]
        name = f"Log2 fold change {point}" if is_lfc else QUANTITY_LABEL[p["track"]]
        out.append(
            f"""  - id: {p['dataset'].lower()}_{p['track'].lower()}
    name: {name}
    type: GWASTrack
    category: [{p["label"]}, {"LFC" if is_lfc else "Curve fit"}]
    adapter:
      type: GWASAdapter
      bedGzLocation: {p["path"]}
      index: {{indexType: TBI, location: {p["path"]}.tbi}}
    displays:
      - type: LinearManhattanDisplay
        height: 160
        minScore: {lo:g}
        maxScore: {hi:g}
        renderers:
          LinearManhattanRenderer:
            color: "jexl:get(feature,'strand')==1?'darkred':'darkblue'"
"""
        )
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())

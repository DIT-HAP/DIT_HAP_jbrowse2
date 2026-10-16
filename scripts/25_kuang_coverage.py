#!/usr/bin/env python3
"""Derive binned read-depth BigWigs from the mirrored Kuang 2017 BAMs.

The six Kuang tracks are BAMs, and a BAM is not quantitative signal -- there is
nothing for a MultiQuantitativeTrack to plot. What is quantitative is the depth
of reads over the genome, so this script reduces each BAM to one mean-depth
value per genomic bin and writes that as BigWig -- two files per time point,
one per strand:

    data/external/kuang/coverage/meiosis_<n>h_fwd.bw
    data/external/kuang/coverage/meiosis_<n>h_rev.bw

Those are the sources of two MultiQuantitativeTracks, so the whole meiosis time
course sits in a single lane per direction and can be read across at a glance.
The per-time-point AlignmentsTracks are untouched: those show reads, this shows
how many, and they answer different questions.

Five decisions here were made against the data, not assumed:

  * BIGWIG, not bedGraph or BED. A bedGraph is the obvious output -- it is what
    the binned values are -- and it does not work. Pointed at one, a plain
    wiggle track draws "Zoom in to see features or force load" at whole-genome
    zoom, because 126,317 features over 12.6 Mb trips the density guard; put the
    same six behind a MultiWiggleAdapter and the lane comes up empty, with no
    error in the console, and the tab then crashes outright. BigWig is the
    format MultiWiggleAdapter is built around -- it carries its own zoom levels,
    so a whole-genome view fetches a few thousand reduced points instead of
    758k features. All of that was measured, not reasoned: the bedGraph version
    of this track is what produced the empty lane.

    BigWig also cannot hold the DIT-HAP BEDs at all: two insertions share a
    coordinate and differ only by strand, and BigWig forbids overlapping
    intervals.

  * SPLIT BY STRAND, because a bulk depth curve says nothing about which strand
    the transcripts came from, and in a meiosis time course that is the point:
    antisense transcription at a locus is invisible in the sum. So each BAM is
    reduced twice, once per strand, and each direction becomes its own track --
    the two are read against each other, and twelve rows in one lane puts that
    comparison across the middle of a very tall track.

    Only ONE of the two is measured. `samtools depth` in samtools 1.14 offers
    just `-g` and `-G`, which REMOVE and ADD flags to its default filter
    (UNMAP,SECONDARY,QCFAIL,DUP); there is no "include only these flags" form,
    so the forward strand can be selected directly (`-G 0x10` adds reverse to
    the exclusion set) and the reverse strand cannot. It does not need to be:
    depth is a count of reads covering a position, and every read is on exactly
    one strand, so reverse = total - forward exactly. Measured on hour 0:
    117,497 mapped, 58,753 forward, 58,744 reverse -- 58,753 + 58,744 is
    117,497, to the read. The subtraction is arithmetic, not an approximation,
    and it halves the number of `samtools depth` passes.

  * NORMALISED, not raw. The six libraries are not equally deep -- mapped reads
    run 117,497 (0 h) to 210,499 (8 h), a 1.8x spread. Plotted raw, 8 h would sit
    on top at every locus in the genome, which is the library, not the meiosis.
    The value written is therefore depth per million mapped reads, so the six
    curves are comparable. Both directions share the unsplit denominator, so the
    two still sum to the unsplit value and the six hours stay comparable to each
    other as well. The cost is that the y axis has no absolute unit, which is why
    the track name says what the number is.

  * BINNED to 100 bp. `samtools depth -aa` emits one line per base: 12,631,379
    lines and 161 MB per sample, about a gigabyte across the six. The site has a
    1 GiB published ceiling and the existing data is 194 MB, so per-base is not
    an option. At 100 bp the twelve files total 8.7 MB, and the bin is well
    under the ~1.4 kb of a typical S. pombe gene, so a gene-scale view still has
    several points to read a shape from. BIN_BP below is the one constant to
    change if that trade is ever worth re-making.

  * The denominator is `samtools view -c -F 0x704`, which is UNMAP | SECONDARY |
    QCFAIL | DUP -- the filter `samtools depth` applies by default, read off
    `samtools depth --help` rather than assumed. Matching the numerator's read
    set exactly is the whole point: a denominator that disagreed with depth
    about which reads count would scale the six samples by an error that varies
    with how many of the excluded reads each one happens to have.

    It is also what `-F 0x904` (UNMAP | SECONDARY | SUPPLEMENTARY, the earlier
    form) resolves to on this data -- 117,497 either way on hour 0, because
    these GMAP alignments carry no QCFAIL, DUP or SUPPLEMENTARY records. 0x704
    is used because it is the one that is true by construction rather than by
    coincidence.

`samtools depth` is the tool for the numerator rather than a pileup or bedtools
genomecov because it is the one that ships here, it applies those filters by
default, and it is fast: 2.0 s per pass, two passes per BAM, so the whole stage
is about two minutes.

Requires pyBigWig (`conda install -c bioconda pybigwig`), which the rest of the
build does not need -- this is the only stage that writes a BigWig. The stage
runs under `./build.sh data` only; CI assembles the site and never gets here.

Outputs are committed, so this runs only when the mirrored BAMs change.

Usage:
    python3 -I scripts/25_kuang_coverage.py [--emit-tracks]
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Bin width in bp. See the BINNED note in the docstring.
BIN_BP = 100

# Reads-per-million divisor. See the NORMALISED note in the docstring.
NORMALISE = True

# The external.yaml key these BAMs come from, and the subdirectory of its `dir`
# that the BigWigs are written to. They sit beside the BAMs they were derived
# from rather than under data/tracks/, because data/tracks/ is generated from
# config/datasets.yaml by scripts/20_tracks.py and this is not one of those
# datasets -- it is a reduction of the mirrored third-party bytes.
DATASET = "kuang2017"
OUT_SUBDIR = "coverage"

# `samtools depth`'s default exclusion filter -- UNMAP | SECONDARY | QCFAIL |
# DUP -- read off `samtools depth --help`. The normalisation denominator uses
# the same mask so the numerator and denominator agree on which reads count.
# See the denominator note in the docstring.
DEPTH_EXCL_FLAGS = "0x704"

# FLAG 0x10, the reverse strand. `samtools depth -G` ADDS flags to the exclusion
# set, so `-G 0x10` is the forward-strand pass; there is no "include only" form
# in samtools 1.14, so the reverse strand is derived by subtraction instead.
# See the SPLIT BY STRAND note in the docstring.
REVERSE_FLAG = "0x10"

# The two directions, in the order the tracks are emitted.
STRANDS = [("fwd", "+", "forward"), ("rev", "-", "reverse")]


def resolve_tool(name: str) -> str:
    """SAMTOOLS=/path/to/samtools, else $PATH. Mirrors scripts/lib/common.sh."""
    override = os.environ.get(name.upper())
    if override:
        if not os.access(override, os.X_OK):
            raise SystemExit(f"{name.upper()} is set to {override!r} but that is not executable")
        return override
    found = shutil.which(name)
    if not found:
        raise SystemExit(
            f"required tool {name!r} not found.\n"
            "  samtools comes from htslib: conda install -c bioconda samtools\n"
            f"  or point {name.upper()} at it, e.g. {name.upper()}=~/miniforge3/envs/bioinformatics/bin/{name}"
        )
    return found


def require_pybigwig():
    try:
        import pyBigWig
    except ImportError:
        raise SystemExit(
            "pyBigWig is required to write the coverage BigWigs.\n"
            "  conda install -c bioconda pybigwig\n"
            "  (or: pip install pyBigWig)"
        ) from None
    return pyBigWig


def hour_of(filename: str) -> int:
    """rep2_meiosis_hour_10.sorted.bam -> 10."""
    m = re.search(r"hour_(\d+)", filename)
    if not m:
        raise SystemExit(f"cannot read an hour out of {filename!r}")
    return int(m.group(1))


def source_name(hour: int) -> str:
    """The name the MultiWiggleAdapter shows for this source."""
    return f"{hour} h"


def mapped_reads(samtools: str, bam: pathlib.Path) -> int:
    """Reads `samtools depth` would count. See the denominator note in the docstring."""
    out = subprocess.run(
        [samtools, "view", "-c", "-F", DEPTH_EXCL_FLAGS, str(bam)],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(out.stdout.strip())


def binned_depth(samtools: str, bam: pathlib.Path, chrom_rank: dict[str, int], extra=()):
    """Stream `samtools depth -a` and fold it into (chrom, bin) -> [sum, count].

    Returns the SUM and the POSITION COUNT rather than a mean, because the
    reverse strand is derived as total - forward per bin (see the SPLIT BY
    STRAND note) and means cannot be subtracted -- they would need weighting by
    a count that the caller has thrown away.

    Streamed rather than buffered: the unbinned output is 161 MB per BAM, and
    the whole point of binning is not to hold that in memory.

    `extra` is spliced into the argv, which is how the forward-strand pass is
    selected (`-G 0x10`).
    """
    # key -> [sum of depth, count of positions]. Count rather than assuming
    # BIN_BP: a contig whose length is not a multiple of the bin is short in its
    # final bin, and dividing that one by BIN_BP would understate it.
    acc: dict[tuple[str, int], list[int]] = {}

    # `-aa`, not `-a`. `-a` means "all positions of a reference that has any
    # read at all", so a sequence whose reads are all on the reverse strand is
    # dropped from the forward pass ENTIRELY -- and then the two passes no
    # longer agree on which bins exist and the subtraction below is meaningless.
    # Measured on hour 0: `-a` gives 12,631,379 lines unfiltered but 12,611,251
    # with `-G 0x10`, while `-aa` gives 12,631,379 either way. That 20,128-line
    # gap is the whole reason for the bin-set assertion in main().
    proc = subprocess.Popen(
        [samtools, "depth", "-aa", *extra, str(bam)],
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1 << 20,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        chrom, pos, depth = line.split()
        key = (chrom, (int(pos) - 1) // BIN_BP)
        slot = acc.get(key)
        if slot is None:
            acc[key] = [int(depth), 1]
        else:
            slot[0] += int(depth)
            slot[1] += 1
    if proc.wait() != 0:
        raise SystemExit(f"samtools depth failed on {bam}")

    # Sorted by the assembly's own chromosome order, not lexicographically --
    # BigWig requires entries in the header's order, and C-locale order would
    # put III before II. Same reason as scripts/20_tracks.py.
    rows = [
        (chrom, b * BIN_BP, b * BIN_BP + n, total, n)
        for (chrom, b), (total, n) in acc.items()
    ]
    rows.sort(key=lambda r: (chrom_rank.get(r[0], len(chrom_rank)), r[1]))
    return rows


def write_bigwig(rows, out_path: pathlib.Path, header: list[tuple[str, int]], pyBigWig) -> int:
    """Write one BigWig. Returns its size in bytes."""
    bw = pyBigWig.open(str(out_path), "w")
    try:
        # maxZooms: the reduction levels a whole-genome view reads instead of the
        # full-resolution entries. That is the whole reason this is a BigWig.
        bw.addHeader(header, maxZooms=10)
        bw.addEntries(
            [r[0] for r in rows],
            [r[1] for r in rows],
            ends=[r[2] for r in rows],
            values=[r[3] for r in rows],
        )
    finally:
        bw.close()
    return out_path.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--emit-tracks",
        action="store_true",
        help="also print the config/tracks.yaml entry for what was written",
    )
    args = ap.parse_args()

    samtools = resolve_tool("samtools")
    pyBigWig = require_pybigwig()

    ext = yaml.safe_load((REPO_ROOT / "config" / "external.yaml").read_text())["external"]
    ds = ext[DATASET]
    asm = yaml.safe_load((REPO_ROOT / "config" / "assembly.yaml").read_text())["assembly"]

    # Chromosome lengths and order both come from the built reference layer, not
    # from a literal here: a BigWig whose header disagrees with the assembly
    # renders nothing, and the failure is silent.
    fai = REPO_ROOT / "data" / "assembly" / "pombe.fa.gz.fai"
    if not fai.exists():
        raise SystemExit(f"missing {fai}. Run ./build.sh data.")
    lengths = {
        line.split("\t")[0]: int(line.split("\t")[1])
        for line in fai.read_text().splitlines()
        if line.strip()
    }
    order = [c for c in asm["chrom_order"] if c in lengths]
    chrom_rank = {name: i for i, name in enumerate(order)}
    header = [(c, lengths[c]) for c in order]

    bam_dir = REPO_ROOT / "data" / "external" / ds["dir"]
    out_dir = bam_dir / OUT_SUBDIR

    # Rebuilt from scratch, like scripts/20_tracks.py: a time point dropped
    # upstream would otherwise be left behind on disk, still committed, still
    # published, and still named by a stale tracks.yaml entry.
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    bams = sorted(
        (bam_dir / f["name"] for f in ds["files"] if f["name"].endswith(".bam")),
        key=lambda b: hour_of(b.name),
    )
    if not bams:
        raise SystemExit(f"config/external.yaml lists no BAMs for {DATASET}")
    missing = [b.name for b in bams if not b.exists()]
    if missing:
        raise SystemExit(f"missing BAMs under {bam_dir}: {missing}\nRun ./build.sh data.")

    unit = "per million reads" if NORMALISE else "raw"
    print(f"bin {BIN_BP} bp, {unit}")
    print(f"{'file':<24} {'bins':>9} {'size':>8} {'mean depth':>11} "
          f"{'value range':>22}")
    print("-" * 82)

    produced: list[dict] = []
    for bam in bams:
        hour = hour_of(bam.name)
        reads = mapped_reads(samtools, bam)

        # Two passes over the same BAM: everything, and forward only. Reverse is
        # the difference. See the SPLIT BY STRAND note in the docstring for why
        # it is done this way rather than with a second filter.
        every = binned_depth(samtools, bam, chrom_rank)
        forward = binned_depth(samtools, bam, chrom_rank, extra=("-G", REVERSE_FLAG))

        # Same bins, same order, same counts. Asserted rather than trusted: the
        # subtraction below turns a disagreement into plausible-looking numbers
        # rather than into an error.
        if [(c, s, e, n) for c, s, e, _, n in every] != \
           [(c, s, e, n) for c, s, e, _, n in forward]:
            raise SystemExit(f"the two depth passes disagree on bins for {bam.name}")

        per_strand = {
            "fwd": [f for *_, f, _ in forward],
            "rev": [t - f for (_, _, _, t, _), (_, _, _, f, _) in zip(every, forward)],
        }

        scale = 1e6 / reads if NORMALISE else 1.0
        print(f"{bam.name}  ({reads:,} reads passing the depth filter)")

        for key, sign, word in STRANDS:
            # Divided by the bin's own position count, not by BIN_BP: the last
            # bin of a contig is short, and by BIN_BP it would read low.
            rows = [
                (c, s, e, depth * scale / n)
                for (c, s, e, _, n), depth in zip(every, per_strand[key])
            ]
            stem = f"meiosis_{hour}h_{key}"
            size = write_bigwig(rows, out_dir / f"{stem}.bw", header, pyBigWig)
            values = [v for *_, v in rows]

            produced.append(
                {
                    "hour": hour,
                    "key": key,
                    "sign": sign,
                    "word": word,
                    "stem": stem,
                    "source": source_name(hour),
                    "path": (out_dir / f"{stem}.bw").relative_to(REPO_ROOT).as_posix(),
                    "bins": len(rows),
                    "lo": min(values),
                    "hi": max(values),
                }
            )
            # The unnormalised mean is the useful diagnostic -- it is what tells
            # a reader whether the six libraries were comparable to begin with,
            # and whether the two strands are balanced within one.
            raw_mean = sum(v / scale for v in values) / len(values)
            print(
                f"  {stem + '.bw':<24} {len(rows):>7,} {size / 1e6:>7.2f}M "
                f"{raw_mean:>10.2f}x "
                f"{min(values):>10.1f} .. {max(values):<10.1f}"
            )

    size = sum((REPO_ROOT / p["path"]).stat().st_size for p in produced)
    lo = min(p["lo"] for p in produced)
    hi = max(p["hi"] for p in produced)
    print(f"\n{len(produced)} sources, {size / 1e6:.1f} MB, "
          f"under {out_dir.relative_to(REPO_ROOT)}/")
    print(f"value range across all sources: {lo:.1f} .. {hi:.1f}")

    if args.emit_tracks:
        sys.stdout.write("\n" + emit_tracks(produced))
    return 0


# The axis autoscales, and it has to. This track carried a fixed 0..300 until
# the alternative was measured against it.
#
# No fixed axis works here. The distribution is too wide: across all twelve
# files the median bin is 0.1, the 90th percentile ~80, and the maximum 129,470
# -- a pileup of reads inside 200 bp of chromosome I near isp3. An axis that
# covers the pileup compresses every gene in the genome into the bottom pixel,
# and an axis set to the bulk clips the pileup; at 300 about 2.5% of bins
# clamped to the top edge. Whichever end is chosen, one zoom or the other is
# unreadable.
#
# `localsd`, not the default `local`. Both are autoscale; the difference is
# whether the domain comes from the region's min/max or from its mean +/- 3
# sigma. `local` uses the extremes, so the isp3 pileup sets the domain on its
# own and both tracks collapse to a flat line at chromosome scale -- measured at
# [0-130000] forward and [0-20000] reverse. At 3 sigma the pileup clips and the
# time course stays legible: [0-1300] and [0-350] at the same zoom, [0-200] and
# [0-1200] at 20 kb.
#
# The floor is 0 either way, because depth cannot be negative and the display
# forces zero into a linear domain. That matters here in a way it did not for
# the unsplit track: the median bin IS 0.1, so most of the genome is genuinely
# at the axis floor.
#
# The one thing autoscaling gives up: the two strand tracks now scale
# INDEPENDENTLY, where a shared fixed axis held them together. See the long note
# in config/tracks.yaml, which is where that tradeoff is written down.
#
# N IS 5, not the schema default of 3. At 3 sigma the ceiling lands below the top
# of the data -- at I:3,090,000-3,110,000 the forward track's mean is 24.0 and sd
# 53.4 against a max of 294, so mean+3sd is 184 and 3.81% of bins clear it -- and
# a bar clamped at the ceiling cannot be told from one twice its height. The
# renderer marks the clamped ones with a red clip marker, which is how this was
# noticed. At 5 sigma the ceiling is 291, the tallest bar in that window lands
# just inside it, and the clipping is gone. 6 would also clear it (345) but
# leaves a quarter of the track empty that every other bar pays for.
#
# The cost is at the other end: the ceiling scales linearly with N, so
# whole-chromosome zoom draws everything 3/5 the height it did. That view was
# already compressed. `local` would frame every gene-scale window perfectly, but
# it fails hard instead of softly -- one pileup bin in view blanks the track.
AUTOSCALE = "localsd"
NUM_STD_DEV = 5


def emit_tracks(produced: list[dict]) -> str:
    """The config/tracks.yaml entries for the BigWigs just written.

    One track per direction. They are separate rather than twelve sources of
    one track because the two are read against each other, not as a series: the
    question is whether a locus is transcribed from the strand it should be,
    and twelve rows in one lane makes that a comparison across the middle of a
    tall track.
    """
    blocks = []
    for key, sign, word in STRANDS:
        subs = "\n".join(
            f"""        - type: BigWigAdapter
          source: "{p["source"]}"
          color: "{COLORS[i % len(COLORS)]}"
          bigWigLocation: {p["path"]}"""
            for i, p in enumerate(p for p in produced if p["key"] == key)
        )
        blocks.append(f"""  - id: kuang2017_coverage_{key}
    name: Read depth over meiosis, {word} strand ({sign}), per million reads — Kuang et al. 2017
    type: MultiQuantitativeTrack
    category: [Kuang et al. 2017, Coverage]
    metadata: *kuang_metadata
    adapter:
      type: MultiWiggleAdapter
      subadapters:
{subs}
    displays:
      - type: MultiLinearWiggleDisplay
        height: 300
        autoscale: {AUTOSCALE}
        numStdDev: {NUM_STD_DEV}
""")
    return "\n".join(blocks)


# One colour per time point, in time order, so the ramp itself reads as the time
# course. Indexed by position, so both directions get the same colour for the
# same hour.
#
# ColorBrewer's Blues, sampled over 0.35..1.0 rather than its full range: the
# ramp's own light end (#f7fbff) is invisible on the white page these tracks are
# drawn on, so the first step is pulled down to the palest blue that still reads
# as a bar. One hue ordered by lightness, so the sequence is unambiguous and
# survives a greyscale print.
#
# Light -> dark with time: 0 h is the palest, 10 h the darkest. That is the
# REVERSE of the viridis ramp these replaced, which ran dark (0 h) -> light
# (10 h). The data is the same either way; the reading is not, so it is written
# down rather than left to be noticed.
#
# The colour identifies the SOURCE, not the value. It is a literal string on each
# subadapter -- `color: "#440154"` -- so every bar in the 0 h row is that purple
# whether it is one read deep or a thousand; height is what carries depth. A
# wiggle colour can instead be a callback over features (`getColorCallback` tests
# `color.isCallback`), which is how a track is coloured BY value. This is
# deliberately not that: on a coverage track a value-coloured ramp would be
# encoding depth a second time, in a channel already occupied by height.
#
# So the colour is redundant with the row label and the row order, both of which
# already say which hour is which. That redundancy is the point -- it is what
# lets the time course be read as a ramp at a glance, and the same hour be
# matched across the two strand tracks, without reading twelve labels.
COLORS = ["#a6cee4", "#74b3d8", "#4695c8", "#2373b6", "#09529d", "#08306b"]


if __name__ == "__main__":
    raise SystemExit(main())

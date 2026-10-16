# DIT-HAP genome browser

A JBrowse 2 genome browser for the DIT-HAP insertion time-series analysis,
served as GitHub Pages and intended as supplementary material for the paper.

It visualises, per genomic insertion site:

| quantity | meaning |
| --- | --- |
| `DR` | depletion rate — more negative means more severe depletion |
| `DL` | depletion lag |
| `LFC` | log2 fold change at each time point |

`DR` and `DL` are the parameters renamed from the pipeline's earlier `um` and
`lam`; note that `DR` runs in the **opposite direction** to the old `um`.

Insertions are drawn with the GWAS scatter renderer: vertical position is the
value, colour is the insertion's strand (dark red = forward, dark blue =
reverse).

## Viewing the site

<https://dit-hap.github.io/DIT_HAP_jbrowse2/>

The browser opens on a whole-genome view with every sequence laid out side by
side. Type a gene name into the search box (`cdc2`, `tlh1`, `SPNCRNA.2000`) and
pick a result, or use the location box to jump straight to coordinates
(`I:99833..101431`) or a chromosome (`II`). Tracks are added from the track
selector, grouped by dataset.

## Building it locally

```bash
./build.sh data     # reference layer + tracks  (slow; outputs are committed)
./build.sh site     # assemble site/            (fast; runs on every deploy)
./build.sh serve    # serve site/ on :8080
./build.sh clean
```

`data` needs `bgzip`, `tabix`, `samtools` and `curl` on `PATH`. If the htslib
tools live in a conda environment that is not activated, point at them
explicitly:

```bash
BGZIP=~/miniforge3/envs/bioinformatics/bin/bgzip \
TABIX=~/miniforge3/envs/bioinformatics/bin/tabix \
./build.sh data
```

`site` needs the [JBrowse CLI](https://www.npmjs.com/package/@jbrowse/cli)
(`npm install -g @jbrowse/cli@4.3.0`) and PyYAML.

`./build.sh serve` runs `scripts/serve.py`, **not** `python3 -m http.server`.
That distinction matters: `http.server` ignores the `Range` header and answers
`200` with the whole body, but JBrowse reads every indexed file by byte range.
Handed the whole file, it slices from offset 0 and lands mid-BGZF-block, and the
Sequence track fails with:

```
problem decompressing block: not a valid bgzf or gzip block
```

Feature tracks usually survive that, because their first reads are served from
offset 0 anyway — the failure needs a read placed away from offset 0, such as a
locus deep inside a chromosome. GitHub Pages honours `Range`, so this only ever
affects local preview.

### Checking a build

```bash
python3 scripts/check_site.py --url http://127.0.0.1:8080/
```

This drives a headless browser and asserts four things a build can otherwise
get wrong silently:

1. that JBrowse **accepted** the default session — a malformed one is discarded
   in favour of an empty default with no visible error;
2. that every configured track actually renders;
3. that the Sequence track reads cleanly at a locus deep in a chromosome, which
   is what catches a preview server that ignores `Range`;
4. that the search box resolves a gene name. It reads that name out of the
   annotation itself, so a missing or stale trix index fails the check.

It needs `playwright`; CI does not run it.

Note that `document.body.innerText` and a `canvas` count are **not** reliable
probes for the Sequence track: it draws bases as DOM elements, not into a
canvas, and a stale `Loading` node stays in the tree after the track has
rendered. Screenshots and the track container's own text are.

## Layout

```
config/          the only place tracks, sessions and app settings are defined
  assembly.yaml    reference assembly + upstream provenance and checksums
  datasets.yaml    which analysis datasets are published, and where they live
  external.yaml    third-party datasets mirrored into data/external/
  tracks.yaml      track registry
  session.yaml     the default view, plus global plugin configuration
data/            built artefacts, committed (the deployed site never
                 reaches back to PomBase)
  assembly/        bgzipped FASTA + .fai + .gzi
  annotation/      bgzipped GFF3 + .tbi
  tracks/          one BED (bgzip + tabix) per dataset / quantity / time point
  external/        mirrored third-party datasets (the Kuang et al. BAMs),
                   plus external/kuang/coverage/ -- BigWigs derived from them
scripts/
  lib/common.sh    tool resolution and logging
  lib/yamlspec.py  config/*.yaml -> site/config.json
  10_assembly.sh   reference layer
  15_external.sh   mirror third-party datasets
  20_tracks.py     release tables -> data/tracks/*.bed.gz
  25_kuang_coverage.py  mirrored BAMs -> data/external/kuang/coverage/*.bw
  serve.py         local preview server (Range-capable; see below)
  check_site.py    headless end-to-end check
  99_assemble_site.sh  site/, including the search index
vendor/plugins/  vendored third-party JBrowse plugins, byte-for-byte upstream
plugins/         our own JBrowse plugins (plain JS, no build step)
site/            build artefact, gitignored
  trix/            search index, rebuilt on every assembly
```

`site/config.json` is generated. Editing it by hand has no effect, because the
next build overwrites it — change `config/*.yaml` instead. Two steps write it:
`yamlspec.py` lays down the assemblies, tracks and session, then
`jbrowse text-index` appends `aggregateTextSearchAdapters`. Letting the CLI own
that one key is deliberate; it owns the schema, so a JBrowse upgrade cannot
leave this repository maintaining a stale copy of it.

## Data flow

```
DIT_HAP_snakemake  ──┐
  release/insertion_level/*.tsv
                     ├─►  scripts/20_tracks.py ─►  data/tracks/*.bed.gz (+ .tbi)
PomBase release    ──┘                           data/assembly/, data/annotation/
PomBase external   ──►  scripts/15_external.sh ─►  data/external/kuang/*.bam (+ .bai)
                                                 │            │
                                                 │            └─► scripts/25_kuang_coverage.py
                                                 │                     │
                                                 │                     └─► coverage/*.bw
                                                 │
                            config/*.yaml  ──────┼─►  scripts/lib/yamlspec.py
                                                 │            │
                                                 │            ├─► site/config.json
                                                 │            │
                                                 └─► jbrowse text-index ─► site/trix/
                                                              (also appends the search
                                                               adapter to config.json)
```

`./build.sh data` runs every `scripts/NN_*` stage in filename order; `.sh` stages
run under bash and `.py` stages under python3 (`-I`, so nothing in the current
directory can shadow the imports).

## Tracks

One track per dataset, quantity and time point — 29 of them — plus the reference
gene track, six mirrored external tracks and two coverage time-series tracks for
those: 38 in all. Every one is added from the track selector, grouped first by
dataset, then by `Curve fit` (the DR and DL parameters) or `LFC` (per time
point). The DIT-HAP tracks are built from the release tables:

| source | column | track |
| --- | --- | --- |
| `fitting_results.tsv` | `DR` | Depletion rate |
| `fitting_results.tsv` | `DL` | Depletion lag |
| `LFC.tsv` | one per time point | Log2 fold change *time point* |

The time point the fold changes are measured against (`YES0` for most datasets,
`0h` for LD haploid) has an LFC of exactly zero at every insertion. It is a flat
line by construction, not a result, so it gets no track; `20_tracks.py` drops any
time point whose values are all identical rather than naming the baseline column,
so a renamed baseline is still handled.

Datasets are listed in `config/datasets.yaml`. `HD_diploid` is deliberately
absent: it appears in `DIT_HAP_analysis/config/datasets.yaml` but has no
`release/` tree on disk. `Spikein` was published until it was dropped — it was a
calibration control, so it carried no result of its own, and it was the single
widest LFC outlier in the release, meaning it alone set the shared LFC axis for
every other dataset.

Four details below were settled by measurement rather than assumption, because
each fails silently:

- **Coordinates are 0-based.** The pipeline writes `Coordinate = R1_Ref_Start + 4`
  for the `+` strand and `R1_Ref_End` for the `-`, both straight out of pysam. So
  the `TTAA` motif occupies `[Coordinate-4, Coordinate)`. Pulling those four bases
  out of the shipped FASTA returns `TTAA` for 96.2% of sampled rows — the same
  share as the `Target` column — where a 1-based reading scores 1.3%.
- **`minScore`/`maxScore` replace the data-derived domain; they do not clamp it.**
  The display builds its domain in `getNiceDomain({domain, bounds})`, where
  `domain` is the visible region's stats and `bounds` is `[minScore, maxScore]`,
  and a bound wins outright: `if (r !== undefined) l = r`. d3's `.nice()` then
  rounds the result outward, which is where a configured `-12..3` becomes the
  `[-12, 4]` drawn on screen. Leave both unset and the domain follows the data
  instead — the schema defaults are `Number.MIN_VALUE`/`MAX_VALUE` and
  `getNiceDomain` treats those two sentinels as "unset". The ranges here are
  fixed and shared across datasets so time points and datasets can be compared
  by eye: LFC `-12..3`, DL `-1..16`, DR `-2..0.5`.
- **`strand` reaches the renderer as the number `1`/`-1`**, not `'+'`/`'-'`, so
  the colour expression compares against numbers. Comparing against characters
  paints every point the same colour, with no error.
- **These tracks have no density guard.** Unlike the feature tracks, the vendored
  `LinearManhattanDisplay` overrides `regionTooLarge` to return `false`, so they
  never show "Zoom in to see features" and draw at any zoom. At whole-genome zoom
  each enabled track downloads its entire BED — 18 tracks at once moved 24.9 MB —
  where at gene scale the same tracks fetch a few kB each.

### The strand key on the manhattan tracks

Every DIT-HAP track colours its points by insertion strand:

```yaml
renderers:
  LinearManhattanRenderer:
    color: "jexl:get(feature,'strand')==1?'darkred':'darkblue'"
```

and nothing on screen said which colour was which. The renderer cannot say it: its
config schema has exactly one slot, `color`, and the vendored bundle contains no
`fillText` call at all.

So `plugins/gwas-chrome.js` draws a small `+ forward` / `− reverse` key in the
top-right corner of every manhattan track, by wrapping the display's React
component. That wrap is safe because the vendored plugin registers **core's own**
`LinearWiggleDisplayReactComponent` for this display — it reaches into the
`WigglePlugin` and takes the component out — so what gets wrapped is the component
every wiggle track in the app renders through, not something the GWAS plugin owns.
The wrapper adds one positioned `div` around it and a sibling for the key; it does
not resize or restyle anything.

`+` is darkred because the BED parser in the app bundle reads the strand column as
`"+"===d?1:"-"===d?-1` and the jexl above tests `==1`. That was checked rather than
reasoned about: filtering the shipped BED down to its `+` rows and rendering that
file alone paints a solid red track, and the `-` rows paint a solid blue one. A key
that is backwards is worse than no key.

The key is `pointer-events: none`, so it does not swallow the mousemove driving the
feature tooltip in the corner it sits in.

### Reading a time course: the multi-source tracks

The per-time-point tracks show one time point each. The strand split on the Kuang
coverage — [below](#coverage-over-the-kuang-time-course) — is the one place a
multi-source track earns its keep, and it is two tracks there rather than one for
the reason given in that section.

One `MultiQuantitativeTrack` per dataset, holding every LFC time point of that
dataset as a source, was built and then **removed by decision** once the two forms
had been compared. It is recorded here because it was measurable, not because it
might come back.

It read the **same BED files** as the manhattan tracks, as `BedTabixAdapter`
subadapters of a `MultiWiggleAdapter`, so the two forms could not drift apart —
`source` and `color` are not slots on `BedTabixAdapter`: they are read off the
subadapter config by `MultiWiggleAdapter.getSources()`, which spreads every key
except `type` into the source object the display gets. That works only because
`subadapters` is a `frozen` slot, the config staying a plain object rather than
becoming an MST model with a schema to check it against, and it means a
`BedTabixAdapter` is being used where a wiggle adapter is expected, tolerated only
because `MultiWiggleAdapter` never inspects its subadapters' types.

What it looked like, since that is the part worth keeping: an LFC row is one
insertion with one score, drawn by the multi-source form as a line through points
that are one base wide at 100 bp spacing, so the lane reads as a picket fence of
vertical strokes. The manhattan tracks draw the same numbers as points, where a
dense locus reads as density and an outlier reads as an outlier.

There is no BigWig alternative either, because there cannot be one: two insertions
share a coordinate and differ only by strand (`I 12726 12727 12726+ … +` and
`… 12726- … -`), and BigWig forbids overlapping intervals, so the strand split the
BED carries is unrepresentable in it.

### Coverage over the Kuang time course

The six Kuang tracks are BAMs, and a BAM is not quantitative signal, so there is
nothing for a `MultiQuantitativeTrack` to plot directly. `scripts/25_kuang_coverage.py`
reduces each BAM to one mean-depth value per 100 bp bin and writes **twelve**
BigWigs — one per time point per strand — which become the six sources of each of
**two** tracks, `Read depth over meiosis, forward strand (+)` and `reverse strand (-)`.

Two tracks rather than one holding all twelve sources, because the two are read
*against* each other rather than as a series: the question a strand split answers is
whether a locus is transcribed from the strand it should be, and in one twelve-row
lane that comparison is across the middle of a very tall track. The same ramp is
shared, so 2 h is the same colour in both and a bar can be compared with the one in
the track above it without consulting a key.

The ramp is ColorBrewer's Blues, palest at 0 h and darkest at 10 h, so it reads as
the time course by lightness alone and survives a greyscale print. It is sampled
over 0.35..1.0 of the range rather than all of it, because the ramp's own light end
(`#f7fbff`) is invisible against this page. Note the direction is the reverse of the
viridis ramp these tracks first shipped with, which ran dark at 0 h to light at
10 h — same data either way, opposite reading.

Five things there were settled against the data:

- **BigWig, not bedGraph.** The bedGraph version is what this started as, and it
  does not render. A single bedGraph behind a plain wiggle track shows "Zoom in to
  see features or force load" at whole-genome zoom, because 126,317 features over
  12.6 Mb trips the density guard; the same six behind a `MultiWiggleAdapter` give
  an **empty lane with nothing in the console**, and the tab then crashes. BigWig
  carries its own zoom levels, so a whole-genome view reads a few thousand reduced
  points instead of 758k features.
- **Split by strand.** A bulk depth curve says nothing about which strand the
  transcripts came from, and in a meiosis time course that is the point — antisense
  transcription at a locus is invisible in the sum. Only one of the two directions
  is measured: `samtools depth` in samtools 1.14 offers only `-g` and `-G`, which
  *remove* and *add* flags to its default filter, with no "include only" form. So
  the forward strand is selected directly (`-G 0x10`) and the reverse strand is
  `total − forward`. That is exact arithmetic, not an approximation: depth counts
  reads over a position and every read is on exactly one strand, so on hour 0
  58,753 + 58,744 = 117,497, to the read. It also halves the number of `samtools
  depth` passes.
- **Normalised to reads per million.** The six libraries are not equally deep —
  mapped reads run 117,497 (0 h) to 210,499 (8 h), a 1.8× spread. Plotted raw, 8 h
  would sit on top at every locus in the genome, which is the library, not the
  meiosis. Both directions share the total's denominator, so the two still sum to
  the unsplit value and the six hours stay comparable.
- **Binned to 100 bp.** `samtools depth -aa` emits one line per base — 12.6 M lines
  and 161 MB per sample, about a gigabyte across the six. At 100 bp the twelve files
  total 9 MB, and the bin is well under the ~1.4 kb of a typical S. pombe gene.
- **The axis autoscales, and has to.** Across all twelve files the median bin is
  0.1, the 90th percentile ~80, and the maximum 129,470 — a pileup of reads inside
  200 bp of chromosome I near `isp3`. No fixed axis serves that: one covering the
  pileup compresses every gene into the bottom pixel, one set to the bulk clips it.
  These tracks carried a fixed `0..300` until the alternative was measured against
  it. They now carry `autoscale: localsd` — mean ± Nσ over the visible region, not
  the default `local`, which uses the region's min/max and so lets the pileup set
  the domain on its own. Measured on the built site with both tracks on, at 20 kb:
  fwd `[0-200]`, rev `[0-1200]`. At whole-chromosome: `[0-1300]` and `[0-350]`,
  where plain `local` gave `[0-130000]` and `[0-20000]` — a flat line at both.

  **N is 5, not the default 3**, and that was raised for the reason these tracks
  exist. At 3σ the ceiling lands *below* the top of the data — at
  `I:3,090,000-3,110,000` the forward track's mean is 24.0 and σ is 53.4 against a
  max of 294, so mean+3σ is 184 and 3.81 % of bins clear it — and the renderer
  marks every bar clamped at the ceiling with a 1px red *clip marker*. A clipped
  bar cannot be told from one twice its height, and the tallest bars are the ones
  being read. At 5σ the ceiling is 291, the tallest bar in that window lands just
  inside it, and the clip markers there go from 674 pixels to zero. 6σ would also
  clear it (345) but leaves a quarter of the track empty that every other bar pays
  for. The cost is at the other end: the ceiling scales linearly with N, so
  whole-chromosome zoom draws everything 3/5 the height it did — `[0-2200]` where
  it was `[0-1300]`, compressed but still legible. `local` would frame every
  gene-scale window perfectly and is tempting for that reason, but it fails *hard*
  rather than softly: one pileup bin in view blanks the whole track.

  The other cost is that the two strand tracks scale independently, where the
  shared fixed axis held them together; each prints its own `[0-N]` label for that
  reason.

One more was found by failing rather than by reasoning: the two passes must run
`samtools depth -aa`, not `-a`. `-a` means "all positions of a reference that has
any read at all", so a sequence whose reads are all on the reverse strand drops out
of the forward pass *entirely*. Measured on hour 0, `-a` gives 12,631,379 lines
unfiltered but 12,611,251 with `-G 0x10`, while `-aa` gives 12,631,379 either way.
The script therefore asserts that the two passes agree on the bin set before
subtracting, because otherwise the difference silently produces plausible-looking
numbers instead of an error.

The denominator is `samtools view -c -F 0x704` (`UNMAP|SECONDARY|QCFAIL|DUP`),
which is the filter `samtools depth` applies by default, read off its own `--help`
— so the numerator and denominator cannot disagree about which reads count. It is
also what the earlier `-F 0x904` form resolved to on this data, 117,497 either way,
because these GMAP alignments carry no QCFAIL, DUP or SUPPLEMENTARY records.

This is the only stage that needs `pyBigWig` (`conda install -c bioconda pybigwig`),
and it runs under `./build.sh data` only; CI assembles the site and never gets here.

### External tracks: Kuang et al. 2017

Six `AlignmentsTrack`s of the published PacBio IsoSeq meiosis time course
(PMID 27856494, GSE79802), **mirrored** from PomBase into `data/external/kuang/`
by `scripts/15_external.sh`.

They were linked until the round trip proved too slow to browse. PomBase serves
at **70–130 kB/s**, so a single 1 MB range read measured 8–14 s, and an
`AlignmentsTrack` issues several per view. Serving the same bytes from the site's
own origin removes that wait. Measured A/B, one track over a 25 kb window at
`I:2000000-2025000`: **2.7–3.0 s** mirrored, against **57.5 s** for the one
remote track that finished painting at all — the other two were still blank
after 90 s.

Mirroring costs 164 MiB of repository (plus their indexes), the largest single
file being 35 MiB — comfortably under Git's 100 MiB block — and takes the
published site from 70 MB to 234 MB, well inside the 1 GiB Pages limit.

Mirroring means redistributing someone else's data, so the attribution is not
decoration. `metadata` on every track carries the citation, study accession,
sample, growth conditions and source URL into JBrowse's "About this track", and
`config/external.yaml` records the upstream URL, byte count and SHA-256 of each
file. The download stage verifies those checksums and is idempotent: a file
already present and matching is left alone, so re-running `./build.sh data`
re-fetches nothing.

Two things were established by probing rather than taken from the paper. Only
**`rep2`** exists (every `rep1` filename is a 404) and the series is 0, 2, 4, 6,
8 and 10 h. And the BAM headers name the same six sequences as our assembly at
the same lengths, with one exception: they say `mitochondrial` is 19,431 bp where
our FASTA says 19,433, so alignments on that one 19 kb sequence can be off by two
bases. The three chromosomes are exact.

Reads are coloured by strand by default (pink forward, lavender reverse) rather
than JBrowse's flat grey, because on IsoSeq data the strand is the thing a reader
is usually following. That is one config slot, `colorBy: {type: strand}`, set on
the pileup sub-display — but it cannot be written on its own. Every display
config schema is declared with `explicitIdentifier: 'displayId'`, so a nested
display block is an MST `types.identifier` and the config tree will not build
without it; a `pileupDisplay` block carrying only `colorBy` throws, and reports
itself as `Reaction[AlignmentsDisplayConfig] Error: no containing track found`
from an autorun on the half-built tree. The value is ignored — the parent
overwrites it with `${parentDisplayId}_${lowerPanelType}_xyz` — so all six tracks
share one string. The full note is in `config/tracks.yaml`.

These tracks **do** have a zoom guard, unlike the manhattan ones, because it is a
different mechanism: `fetchSizeLimit` (1 MB by default) rejects a single fetch
that returns too many records, where `regionTooLarge` refuses a zoom level by
feature density. The libraries are deep, so at a 25 kb window the 10 h track asks
for 5.29 MB and offers "zoom in or force load" instead of rendering. That is
stock JBrowse behaviour, and FORCE LOAD resolves it. Mirroring does not remove
that guard — it counts records, not seconds — but the same 5.29 MB now arrives
over loopback or a CDN instead of a 100 kB/s link.

## Gene search

The search box resolves gene names and systematic IDs. Of the annotation's
12,685 genes, 4,529 carry a `Name` (`tlh1`, `cdc2`) and the remaining 8,156
only an `ID` (`SPNCRNA.2000`), so the index is built over `Name,ID` and both
kinds are findable — searching `cdc2` lists `cdc2`, `cdc20`, `cdc22`…, and
picking one navigates to it.

The index is built on every `./build.sh site`, from the committed
`data/annotation/pombe.gff.gz`, rather than being committed itself. It is 3.6 MB
of pure derivative, it takes about a second to rebuild, and building it each
time keeps it in step with the GFF3 — a committed copy would quietly go stale
the next time the annotation is updated.

Search is also pass 4 of `scripts/check_site.py`, because its failure mode is
invisible: with no index the box still renders and typing still opens a
dropdown, it just never matches.

## Track labels

Track names are drawn in `offset` mode rather than JBrowse's `overlapping`
default. Measured at a 1400px viewport with one gene track, the label sits at
y=171 and is 15 px tall. `offset` starts the track body at y=193, below it;
`overlapping` starts it at y=166, so the label is painted over the top of the
track's own content — which is where the leftmost gene names sit.

It is set in `config/session.yaml` under `configuration:`, which becomes
config.json's top-level `configuration` block. That placement matters: the view
resolves `view.trackLabels || <plugin config>`, so setting it there covers every
view — including views inside a session a returning visitor already has stored
in IndexedDB, which no change to `defaultSession` would reach.

## The y-axis on the manhattan tracks

Every DIT-HAP track draws a y-axis with tick labels, so `DR`, `DL` and `LFC` can
be read off the browser rather than only compared between tracks.

Nothing in `config/*.yaml` turns this on, and the reason is worth recording,
because the obvious candidates all look like they should work and none of them
do. Core draws the axis in `WiggleDisplayComponent`:

```jsx
const { stats, height, graphType } = model
...
{stats && graphType ? <svg ...><YScaleBar model={model} /></svg> : null}
```

and `graphType` is a getter on the wiggle display model that is true only for
`XYPlotRenderer` and `LinePlotRenderer`. The vendored `LinearManhattanDisplay`
overrides `rendererTypeName` to `LinearManhattanRenderer`, so `graphType` is
false and the axis is never mounted. It also declares
`get needsScalebar() { return true }` — which is **dead code in v4.3.0**:
`needsScalebar` appears nowhere in the v4.3.0 source, an earlier 2.x core read it
here, and the plugin was written against that. There is no config slot for the
axis either; `BaseLinearDisplay` offers only `maxFeatureScreenDensity`,
`fetchSizeLimit`, `height`, `mouseover` and `jexlFilters`, and
`SharedWiggleConfigSchema` only `autoscale`, `minimalTicks`, `minScore`,
`maxScore`, `numStdDev`, `scaleType` and `inverted`. Track height is not a gate
either: it changes which ticks are drawn, not whether the bar is.

So it takes a plugin. `plugins/gwas-chrome.js` is plain JS with no build step, and
it overrides that one getter through the public `Core-extendPluggableElement`
extension point — the same mechanism core plugins use on each other, and the same
shape core's own handlers use. The same handler adds all four pieces of chrome
this site needs: the getter for the axis, the
[strand key](#the-strand-key-on-the-manhattan-tracks) wrapped around the display's
React component, and the [line at y = 0](#the-line-at-y--0) and the
[threshold at y = −1](#the-threshold-at-y--1) drawn by the renderer. The vendored
bundle is not patched: it is
still byte-for-byte upstream, and `scripts/99_assemble_site.sh` copies
`vendor/plugins/` and `plugins/` side by side into `site/plugins/`, emptying that
directory first so a renamed plugin cannot linger in the built site.

Claiming `graphType` is less of a lie than it looks. For a single-source wiggle
display it is read in exactly three places: the axis condition above, the SVG
export's copy of it, and the track menu, where it gates "Show inverted" and
"Show cross hatches". The renderer never reads it. And core already treats the
flag as "this display wants a y-axis" rather than "this is a line plot":
`SNPCoverageDisplay` declares `get graphType() { return true }` outright.

### The line at y = 0

`DR`, `DL` and `LFC` are all signed, and sign is usually the question being asked
of them: above zero the insertion is enriched, below it depleted. The renderer
draws no horizontal reference of any kind — it has a cross-hatch pass, but that
only runs when `displayCrossHatches` is set, and it is off by default — so the
sign boundary is wherever the reader judges y = 0 to be from the axis. The third
piece of chrome draws it, by wrapping the renderer's `draw` so the line goes on
last, over the points rather than under them.

Where y = 0 falls is read from the props the renderer is already handed, not
recomputed from `minScore`/`maxScore`. That distinction matters: the display
widens the configured domain, and an LFC track set to `-12..3` is drawn on a
measured `[-12, 4]`. The two props used are `scaleOpts.domain`, the axis domain,
and `ticks.range`, which is `[y(domain[0]), y(domain[1])]` — together the whole
transform for a linear scale, which is the only scale these tracks can use, the
values being negative and a log scale unavailable. Both arrive per block, so
each block gets its line in its own place.

**Dashed, not solid.** Three reasons, in order of weight:

* The line is an annotation, and a dashed rule is the convention for one. Solid,
  it reads as data — or worse, as the axis.
* The LFC cloud is densest where it crosses zero, so a solid line is buried
  exactly where it is most wanted. Dashes stay legible through it.
* The renderer's own gridlines are solid and are drawn from `ticks.values`, which
  includes zero. A solid line in a different colour would be two lines a pixel
  apart the moment someone switches on "Show cross hatches" from the track menu.

`ZERO_DASH` and `ZERO_COLOR` at the top of `plugins/gwas-chrome.js` are the two
constants to change if that call is ever revisited. The line is also skipped
unless zero is *strictly* inside the domain, so a track scaled entirely above or
below zero gets no line pinned to its edge claiming to be the sign boundary.

### The threshold at y = −1

`DR` is the depletion rate, and −1 is the cut for a severe phenotype: below it the
insertion is depleted hard enough to call. That cut is a judgement rather than
arithmetic, and nothing in the track data says where it is, so the plugin draws
it — orange, dashed, on the DR tracks only.

**Only the DR tracks.** The renderer is shared by all 29 `GWASTrack`s, and −1
means a different thing on each: a log2 fold change of −1 is not the same claim
as a depletion rate of −1, and `DL` is not on that scale at all. A line drawn
unconditionally would be asserting it on all three. There is nothing in the
renderer's own config to key off — its schema has exactly one slot, `color` — and
the display's config is not among the props, but `props.adapterConfig` is, and it
names the file being drawn. Every DR track reads
`data/tracks/<dataset>/DR.bed.gz`, so the plugin keys a small table on that
basename. That also survives track renames, display renames and session changes,
none of which a `displayId` would.

**Orange, not red.** Red is the obvious colour for a severity threshold and the
one that does not work: the forward-strand points are already `darkred`, and −1
sits at the bottom of the cloud where those points are exactly what is being
read, so
a red line lands on top of them. In CIE76 ΔE, pure red `#d62728` measures 23.7
from `darkred` — the same colour to the eye. Magenta and purple are no better at
the other end, at ΔE 50.0 and 34.6 from the reverse strand's `darkblue`.
`#e08214` clears everything on the track: 48.1 from `darkred`, 77.3 from the grey
zero line, 81.2 from the white background. Orange is also the conventional
threshold hue, which is the meaning wanted.

**Longer dashes than the zero line** — `[9, 4]` against `[4, 3]`. Both are
annotations, but not the same kind: y = 0 is arithmetic, y = −1 is a chosen cut.
Carrying that difference in the pattern as well as the colour keeps it through a
greyscale print or a colour-blind reader, and stops the threshold reading as
being as absolute as zero. `THRESHOLD_COLOR` and `THRESHOLD_DASH` are the knobs.

### Why the plugin is not simply upgraded to a version that has it

Worth writing down, because it looks like the obvious fix and it is not.

`get graphType()` was added to the plugin in 2.2.0 and **removed again** in
commit `7d0ac2de` ("Inline WiggleBaseRenderer concept into plugin", #24), which
is what shipped as 2.1.2 and everything after. It has not come back: the current
`master` of `GMOD/jbrowse-plugin-gwas` still has `needsScalebar` and no
`graphType`. So no version of the plugin both works here and draws the axis.

The version numbers are also not in publish order, which is the trap. 2.2.0 was
published 2025-11-18, *between* 2.1.1 and 2.1.2, and the author then carried on
with 2.1.3, 2.1.4, 2.1.6. `npm dist-tags` says `latest: 2.1.6` — which is both
the newest publish (2026-03-14) and the one vendored here.

Pinning to 2.2.0 does not merely lose the axis, it stops the site loading at all.
2.2.0 was built against `@jbrowse/core ^3.3.0` and extends
`WigglePlugin.exports.WiggleBaseRenderer`; that name **does not exist anywhere in
JBrowse 4.3.0** (grep the app bundle: zero hits). The class extends `undefined`
and the app dies on a blank page with
`TypeError: Class extends value undefined is not a constructor or null` thrown
from `createPluggableElements`. 2.1.6 replaced that with an inlined renderer and
so needs nothing from the host but `LinearWiggleDisplayReactComponent`, which
4.3.0 does export. Confirmed by swapping the bundle in and loading it.

So the plugin stays at 2.1.6 and `plugins/gwas-chrome.js` restores the one getter
#24 dropped. If the plugin is ever bumped, re-check this file: the override is
harmless if `graphType` comes back, but it would then be redundant. The strand key
would survive a bump either way — it wraps the display's React component, and does
not depend on anything the plugin omits.

### Why the plugin is still a plugin at all

Short answer: because the built-in `GWASTrack` is real and works, is pinned to a
version this site could move to, and would still cost a rewrite of every track
below it. It was tried, and the decision was to stay on 4.3.0 and keep the plugin.
The measurements are here so the next person does not have to redo them.

**The built-in exists, in 5.0.** The GWAS track has been upstreamed into JBrowse
itself: `plugins/gwas` is a package in `GMOD/jbrowse-components`, and the demo
config on `jbrowse.org/code/jb2/main/` declares `"plugins": null` while still
using `type: GWASTrack` and `LinearManhattanDisplay`. Grep confirms the split —
over the v4.3.0 bundle, `LinearManhattanDisplay`, `GWASTrack`, `GWASAdapter` and
even the bare string `Manhattan` return **zero hits** across all 433 JS files,
while `MultiQuantitativeTrack` and `MultiLinearWiggleDisplay` are both present; over
`5.0.0-beta.13` they return 8, 6, 7 and present.

**And it is pinnable, which is the part that is easy to get wrong.**
`jbrowse create --branch <x>` resolves to
`https://s3.amazonaws.com/jbrowse.org/code/jb2/<x>/jbrowse-web-<x>.zip`, and that
bucket is keyed by **git tag**, so every tag has an immutable build even though
GitHub has no *release* for the 5.0 pre-releases (tags only, so `--tag` cannot
find one):

```
jb2/v5.0.0-beta.13/jbrowse-web-v5.0.0-beta.13.zip   200, 25.8 MB, 2026-10-05
jb2/v5.0.0-beta.12/…                                200
jb2/v4.3.0/…                                        200
```

Probing only the obvious names (`5.0`, `v5.0`, `next`, `release`) 403s and makes
this look unpinnable. It is not.

**It runs.** A 5.0.0-beta.13 site was assembled from this repository's own
`config.json` and `data/` with `plugins` emptied, and `check_site.py` passed all
four passes — every track present, sequence clean, search working, no console
errors. The gene tracks came out *better*: 5.0 drops the density guard, so
`pombe_genes` draws at 1 Mbp/px where 4.3.0 shows "Zoom in to see features or
force load".

**What it would cost.** Three things, all measured on that build:

- The 29 `GWASTrack` displays render **blank**. The plugin-era `renderers:` block
  is not honoured and every feature is skipped — `14,177 of 14,177 skipped` on a
  300 kb window of `hd_dit_hap_lfc_yes1`. The fix is `scoreColumn: "score"` on the
  adapter plus a `marks`/`scales` display block; verified working, but it is 29
  entries rewritten against an API with no migration shim.
- The multi-source tracks lose their axis and colour. 5.0 ignores
  `minScore`/`maxScore` on this display — measured when those tracks carried a
  fixed `0..500`, which the coverage autoscaled away from to 2000 — and whether
  it honours the `autoscale: localsd` they now carry instead is **unmeasured**.
  5.0 replaces the five `Multi*Renderer` types with `rows`/`color`/`scales` —
  there is a migration shim for `defaultRendering: multirowxy`, but `color.field`
  only accepts `"score"`, so a colour per source cannot be expressed that way.
- The plugin and this file's axis override both become redundant.

None of that is a reason not to move eventually. It is a reason not to move to a
**beta** to save a plugin.

## Deployment

`.github/workflows/pages.yml` runs `./build.sh site` on every push to `main` and
publishes `site/` with `actions/deploy-pages`.

**Settings → Pages → Build and deployment → Source** must be **GitHub Actions**.
It cannot be set from inside the repository, so the very first deploy needs a
human. Note that changing this setting does *not* re-run a workflow that has
already failed: re-run it from the Actions tab, or push another commit.

Limits that shape this repository, from GitHub's documentation:

| limit | value | consequence here |
| --- | --- | --- |
| Git file size | **blocked above 100 MiB** (warned above 50 MiB) | applies to committed files, i.e. `data/`; `build.sh` warns |
| published site | **1 GiB** | `build.sh` warns |
| source repository | recommended 1 GiB | — |
| deploy duration | **10 minutes** | irrelevant while `site` only assembles files |
| bandwidth | soft 100 GiB/month | — |
| builds | soft 10/hour, **not applied to custom Actions workflows** | one reason this uses Actions |
| Pages availability | public repos on any plan; private repos need Pro/Team/Enterprise | this repo is public |

A file over 100 MiB cannot be committed at all, so it cannot be shipped by
copying it into the repository; Git LFS is the usual escape, but
`actions/checkout` does not fetch LFS content unless told to, so the artifact
would contain pointer files instead of data. Committing small build artefacts,
as this repository does, avoids the question entirely.

## Provenance

`config/assembly.yaml` records the PomBase release, the SHA-256 of both upstream
files, and the `DIT_HAP_snakemake` / `DIT_HAP_analysis` commits this build was
made from. `scripts/10_assembly.sh` verifies the checksums before use, so a
reissued PomBase release fails the build instead of silently shifting the
coordinate system under every track.

## Licence

Code is MIT (see `LICENSE`). Data produced by this project is CC BY 4.0 (see
`LICENSE-DATA`). Reference sequence and annotation are from
[PomBase](https://www.pombase.org/) and remain subject to PomBase's terms.

The Kuang et al. 2017 BAMs under `data/external/kuang/` are **redistributed**
here rather than only linked, so the tracks load at a usable speed. They remain
their authors' data and are subject to whatever terms GSE79802 carries; the
citation, sample and source URL are recorded on every track and in
`config/external.yaml`.

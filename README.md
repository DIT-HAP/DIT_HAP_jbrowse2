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
  external/        mirrored third-party datasets (the Kuang et al. BAMs)
scripts/
  lib/common.sh    tool resolution and logging
  lib/yamlspec.py  config/*.yaml -> site/config.json
  10_assembly.sh   reference layer
  15_external.sh   mirror third-party datasets
  20_tracks.py     release tables -> data/tracks/*.bed.gz
  serve.py         local preview server (Range-capable; see below)
  check_site.py    headless end-to-end check
  99_assemble_site.sh  site/, including the search index
vendor/plugins/  vendored JBrowse plugins
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

One track per dataset, quantity and time point — 34 of them — plus the reference
gene track and six mirrored external tracks, 41 in all. Every one is added from
the track selector, grouped first by dataset, then by `Curve fit` (the DR and DL
parameters) or `LFC` (per time point). The DIT-HAP tracks are built from the
release tables:

| source | column | track |
| --- | --- | --- |
| `fitting_results.tsv` | `DR` | Depletion rate |
| `fitting_results.tsv` | `DL` | Depletion lag |
| `LFC.tsv` | one per time point | Log2 fold change *time point* |

The time point the fold changes are measured against (`YES0` for most datasets,
`0h` for LD haploid, `Spikein0` for the control) has an LFC of exactly zero at
every insertion. It is a flat line by construction, not a result, so it gets no
track; `20_tracks.py` drops any time point whose values are all identical rather
than naming the baseline column, so a renamed baseline is still handled.

Datasets are listed in `config/datasets.yaml`. `HD_diploid` is deliberately
absent: it appears in `DIT_HAP_analysis/config/datasets.yaml` but has no
`release/` tree on disk.

Four details below were settled by measurement rather than assumption, because
each fails silently:

- **Coordinates are 0-based.** The pipeline writes `Coordinate = R1_Ref_Start + 4`
  for the `+` strand and `R1_Ref_End` for the `-`, both straight out of pysam. So
  the `TTAA` motif occupies `[Coordinate-4, Coordinate)`. Pulling those four bases
  out of the shipped FASTA returns `TTAA` for 96.2% of sampled rows — the same
  share as the `Target` column — where a 1-based reading scores 1.3%.
- **`minScore`/`maxScore` are honoured**, even though the vendored display
  defaults to `autoscale: local`. Rendering one BED under `-2.2..1.1` and again
  under `-100..100` gives a scattered cloud and a flat line. The ranges are fixed
  and shared across datasets so time points and datasets can be compared by eye;
  points outside the domain clamp to the edge rather than erroring.
- **`strand` reaches the renderer as the number `1`/`-1`**, not `'+'`/`'-'`, so
  the colour expression compares against numbers. Comparing against characters
  paints every point the same colour, with no error.
- **These tracks have no density guard.** Unlike the feature tracks, the vendored
  `LinearManhattanDisplay` overrides `regionTooLarge` to return `false`, so they
  never show "Zoom in to see features" and draw at any zoom. At whole-genome zoom
  each enabled track downloads its entire BED — 18 tracks at once moved 24.9 MB —
  where at gene scale the same tracks fetch a few kB each.

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

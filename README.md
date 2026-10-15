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
side. Use the location box to jump to a gene (`I:99833..101431`) or a
chromosome (`II`). Tracks are added from the track selector, grouped by
dataset.

## Building it locally

```bash
./build.sh data     # reference layer + tracks  (slow; outputs are committed)
./build.sh site     # assemble site/            (fast; runs on every deploy)
./build.sh serve    # serve site/ on :8080
./build.sh clean
```

`data` needs `bgzip`, `tabix` and `samtools` on `PATH`. If they live in a conda
environment that is not activated, point at them explicitly:

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

This drives a headless browser and asserts three things a build can otherwise
get wrong silently:

1. that JBrowse **accepted** the default session — a malformed one is discarded
   in favour of an empty default with no visible error;
2. that every configured track actually renders;
3. that the Sequence track reads cleanly at a locus deep in a chromosome, which
   is what catches a preview server that ignores `Range`.

It needs `playwright`; CI does not run it.

Note that `document.body.innerText` and a `canvas` count are **not** reliable
probes for the Sequence track: it draws bases as DOM elements, not into a
canvas, and a stale `Loading` node stays in the tree after the track has
rendered. Screenshots and the track container's own text are.

## Layout

```
config/          the only place tracks and sessions are defined
  assembly.yaml    reference assembly + upstream provenance and checksums
  datasets.yaml    which analysis datasets are published, and where they live
  tracks.yaml      track registry
  session.yaml     the default view
data/            built artefacts, committed (the deployed site never
                 reaches back to PomBase)
  assembly/        bgzipped FASTA + .fai + .gzi
  annotation/      bgzipped GFF3 + .tbi
  tracks/          one BED (bgzip + tabix) per dataset / quantity / time point
scripts/
  lib/common.sh    tool resolution and logging
  lib/yamlspec.py  config/*.yaml -> site/config.json   (the only writer)
  10_assembly.sh   reference layer
  20_tracks.py     release tables -> data/tracks/*.bed.gz
  serve.py         local preview server (Range-capable; see below)
  check_site.py    headless end-to-end check
  99_assemble_site.sh  site/
vendor/plugins/  vendored JBrowse plugins
site/            build artefact, gitignored
```

`site/config.json` is generated. Editing it by hand has no effect, because the
next build overwrites it — change `config/*.yaml` instead.

## Data flow

```
DIT_HAP_snakemake  ──┐
  release/insertion_level/*.tsv
                     ├─►  scripts/20_tracks.py ─►  data/tracks/*.bed.gz (+ .tbi)
PomBase release    ──┘                           data/assembly/, data/annotation/
                                                 │
                            config/*.yaml  ──────┼─►  scripts/lib/yamlspec.py
                                                 │            │
                                                 └────────────┴─► site/config.json
```

`./build.sh data` runs every `scripts/NN_*` stage in filename order; `.sh` stages
run under bash and `.py` stages under python3 (`-I`, so nothing in the current
directory can shadow the imports).

## Tracks

One track per dataset, quantity and time point — 34 of them, all added from the
track selector and grouped first by dataset, then by `Curve fit` (the DR and DL
parameters) or `LFC` (per time point). They are built from the release tables:

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
Third-party datasets linked from the browser keep their own licences; see the
site's documentation page.

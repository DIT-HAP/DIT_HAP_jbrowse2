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

### Checking a build

```bash
python3 scripts/check_site.py --url http://127.0.0.1:8080/
```

This drives a headless browser and asserts two things a build can otherwise get
wrong silently: that JBrowse **accepted** the default session (a malformed one
is discarded in favour of an empty default without any visible error), and that
every configured track actually renders. It needs `playwright`; CI does not run
it.

## Layout

```
config/          the only place tracks and sessions are defined
  assembly.yaml    reference assembly + upstream provenance and checksums
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
                     ├─►  scripts/20_*.py  ─►  data/tracks/*.bed.gz (+ .tbi)
PomBase release    ──┘                        data/assembly/, data/annotation/
                                              │
                            config/*.yaml  ───┼─►  scripts/lib/yamlspec.py
                                              │            │
                                              └────────────┴─► site/config.json
```

## Deployment

`.github/workflows/pages.yml` runs `./build.sh site` on every push to `main` and
publishes `site/` with `actions/deploy-pages`.

Two settings must be in place on the repository, and neither can be set from
inside the repository:

- **Settings → Pages → Build and deployment → Source** must be **GitHub
  Actions**.
- GitHub Pages serves files up to **100 MB each**, does not serve **Git LFS**
  content, and applies a 1 GB soft limit to the published site. `build.sh`
  warns if any file crosses the per-file limit.

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

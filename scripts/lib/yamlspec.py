#!/usr/bin/env python3
"""Generate site/config.json from config/{assembly,tracks,session}.yaml.

config.json is a build artefact. Editing it by hand would be silently
overwritten on the next build, so all configuration lives in the YAML files and
this script is the only writer.

Two things here are easy to get wrong and are therefore done mechanically:

  * Display ids. A saved session addresses a display by
    "<track id>-<display type>", and that same string must appear as the
    display's `displayId` in config.json. It is derived here rather than
    hand-written so the two cannot drift.

  * Location objects. JBrowse wants
    {"uri": ..., "locationType": "UriLocation"} where the YAML just names a
    repo-relative path. Any key ending in "Location", plus `location` inside an
    index block, is rewritten. Paths stay RELATIVE so the same config.json works
    for local preview and for the deployed site under a subpath.

Usage:
    python3 -I scripts/lib/yamlspec.py --out site/config.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_yaml(path: pathlib.Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def read_fai(path: pathlib.Path) -> dict[str, int]:
    """Sequence name -> length, from a .fai index."""
    lengths: dict[str, int] = {}
    if not path.exists():
        raise SystemExit(
            f"missing FASTA index {path}.\n"
            "Run `./build.sh data` first -- it bgzips and indexes the assembly."
        )
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        lengths[parts[0]] = int(parts[1])
    return lengths


def to_location(value, data_base: str):
    """Repo-relative path -> a JBrowse UriLocation object."""
    if isinstance(value, dict):
        return value
    # An absolute URL is already a complete location. Prefixing the site's data
    # base onto it yields a nonsense relative path like "https:/..." nested
    # inside the site, which fails at fetch time with nothing said. Remote
    # tracks use full URLs deliberately: third-party data stays on the
    # authoritative copy rather than being redistributed from here.
    uri = value if "://" in value else f"{data_base}{value}"
    return {"uri": uri, "locationType": "UriLocation"}


def rewrite_locations(node, data_base: str):
    """Recursively wrap every *Location / index.location value."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if (key.endswith("Location") or key == "location") and isinstance(value, str):
                out[key] = to_location(value, data_base)
            else:
                out[key] = rewrite_locations(value, data_base)
        return out
    if isinstance(node, list):
        return [rewrite_locations(v, data_base) for v in node]
    return node


def build_assembly(spec: dict, lengths: dict[str, int], order: list[str]) -> dict:
    name = spec["name"]
    return {
        "name": name,
        "displayName": spec.get("display_name", name),
        "sequence": {
            "type": "ReferenceSequenceTrack",
            "trackId": f"{name}_refseq",
            "name": "Reference sequence",
            "adapter": {
                "type": "BgzipFastaAdapter",
                "fastaLocation": {
                    "uri": "data/assembly/pombe.fa.gz",
                    "locationType": "UriLocation",
                },
                "faiLocation": {
                    "uri": "data/assembly/pombe.fa.gz.fai",
                    "locationType": "UriLocation",
                },
                "gziLocation": {
                    "uri": "data/assembly/pombe.fa.gz.gzi",
                    "locationType": "UriLocation",
                },
            },
        },
        # JBrowse lists sequences in this order in the loc bar and the assembly
        # selector. Without it, order comes from the FASTA file, which starts
        # with chr_II_telomeric_gap.
        #
        # refNameAliases is deliberately absent: the loader does
        # `const {refNameAliases} = assembly; if (!refNameAliases) throw ...`,
        # so an empty object passes the check and then fails on the missing
        # adapter. Omitting the key is the correct way to have no aliases.
        "refNames": order,
    }


def build_tracks(spec: dict, assembly_name: str, data_base: str) -> tuple[list[dict], dict]:
    """Returns (config.json tracks, track id -> {type, displays})."""
    defaults = spec.get("defaults") or {}
    tracks: list[dict] = []
    index: dict[str, dict] = {}
    seen: set[str] = set()

    for entry in spec.get("tracks") or []:
        tid = entry["id"]
        if tid in seen:
            raise SystemExit(f"duplicate track id {tid!r} in config/tracks.yaml")
        seen.add(tid)

        track = {
            "type": entry["type"],
            "trackId": tid,
            "name": entry["name"],
            "assemblyNames": [entry.get("assembly", defaults.get("assembly"))],
        }
        if entry.get("category"):
            category = entry["category"]
            track["category"] = [category] if isinstance(category, str) else list(category)

        # Free-form provenance (source, citation, sample, conditions). JBrowse
        # shows it under the track's "About this track", which is where the
        # attribution for linked third-party data has to live.
        if entry.get("metadata"):
            track["metadata"] = entry["metadata"]

        track["adapter"] = rewrite_locations(entry["adapter"], data_base)

        displays = []
        display_index = []
        for disp in entry.get("displays") or []:
            dtype = disp["type"]
            display_id = f"{tid}-{dtype}"
            if any(d["type"] == dtype for d in display_index):
                raise SystemExit(f"track {tid!r} declares two {dtype} displays")
            out = {"type": dtype, "displayId": display_id}
            for key, value in disp.items():
                if key != "type":
                    out[key] = rewrite_locations(value, data_base)
            displays.append(out)
            display_index.append({"type": dtype, "configuration": display_id})

        if displays:
            track["displays"] = displays
        tracks.append(track)
        index[tid] = {"type": entry["type"], "displays": display_index}

    return tracks, index


def build_session(
    spec: dict,
    track_index: dict,
    order: list[str],
    lengths: dict[str, int],
    assembly_name: str,
) -> dict:
    views = []
    for view in spec.get("views") or []:
        want = view.get("regions", "all")
        if want == "all":
            refs = order
        else:
            unknown = [r for r in want if r not in lengths]
            if unknown:
                raise SystemExit(f"session view {view['id']!r} names unknown regions {unknown}")
            refs = list(want)

        # assemblyName is NOT optional. Without it the view cannot resolve which
        # assembly its regions belong to, and JBrowse sits on its "Loading"
        # spinner forever -- no exception, no failed request, nothing in the
        # console, and the session still reaches IndexedDB looking perfectly
        # well-formed. Cost an afternoon to find; it is the first thing to check
        # if the default view ever fails to appear.
        regions = [
            {
                "refName": ref,
                "start": 0,
                "end": lengths[ref],
                "reversed": False,
                "assemblyName": assembly_name,
            }
            for ref in refs
        ]
        total = sum(lengths[ref] for ref in refs)
        bp_per_px = round(total / float(view.get("assume_width_px", 1320)), 4)

        track_refs = []
        for tid in view.get("tracks") or []:
            if tid not in track_index:
                raise SystemExit(f"session view {view['id']!r} names unknown track {tid!r}")
            info = track_index[tid]
            track_refs.append(
                {
                    "id": f"{view['id']}-{tid}",
                    "type": info["type"],
                    "configuration": tid,
                    "displays": [
                        {"id": f"{view['id']}-{d['configuration']}", **d}
                        for d in info["displays"]
                    ],
                }
            )

        views.append(
            {
                "id": view["id"],
                "type": "LinearGenomeView",
                "bpPerPx": bp_per_px,
                "displayedRegions": regions,
                "tracks": track_refs,
            }
        )

    return {
        "id": "dit-hap-default",
        "name": spec.get("name", "DIT-HAP browser"),
        "views": views,
        "sessionPlugins": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="path to write config.json")
    ap.add_argument(
        "--data-base",
        default="",
        help="URL prefix for data/ paths; empty keeps them relative to the page",
    )
    args = ap.parse_args()

    config_dir = REPO_ROOT / "config"
    assembly_spec = load_yaml(config_dir / "assembly.yaml")["assembly"]
    tracks_spec = load_yaml(config_dir / "tracks.yaml")
    session_spec = load_yaml(config_dir / "session.yaml")["session"]

    lengths = read_fai(REPO_ROOT / "data" / "assembly" / "pombe.fa.gz.fai")
    order = [c for c in assembly_spec["chrom_order"] if c in lengths]
    missing = [c for c in assembly_spec["chrom_order"] if c not in lengths]
    if missing:
        print(f"warning: chrom_order names sequences absent from the FASTA: {missing}", file=sys.stderr)
    extra = [c for c in lengths if c not in order]
    if extra:
        print(f"warning: FASTA has sequences not in chrom_order (they get no refNames slot): {extra}", file=sys.stderr)

    tracks, track_index = build_tracks(tracks_spec, assembly_spec["name"], args.data_base)

    config = {
        "assemblies": [build_assembly(assembly_spec, lengths, order)],
        "tracks": tracks,
        "plugins": [
            {"name": "GWAS", "url": "plugins/jbrowse-plugin-gwas.umd.js"},
        ],
        "configuration": {},
        "defaultSession": build_session(
            session_spec, track_index, order, lengths, assembly_spec["name"]
        ),
    }

    out = pathlib.Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(config, indent=2) + "\n")

    total = sum(lengths[r] for r in order)
    view = config["defaultSession"]["views"][0]
    print(f"wrote {out.relative_to(REPO_ROOT)}")
    print(f"  assembly   {assembly_spec['name']}  {len(lengths)} sequences  {total:,} bp")
    print(f"  tracks     {len(tracks)}")
    print(f"  session    1 view, {len(view['displayedRegions'])} regions, "
          f"{len(view['tracks'])} tracks, bpPerPx={view['bpPerPx']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

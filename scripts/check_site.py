#!/usr/bin/env python3
"""Headless end-to-end check of a built site.

Four passes, because each catches failures the others cannot:

  1. Load the site with no session in the URL, then read back the session
     JBrowse wrote to IndexedDB. This proves defaultSession was ACCEPTED --
     a snapshot with the wrong shape is silently discarded in favour of an
     empty default, and the page still looks like it loaded fine.

  2. Load again with a ?session=spec- URL that switches every track on, then
     look for the track names and for ink on the canvases. This exercises
     adapter -> renderer -> data for real, which pass 1 never touches.

  3. Load the Sequence track at a locus deep inside a chromosome. It is the
     only reader of the .fai and .gzi indexes, so it is what catches a preview
     server that ignores the Range header.

  4. Type a gene name into the search box and require a result. A missing trix
     index is invisible otherwise: the box still renders and typing still opens
     a dropdown, it just never matches anything.

The spec JSON in the URL must be RAW, not percent-encoded; encoding it makes
JBrowse ignore the parameter entirely.

Needs playwright. It is not a build dependency -- CI does not run this.

Usage:
    python3 scripts/check_site.py --url http://127.0.0.1:8080/
"""

from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import re
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit(
        "playwright is required for site checks.\n"
        "  pip install playwright && playwright install chromium\n"
        "or run with an interpreter that already has it."
    )

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

CHROME_CANDIDATES = [
    "/data/a/yangyusheng/.cache/ms-playwright/chromium-1187/chrome-linux/chrome",
]

READ_SESSION = """
async () => {
  const out = [];
  for (const { name } of await indexedDB.databases()) {
    const db = await new Promise((res, rej) => {
      const r = indexedDB.open(name);
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
    for (const store of Array.from(db.objectStoreNames)) {
      const vals = await new Promise((res, rej) => {
        const r = db.transaction(store, 'readonly').objectStore(store).getAll();
        r.onsuccess = () => res(r.result);
        r.onerror = () => rej(r.error);
      });
      for (const v of vals) {
        try { out.push(JSON.parse(JSON.stringify(v))); } catch (e) {}
      }
    }
  }
  return out;
}
"""

CANVAS_INK = """
() => [...document.querySelectorAll('canvas')].map(c => {
  let ink = 0;
  try {
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i+3] > 10 && (d[i] < 240 || d[i+1] < 240 || d[i+2] < 240)) ink++;
    }
  } catch (e) {}
  return ink;
})
"""


def launch(browser_type, **kw):
    for cand in CHROME_CANDIDATES:
        if pathlib.Path(cand).exists():
            return browser_type.launch(executable_path=cand, **kw)
    return browser_type.launch(**kw)


def probe_term(config):
    """A gene name to search for, taken from the annotation itself.

    The index is built from this same GFF3, so a name read out of it must
    resolve. Prefers `Name=` -- what a human would actually type -- and falls
    back to `ID=` for the genes that carry only a systematic name.
    """
    for track in config.get("tracks") or []:
        adapter = track.get("adapter") or {}
        loc = adapter.get("gffGzLocation") or adapter.get("gffLocation")
        if not isinstance(loc, dict):
            continue
        path = REPO_ROOT / (loc.get("uri") or "")
        if not path.exists():
            continue
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt") as fh:
            fallback = None
            for i, line in enumerate(fh):
                if i > 4000:
                    break
                if "\tgene\t" not in line:
                    continue
                named = re.search(r"(?:^|;)Name=([^;]+)", line)
                if named:
                    return named.group(1)
                if fallback is None:
                    ident = re.search(r"(?:^|;)ID=([^;]+)", line)
                    fallback = ident.group(1) if ident else None
            if fallback:
                return fallback
    return None


def find_session(values):
    for v in values:
        blob = json.dumps(v)
        if "displayedRegions" in blob and "LinearGenomeView" in blob:
            return v.get("session", v)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--config", default=str(REPO_ROOT / "site" / "config.json"))
    ap.add_argument("--timeout", type=int, default=30_000)
    ap.add_argument("--screenshot", help="write a PNG of the track view to this path")
    args = ap.parse_args()

    config = json.loads(pathlib.Path(args.config).read_text())
    assembly = config["assemblies"][0]["name"]
    track_ids = [t["trackId"] for t in config["tracks"]]
    track_names = {t["trackId"]: t["name"] for t in config["tracks"]}
    expected_regions = len(config["defaultSession"]["views"][0]["displayedRegions"])
    failures: list[str] = []

    with sync_playwright() as p:
        browser = launch(
            p.chromium,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )

        # --- pass 1: was defaultSession accepted? ---------------------------
        print("pass 1: default session")
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)[:200]))
        page.goto(args.url, wait_until="load", timeout=args.timeout)
        page.wait_for_timeout(12_000)
        text = page.evaluate("() => document.body.innerText")
        if "Fatal error" in text or "JBrowse Error" in text:
            failures.append("page reported an error on load")

        # A malformed displayedRegion (most easily: a missing assemblyName) does
        # not raise, does not log, and still writes a well-formed session to
        # IndexedDB -- the page just never leaves "Loading". Asserting only on
        # the stored session therefore passes on a site that shows nothing.
        #
        # "No canvas" on its own does NOT mean broken, though. A
        # LinearGenomeView draws its ruler as DOM text and paints no canvas in
        # two legitimate states, each with its own notice:
        #   "No tracks active"          -- the session has no tracks
        #   "Zoom in to see features"   -- JBrowse refuses to fetch a whole
        #                                  chromosome of features at genome-wide
        #                                  zoom and waits for a zoom or FORCE LOAD
        # So the default view is stuck only if it shows NEITHER notice AND no
        # canvas -- which is precisely the assemblyName failure.
        canvases = page.evaluate("() => document.querySelectorAll('canvas').length")
        notices = ("No tracks active", "Zoom in to see features")
        notice = next((n for n in notices if n in text), None)
        declared = len(config["defaultSession"]["views"][0].get("tracks") or [])
        if canvases == 0:
            if notice is None:
                failures.append(
                    "default view rendered nothing at all -- no canvas and no "
                    "track notice; the page is stuck on Loading (check that "
                    "every displayedRegion carries assemblyName)"
                )
            else:
                print(f"  no canvas; the track box says {notice!r} (not a fault)")
                if declared == 0:
                    print("  note: defaultSession declares no tracks")
        print(f"  canvases in default view: {canvases}")

        session = find_session(page.evaluate(READ_SESSION))
        if session is None:
            failures.append("no session snapshot reached IndexedDB")
        else:
            views = session.get("views") or []
            if not views:
                failures.append("stored session has no views (defaultSession was rejected)")
            else:
                n = len(views[0].get("displayedRegions") or [])
                if n != expected_regions:
                    failures.append(
                        f"stored session shows {n} regions, config declares {expected_regions}"
                    )
                else:
                    print(f"  ok: {n} regions, bpPerPx={views[0].get('bpPerPx')}")
                print(f"  ok: {len(views)} view(s)")
        page.close()

        # --- pass 2: does every track actually render? ----------------------
        print("pass 2: track rendering")
        spec = {
            "views": [
                {
                    "assembly": assembly,
                    "loc": "I:1-20000",
                    "type": "LinearGenomeView",
                    "tracks": track_ids,
                }
            ]
        }
        url = args.url.rstrip("/") + "/?session=spec-" + json.dumps(spec, separators=(",", ":"))
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors2: list[str] = []
        page.on("pageerror", lambda e: errors2.append(str(e)[:200]))
        page.goto(url, wait_until="load", timeout=args.timeout)
        page.wait_for_timeout(15_000)
        text = page.evaluate("() => document.body.innerText")
        if "Fatal error" in text or "JBrowse Error" in text:
            failures.append("page reported an error with tracks enabled")

        ink = page.evaluate(CANVAS_INK)
        print(f"  canvases: {len(ink)}, max ink: {max(ink) if ink else 0}")
        for tid in track_ids:
            name = track_names[tid]
            ok = name in text
            print(f"  {'ok' if ok else 'MISSING'}: track {tid!r} ({name})")
            if not ok:
                failures.append(f"track {tid!r} did not appear in the view")
        if not ink or max(ink) < 500:
            failures.append("no canvas showed meaningful ink")
        if errors2:
            print(f"  page errors: {errors2[-3:]}")
        if args.screenshot:
            page.screenshot(path=args.screenshot, full_page=True)
            print(f"  screenshot: {args.screenshot}")
        page.close()

        # --- pass 3: the Sequence track, the only reader of .fai + .gzi -------
        # This is the check for a server that ignores Range (notably
        # `python3 -m http.server`, which answers 200 with the whole body).
        # Feature tracks can survive that -- their first reads are served from
        # offset 0 either way -- but the Sequence track resolves each read
        # through the .gzi first, so whole-body answers put it in the middle of
        # a BGZF block and it fails with
        #     problem decompressing block: not a valid bgzf or gzip block
        # The locus is deliberately deep into a chromosome: near offset 0 the
        # wrong bytes can still happen to be the right ones.
        print("pass 3: sequence track")
        refseq = config["assemblies"][0]["sequence"]["trackId"]
        region = config["defaultSession"]["views"][0]["displayedRegions"][0]
        deep = max(0, region["end"] - 300_000)
        loc = f"{region['refName']}:{deep + 1}-{deep + 300}"
        spec = {"views": [{"assembly": assembly, "loc": loc,
                           "type": "LinearGenomeView", "tracks": [refseq]}]}
        url = args.url.rstrip("/") + "/?session=spec-" + json.dumps(spec, separators=(",", ":"))
        page = browser.new_page(viewport={"width": 1400, "height": 700})
        page.goto(url, wait_until="load", timeout=args.timeout)
        page.wait_for_timeout(15_000)
        # The container holds the rendered bases when it works and the error box
        # when it does not, so one innerText read distinguishes the two.
        box = page.evaluate(
            """() => {
              const el = document.querySelector('[data-testid^="trackRenderingContainer"]');
              return el ? el.innerText : null;
            }"""
        )
        if box is None:
            failures.append("the Sequence track rendered no container")
        elif "Error" in box:
            failures.append(
                f"Sequence track failed at {loc}: {box.strip()[:160]} "
                "(most likely the server ignores Range; use ./build.sh serve)"
            )
        else:
            print(f"  ok: sequence track clean at {loc}")
        page.close()

        # --- pass 4: gene search ---------------------------------------------
        # Without an index the search box still renders and typing still opens a
        # dropdown -- it simply never matches. So drive it with a name read out
        # of the annotation the index was built from; if the index is missing or
        # stale, that name stops resolving.
        print("pass 4: gene search")
        term = probe_term(config)
        if term is None:
            print("  skipped: no local annotation to take a probe gene from")
        else:
            page = browser.new_page(viewport={"width": 1400, "height": 700})
            page.goto(args.url, wait_until="load", timeout=args.timeout)
            page.wait_for_timeout(12_000)
            box = page.locator('input[placeholder*="earch" i]').first
            if box.count() == 0:
                failures.append(
                    "no search box -- the assembly has no text search adapter "
                    "(the trix index was not built; run ./build.sh site)"
                )
            else:
                box.click()
                box.type(term, delay=50)
                page.wait_for_timeout(5_000)
                opts = page.evaluate(
                    """() => [...document.querySelectorAll('[role="option"]')]
                         .map(e => e.innerText.trim())"""
                )
                print(f"  {term!r} -> {opts[:6]}")
                if not opts:
                    failures.append(
                        f"searching for {term!r} returned nothing; the trix index "
                        "is missing or stale (rebuild with ./build.sh site)"
                    )
            page.close()

        browser.close()

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

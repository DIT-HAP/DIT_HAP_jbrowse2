#!/usr/bin/env python3
"""Static file server for local preview, WITH HTTP Range support.

Use this rather than `python3 -m http.server`. That one ignores the Range
header and answers 200 with the whole body, which breaks every indexed track:

  * JBrowse positions each read with a byte range (`Range: bytes=0-19585`).
    Handed the whole file instead, it slices from offset 0 and lands in the
    middle of a BGZF block.
  * bgzf-filehandle then reports, for the Sequence track only:
        problem decompressing block: not a valid bgzf or gzip block
    Feature tracks on the same server often look fine, because their first
    reads are satisfied from the start of the file -- the failure needs a read
    placed away from offset 0, e.g. a locus deep in a chromosome.

GitHub Pages does honour Range, so this is a local-preview problem only, but it
is indistinguishable from a corrupt .gz unless you know to look at the server.

Usage:
    python3 -I scripts/serve.py <directory> [--port 8080] [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import re
import sys

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    # Content-Length is always sent, so keep-alive is safe; JBrowse issues many
    # small reads and reuses the connection.
    protocol_version = "HTTP/1.1"

    def send_head(self):
        self._body_remaining = None
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            fh = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        try:
            size = os.fstat(fh.fileno()).st_size
        except OSError:
            fh.close()
            self.send_error(500, "Cannot stat file")
            return None

        start, end = self._requested_range(size)
        if start is None:
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return fh

        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        fh.seek(start)
        self._body_remaining = length
        return fh

    def _requested_range(self, size: int):
        """(start, end) from the Range header, or (None, None) for a full body.

        An unparsable or unsatisfiable Range falls back to the whole file, as
        RFC 9110 permits, so a stray header cannot break the preview.
        """
        header = self.headers.get("Range")
        if not header:
            return None, None
        m = RANGE_RE.match(header.strip())
        if not m:
            return None, None
        lo, hi = m.groups()
        if lo:
            start = int(lo)
            end = min(int(hi), size - 1) if hi else size - 1
        else:
            # Suffix form, "bytes=-500": the LAST n bytes.
            if not hi:
                return None, None
            start = max(0, size - int(hi))
            end = size - 1
        if start >= size or start > end:
            return None, None
        return start, end

    def copyfile(self, source, outputfile):
        remaining = self._body_remaining
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                # The file shrank under us (a rebuild during preview). Stop
                # rather than desync a keep-alive connection.
                self.close_connection = True
                return
            outputfile.write(chunk)
            remaining -= len(chunk)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("directory", help="directory to serve")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    directory = os.path.abspath(args.directory)
    if not os.path.isdir(directory):
        raise SystemExit(f"not a directory: {directory}")

    handler = functools.partial(RangeHandler, directory=directory)
    server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    print(f"serving {directory} on http://{args.host}:{args.port}/ (Range enabled)",
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

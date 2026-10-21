"""Local preview server for the built site, with HTTP Range support.

PMTiles is read by the browser in small byte ranges. Python's built-in
`python3 -m http.server` ignores Range headers, so this adds them.
nginx and Caddy support ranges out of the box, so this is for local use only.

Usage:
    python3 -m pipeline.serve [--port 8000]
"""

import argparse
import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "site")


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """Serves `Range: bytes=start-end` requests with 206 Partial Content."""

    def send_head(self):
        match = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not match or not os.path.isfile(path):
            return super().send_head()

        size = os.path.getsize(path)
        start_s, end_s = match.groups()
        if start_s:
            start, end = int(start_s), int(end_s) if end_s else size - 1
        else:  # Suffix range: last N bytes
            start, end = max(0, size - int(end_s)), size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416, "Requested Range Not Satisfiable")
            return None

        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._range_remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_range_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)
        self._range_remaining = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview the built map locally")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not os.path.isdir(SITE_DIR):
        raise SystemExit(f"{SITE_DIR} not found. Run `python3 -m pipeline.run tiles` first.")
    handler = partial(RangeRequestHandler, directory=SITE_DIR)
    print(f"Serving {SITE_DIR} at http://localhost:{args.port}/")
    ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()

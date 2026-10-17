"""Serve a generated demo bundle on localhost only; never serve the repo/.env."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path


class DemoHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; frame-src 'self'")
        super().end_headers()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    directory = args.directory.resolve(strict=True)
    if not (directory/"summary.json").is_file() or not (directory/"index.html").is_file():
        raise SystemExit("Expected a generated demo bundle, not a source directory")
    server = ThreadingHTTPServer(("127.0.0.1",args.port),partial(DemoHandler,directory=str(directory)))
    metadata = {"pid":os.getpid(),"url":f"http://127.0.0.1:{server.server_address[1]}/index.html",
                "directory":str(directory),"binding":"127.0.0.1 only"}
    (directory/"server.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(metadata,ensure_ascii=False),flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()

#!/usr/bin/env python3
"""Reverse proxy between the tunnel and the app; logs counted vs ignored traffic."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC_EXT = {
    ".js", ".css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".webp", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".webm", ".mp3",
    ".pdf", ".txt", ".xml", ".json", ".wasm",
}

# Merged with repo ignorePaths. Repos add their own patterns in .preview.json.
DEFAULT_IGNORE_PATHS = ["*poll*"]

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


class CountTracker:
    def __init__(self, ignore_paths: list[str]):
        patterns = list(DEFAULT_IGNORE_PATHS)
        for p in ignore_paths:
            if p and p not in patterns:
                patterns.append(p)
        self.ignore_patterns = patterns

    def ignored_by_config(self, path: str) -> bool:
        candidates = {path, path.split("?", 1)[0]}
        bare = path.lstrip("/")
        if bare:
            candidates.add(bare)
            candidates.add("/" + bare)
        for pattern in self.ignore_patterns:
            for candidate in candidates:
                if fnmatch.fnmatchcase(candidate, pattern) or fnmatch.fnmatchcase(candidate.lower(), pattern.lower()):
                    return True
        return False

    @staticmethod
    def is_static(path: str) -> bool:
        base = path.split("?", 1)[0]
        _, ext = os.path.splitext(base)
        if ext.lower() in STATIC_EXT:
            return True
        if base.startswith("/assets/") or base.startswith("/static/"):
            return True
        return False

    def should_count(self, method: str, path: str) -> tuple[bool, str]:
        clean = path.split("?", 1)[0] or "/"
        if self.ignored_by_config(clean):
            return False, "ignorePaths"
        if method.upper() in {"GET", "HEAD"} and self.is_static(clean):
            return False, "static"
        return True, "user"


def make_handler(upstream_host: str, upstream_port: int, log_path: str, tracker: CountTracker):
    log_lock = threading.Lock()

    class ProxyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            return

        def _proxy(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length else b""
            path = self.path
            counted, reason = tracker.should_count(self.command, path)
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            line = f"{ts}\t{self.command}\t{path.split('?', 1)[0]}\t{1 if counted else 0}\t{reason}\n"
            with log_lock:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line)

            conn = HTTPConnection(upstream_host, upstream_port, timeout=60)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            try:
                conn.request(self.command, path, body=body, headers=headers)
                resp = conn.getresponse()
                resp_body = resp.read()
                self.send_response(resp.status, resp.reason)
                for k, v in resp.getheaders():
                    if k.lower() in HOP_BY_HOP:
                        continue
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(resp_body)
            except Exception as exc:  # noqa: BLE001
                msg = f"upstream error: {exc}".encode()
                self.send_response(502)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
            finally:
                conn.close()

        def do_GET(self):  # noqa: N802
            self._proxy()

        def do_POST(self):  # noqa: N802
            self._proxy()

        def do_PUT(self):  # noqa: N802
            self._proxy()

        def do_PATCH(self):  # noqa: N802
            self._proxy()

        def do_DELETE(self):  # noqa: N802
            self._proxy()

        def do_HEAD(self):  # noqa: N802
            self._proxy()

        def do_OPTIONS(self):  # noqa: N802
            self._proxy()

    return ProxyHandler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--ignore-paths", default="[]")
    args = parser.parse_args()

    ignore = json.loads(args.ignore_paths)
    tracker = CountTracker(ignore)
    open(args.log, "a", encoding="utf-8").close()
    handler = make_handler(args.upstream_host, args.upstream_port, args.log, tracker)
    server = ThreadingHTTPServer((args.listen_host, args.listen_port), handler)
    print(f"proxy listening on {args.listen_host}:{args.listen_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "4173"))
PR = os.environ.get("PREVIEW_PR_NUMBER") or os.environ.get("GITHUB_PR_NUMBER") or ""
BRANCH = os.environ.get("PREVIEW_BRANCH") or os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or "local"


def greeting() -> str:
    # Prefer a live-editable override file so agent edits show after refresh.
    override = ROOT / "greeting.txt"
    if override.is_file():
        text = override.read_text(encoding="utf-8").strip()
        if text:
            return text
    if PR:
        return f"Hello from PR #{PR}"
    return f"Hello from {BRANCH}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            html = (ROOT / "index.html").read_text(encoding="utf-8").replace("__GREETING__", greeting())
            self._send(200, html.encode(), "text/html; charset=utf-8")
            return
        if path == "/poll":
            self._send(200, b'{"ok":true}', "application/json")
            return
        if path == "/ping":
            payload = json.dumps({"ok": True, "t": int(time.time())}).encode()
            self._send(200, payload, "application/json")
            return
        file_path = (ROOT / path.lstrip("/")).resolve()
        if ROOT in file_path.parents or file_path == ROOT:
            if file_path.is_file():
                data = file_path.read_bytes()
                ctype = "application/octet-stream"
                if file_path.suffix == ".js":
                    ctype = "application/javascript"
                elif file_path.suffix == ".css":
                    ctype = "text/css"
                elif file_path.suffix == ".html":
                    ctype = "text/html; charset=utf-8"
                self._send(200, data, ctype)
                return
        self._send(404, b"not found", "text/plain")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"hello-world on 127.0.0.1:{PORT} :: {greeting()}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

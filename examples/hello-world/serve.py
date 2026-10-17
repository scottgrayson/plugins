#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "4173"))
PR = os.environ.get("PREVIEW_PR_NUMBER") or os.environ.get("GITHUB_PR_NUMBER") or ""
BRANCH = os.environ.get("PREVIEW_BRANCH") or os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or "local"


def greeting() -> str:
    if PR:
        return f"Hello from PR #{PR}"
    return f"Hello from {BRANCH}"


INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>preview hello-world</title>
  <style>
    :root { color-scheme: light; --ink: #14201b; --accent: #0f6b4c; --wash: #e7f3ec; }
    body { margin: 0; min-height: 100vh; font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(circle at 20% 10%, #f7fff9, #d9ebe1 55%, #c5ddd1);
      color: var(--ink); display: grid; place-items: center; }
    main { text-align: center; padding: 2rem; }
    h1 { font-size: clamp(2rem, 6vw, 3.5rem); margin: 0 0 1rem; letter-spacing: -0.03em; }
    button { appearance: none; border: 0; background: var(--accent); color: white; font: inherit;
      padding: 0.7rem 1.2rem; border-radius: 0.4rem; cursor: pointer; }
    #ping { margin-top: 1rem; min-height: 1.5rem; color: #355246; }
  </style>
</head>
<body>
  <main>
    <h1 id="greet">__GREETING__</h1>
    <button id="ping-btn" type="button">Ping</button>
    <p id="ping"></p>
  </main>
  <script>
    setInterval(() => { fetch("/poll").catch(() => {}); }, 29000);
    document.getElementById("ping-btn").addEventListener("click", async () => {
      const r = await fetch("/ping");
      const j = await r.json();
      document.getElementById("ping").textContent = "pong " + j.t;
    });
  </script>
</body>
</html>
"""


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
            html = INDEX.replace("__GREETING__", greeting()).encode()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/poll":
            self._send(200, b'{"ok":true}', "application/json")
            return
        if path == "/ping":
            import time
            payload = json.dumps({"ok": True, "t": int(time.time())}).encode()
            self._send(200, payload, "application/json")
            return
        file_path = ROOT / path.lstrip("/")
        if file_path.is_file() and ROOT in file_path.resolve().parents | {ROOT}:
            data = file_path.read_bytes()
            ctype = "application/octet-stream"
            if file_path.suffix == ".js":
                ctype = "application/javascript"
            elif file_path.suffix == ".css":
                ctype = "text/css"
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

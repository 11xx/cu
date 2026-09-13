"""An explicitly started loopback server for the packaged CU playground."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from urllib.parse import urlsplit


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/":
            self.send_error(404)
            return
        body = files("cu").joinpath("playground.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def serve(port: int, *, as_json: bool = False) -> None:
    """Serve only the fixture until Ctrl-C, without launching a browser."""
    if not 0 <= port <= 65535:
        raise ValueError("--port must be between 0 and 65535")
    with HTTPServer(("127.0.0.1", port), Handler) as server:
        server.timeout = 0.5
        url = f"http://127.0.0.1:{server.server_port}/"
        announcement = {"url": url, "fixture": "cu-playground/1", "extension_required": False}
        print(json.dumps(announcement) if as_json else (
            f"CU playground: {url}\nOpen explicitly in your browser. Ctrl-C stops the server."
        ), flush=True)
        try:
            while True:
                server.handle_request()
        except KeyboardInterrupt:
            return

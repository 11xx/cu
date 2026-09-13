"""Serve the packaged extension-free native CU playground on loopback."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from urllib.parse import urlsplit

ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/state.js": ("state.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def asset(path: str) -> tuple[str, bytes] | None:
    """Return one declared asset, refusing every other loopback path."""
    declaration = ASSETS.get(urlsplit(path).path)
    if declaration is None:
        return None
    name, content_type = declaration
    return content_type, files("cu.native_playground").joinpath(name).read_bytes()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        served = asset(self.path)
        if served is None:
            self.send_error(404)
            return
        content_type, body = served
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def serve(port: int, *, as_json: bool = False) -> None:
    """Serve declared native assets until Ctrl-C without launching a browser."""
    if not 0 <= port <= 65535:
        raise ValueError("--port must be between 0 and 65535")
    with HTTPServer(("127.0.0.1", port), Handler) as server:
        server.timeout = 0.5
        url = f"http://127.0.0.1:{server.server_port}/"
        announcement = {
            "url": url,
            "suite": "native",
            "fixture": "cu-native-playground/1",
            "assets": sorted(ASSETS),
            "extension_required": False,
            "lifecycle": "foreground-until-interrupted",
        }
        print(
            json.dumps(announcement)
            if as_json
            else (
                f"CU native playground: {url}\n"
                "Open explicitly in your browser. Ctrl-C stops the server."
            ),
            flush=True,
        )
        try:
            while True:
                server.handle_request()
        except KeyboardInterrupt:
            return

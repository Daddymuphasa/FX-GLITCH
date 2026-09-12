"""Vercel Python entry: class name `handler` is required by the runtime."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .http_api import dispatch

PATH_ALIASES = {
    "/api/auth_code": "/api/auth/code",
    "/api/auth_logout": "/api/auth/logout",
    "/api/auth_login": "/api/auth/login",
    "/api/auth_register": "/api/auth/register",
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._run("GET")

    def do_POST(self):
        self._run("POST")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, *_args):
        return

    def _run(self, method: str) -> None:
        parsed = urlparse(self.path)
        payload = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                payload = {}
        path = PATH_ALIASES.get(parsed.path, parsed.path)
        result = dispatch(
            method, path, parse_qs(parsed.query), payload, dict(self.headers)
        )
        status, content_type, body, *rest = result
        extra = rest[0] if rest else {}
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Credentials", "true")
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

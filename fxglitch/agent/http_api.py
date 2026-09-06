"""JSON API for the judge dashboard. Stdlib only."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from ..venues.binance import Binance
from .mcp_server import TOOLS
from .positioning import fetch_briefing
from .session import from_signal, judge_demo, run_cycle

HACKATHON = {
    "event": "Binance Agent OS Mini Hackathon",
    "source": "https://www.binance.com/en/blog/community/8802181509900814931",
    "announcement": "https://x.com/binance/status/2096025526339010723",
    "deadline_utc": "2026-09-08T23:59:00Z",
    "track": "B — Connect your MCPs and trade (also usable as Track A agent)",
    "prize_pool": "Track A 20,000 USDC / Track B 40,000 USDC / total 60,000 USDC",
    "published_entry_steps": [
        "Follow @Binance and repost the official announcement",
        "Reply or quote-repost with the submission (Track A: video/demo + GitHub)",
        "Complete the survey: https://www.binance.com/en/survey/2913aa200aac462c89a737779393f3d4",
    ],
    "what_this_repo_covers": [
        "Custom MCP (stdio) exposing positioning, policy, dry-run/live cycle",
        "Documented pairing with official Binance MCP https://agent.binance.com/mcp/agentic",
        "USDⓈ-M futures venue (HMAC, filters, protective stops)",
        "Working demo dashboard + offline 60-second judge flow",
        "GitHub repo with tests",
    ],
    "what_you_must_do_yourself": [
        "Follow, repost, reply, and submit the survey from your own accounts",
        "Confirm you are not in a restricted jurisdiction (US, UK, EEA, HK, SG, and Binance prohibited list)",
        "Record a short demo video if entering Track A",
    ],
    "unpublished": "Binance did not publish a numeric judging rubric. This file does not invent one.",
}


def _json(payload, status=200):
    body = json.dumps(payload, default=str).encode()
    return status, "application/json; charset=utf-8", body


def dispatch(method: str, path: str, query: dict, body: dict):
    if path == "/api/health":
        return _json({"ok": True, "product": "FX-GLITCH Agent OS", "dry_run": True})
    if path == "/api/hackathon":
        return _json(HACKATHON)
    if path == "/api/tools":
        return _json({"tools": TOOLS, "binance_mcp": "https://agent.binance.com/mcp/agentic"})
    if path == "/api/demo":
        return _json({"cases": judge_demo()})
    if path == "/api/briefing":
        symbol = (query.get("symbol") or ["BTCUSDT"])[0]
        try:
            return _json(fetch_briefing(symbol).to_dict())
        except RuntimeError as exc:
            return _json({"error": str(exc), "hint": "Use /api/demo if Binance is unreachable"}, 503)
    if path == "/api/cycle" and method == "POST":
        # Public host never sends orders. Local --live still works via CLI.
        live = bool(body.get("live")) and not os.environ.get("VERCEL")
        symbol = body.get("symbol") or "BTCUSDT"
        try:
            result = run_cycle(
                symbol,
                message=body.get("message"),
                live=live,
                venue=Binance() if live else None,
            )
            return _json(result.to_dict())
        except RuntimeError as exc:
            return _json({"error": str(exc)}, 503)
    if path == "/api/signal" and method == "POST":
        message = body.get("message") or ""
        proposal = from_signal(message)
        return _json(proposal.to_dict())
    if path not in ("/api/health", "/api/hackathon", "/api/tools", "/api/demo",
                    "/api/briefing", "/api/cycle", "/api/signal"):
        return _json({"error": "not found"}, 404)
    return _json({"error": "method not allowed"}, 405)


class Handler(BaseHTTPRequestHandler):
    web_root = ""

    def log_message(self, fmt, *args):
        sys_stderr = __import__("sys").stderr
        sys_stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            status, ctype, body = dispatch("GET", parsed.path, parse_qs(parsed.query), {})
            self._send(status, ctype, body)
            return
        self._static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            payload = {}
        status, ctype, body = dispatch("POST", parsed.path, parse_qs(parsed.query), payload)
        self._send(status, ctype, body)

    def _static(self, path: str):
        import os
        if path in ("/", "/index.html"):
            path = "/index.html"
        safe = os.path.normpath(path).lstrip("\\/")
        full = os.path.join(self.web_root, safe)
        if not os.path.abspath(full).startswith(os.path.abspath(self.web_root)):
            self._send(403, "text/plain", b"forbidden")
            return
        if not os.path.isfile(full):
            self._send(404, "text/plain", b"not found")
            return
        ext = os.path.splitext(full)[1]
        types = {".html": "text/html; charset=utf-8", ".css": "text/css",
                 ".js": "application/javascript", ".svg": "image/svg+xml"}
        with open(full, "rb") as fh:
            self._send(200, types.get(ext, "application/octet-stream"), fh.read())

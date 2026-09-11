"""JSON API for the judge dashboard. Stdlib only."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from ..venues.binance import Binance
from ..venues.bitunix import Bitunix, from_slot, listed_accounts
from ..venues.base import VenueError
from . import access as access_codes
from . import auth as passkeys
from .whatsapp_user import bridge as wa_bridge
from .inbox import ingest, list_signals, publish_live, replace_signals
from .mcp_server import TOOLS
from .plans import place_plan, plan_by_id, proposal_from_plan, recommend
from .positioning import fetch_briefing
from .session import from_signal, judge_demo, paper_balance, run_cycle
from .telegram_in import ingest_update
from .telegram_user import bridge

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


def _slot(value) -> int:
    try:
        slot = int(value)
    except (TypeError, ValueError):
        slot = 1
    return slot if slot in (1, 2, 3) else 1


def _bitunix(slot: int = 1) -> Bitunix:
    """Desk credentials only — never from the browser."""
    return from_slot(slot)


def _context(symbol: str | None, slot: int = 1):
    instruments = None
    mark = None
    balance = None
    try:
        venue = _bitunix(slot)
        instruments = venue.instruments()
        if not instruments:
            instruments = None
        if symbol:
            mark = venue.mark_price(symbol)
        if venue.authenticated:
            balance = venue.balance()
    except Exception:
        pass
    return instruments, mark, balance


def _recommend_body(body: dict):
    message = (body.get("message") or "").strip()
    if not message:
        return _json({"error": "message is required"}, 400)
    equity = float(body.get("equity") or 1000)
    hint_mark = body.get("mark")
    try:
        hint_mark = float(hint_mark) if hint_mark not in (None, "") else None
    except (TypeError, ValueError):
        hint_mark = None
    slot = _slot(body.get("account"))
    instruments, mark, balance = _context(None, slot)
    mark = hint_mark or mark
    rec_once = recommend(message, equity=equity, mark=mark, instruments=instruments,
                         balance=balance)
    parsed_symbol = rec_once.bitunix_symbol or rec_once.binance_symbol
    if parsed_symbol and mark is None:
        instruments, mark, balance = _context(parsed_symbol, slot)
        rec_once = recommend(message, equity=(balance.equity if balance else equity),
                             mark=mark, instruments=instruments, balance=balance)
    return _json(rec_once.to_dict())


def _host(headers: dict | None) -> str:
    headers = headers or {}
    return (headers.get("Host") or headers.get("host") or "localhost").split(",")[0].strip()


def _cookie_headers(token: str, headers: dict | None, *, clear: bool = False) -> dict:
    headers = headers or {}
    origin = passkeys.origin_for(headers, _host(headers))
    return {
        "Set-Cookie": passkeys.cookie_header(
            token or "",
            clear=clear,
            secure=origin.startswith("https"),
        )
    }


def _auth_error(exc: Exception, status: int = 400):
    return _json({"error": str(exc)}, status)


def _who(headers: dict) -> dict | None:
    user = access_codes.current_user(headers)
    if user:
        return user
    pk = passkeys.current_user(headers)
    if not pk:
        return None
    out = passkeys.public_user(pk)
    out["account"] = 1 if out.get("admin") else None
    out["via"] = "passkey"
    return out


def dispatch(method: str, path: str, query: dict, body: dict, headers: dict | None = None):
    headers = headers or {}
    user = _who(headers)
    if path == "/api/me":
        return _json(user or {"signed_in": False, "admin": False})
    if path == "/api/auth/code" and method == "POST":
        found = access_codes.unlock(str(body.get("code") or body.get("password") or ""))
        if not found:
            return _json({"error": "That access code does not match an account."}, 401)
        token = access_codes.mint(found)
        origin = passkeys.origin_for(headers, _host(headers))
        extra = {"Set-Cookie": access_codes.cookie_header(token, secure=origin.startswith("https"))}
        return (*_json(found), extra)
    if path == "/api/auth/logout" and method == "POST":
        passkeys.logout(headers)
        origin = passkeys.origin_for(headers, _host(headers))
        extra = {
            "Set-Cookie": passkeys.cookie_header("", clear=True, secure=origin.startswith("https")),
        }
        # two cookies: clear both
        extra = {
            "Set-Cookie": [
                passkeys.cookie_header("", clear=True, secure=origin.startswith("https")),
                access_codes.cookie_header("", clear=True, secure=origin.startswith("https")),
            ]
        }
        return (*_json({"ok": True, "signed_in": False}), extra)
    if path == "/api/auth/register" and method == "POST":
        action = body.get("action") or "begin"
        try:
            if action == "begin":
                return _json(passkeys.begin_register(str(body.get("name") or ""), headers, _host(headers)))
            user_out, token = passkeys.finish_register(
                str(body.get("flow_id") or ""), body.get("credential") or {}, headers, _host(headers)
            )
            return (*_json(user_out), _cookie_headers(token, headers))
        except Exception as exc:
            return _auth_error(exc)
    if path == "/api/auth/login" and method == "POST":
        action = body.get("action") or "begin"
        try:
            if action == "begin":
                return _json(passkeys.begin_login(str(body.get("name") or ""), headers, _host(headers)))
            user_out, token = passkeys.finish_login(
                str(body.get("flow_id") or ""), body.get("credential") or {}, headers, _host(headers)
            )
            return (*_json(user_out), _cookie_headers(token, headers))
        except Exception as exc:
            return _auth_error(exc)
    if path == "/api/book" and method == "GET":
        if not user:
            return _json({"error": "Unlock with your passkey first.", "signals": []}, 401)
        return _json({"fills": passkeys.load_book(user["id"])})
    if path == "/api/book" and method == "POST":
        if not user:
            return _json({"error": "Unlock with your passkey first."}, 401)
        fill = body.get("fill") if isinstance(body.get("fill"), dict) else body
        rows = passkeys.save_fill(user["id"], fill)
        return _json({"ok": True, "fills": rows})
    if path == "/api/health":
        accounts = listed_accounts()
        live_ok = any(a["ready"] for a in accounts) and not os.environ.get("VERCEL")
        return _json({
            "ok": True,
            "product": "FX-GLITCH Agent OS",
            "dry_run": not live_ok,
            "bitunix": live_ok,
            "bitunix_accounts": accounts,
            "binance": bool(Binance().authenticated),
            "venue": "bitunix",
            "live_allowed": live_ok,
            "user_keys_ok": True,
        })
    if path == "/api/account":
        if not user or not user.get("admin"):
            return _json({"error": "operator only"}, 401)
        slot = _slot((body or {}).get("account") or (query.get("account") or ["1"])[0])
        venue = _bitunix(slot)
        accounts = listed_accounts()
        if not venue.authenticated:
            return _json({"venue": "bitunix", "account": slot, "authenticated": False,
                          "accounts": accounts})
        try:
            bal = venue.balance()
        except VenueError as exc:
            return _json({"venue": "bitunix", "account": slot, "authenticated": True,
                          "accounts": accounts, "error": str(exc)}, 400)
        return _json({
            "venue": "bitunix",
            "account": slot,
            "name": getattr(venue, "account_name", f"account-{slot}"),
            "authenticated": True,
            "accounts": accounts,
            "equity": bal.equity,
            "available": bal.available,
            "currency": bal.currency,
        })
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
    if path == "/api/inbox" and method == "GET":
        snap = bridge.snapshot()
        return _json({"signals": list_signals(),
                      "telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN")) or snap.get("status") in ("linked", "watching"),
                      "account": snap})
    if path == "/api/inbox" and method == "POST":
        try:
            item = ingest(body.get("message") or "", source=body.get("source") or "paste")
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        return _json(item)
    if path == "/api/recommend" and method == "POST":
        return _recommend_body(body)
    if path == "/api/execute" and method == "POST":
        message = (body.get("message") or "").strip()
        plan_id = body.get("plan") or "mid"
        if not message:
            return _json({"error": "message is required"}, 400)
        if not user:
            return _json({"error": "Enter your access code to take a trade."}, 401)
        equity = float(body.get("equity") or 1000)
        slot = _slot(user.get("account") or body.get("account"))
        if user.get("account") and not user.get("admin"):
            slot = _slot(user.get("account"))
        venue = _bitunix(slot)
        instruments, mark, balance = _context(None, slot)
        rec = recommend(message, equity=equity, mark=mark, instruments=instruments, balance=balance)
        symbol = rec.bitunix_symbol or rec.binance_symbol
        if symbol and mark is None:
            instruments, mark, balance = _context(symbol, slot)
            rec = recommend(message, equity=(balance.equity if balance else equity),
                            mark=mark, instruments=instruments, balance=balance)
        plan = plan_by_id(rec, plan_id)
        if plan is None:
            return _json({"error": f"unknown plan {plan_id}"}, 400)
        if not plan["policy_ok"]:
            return _json({"error": plan["policy_reason"], "recommendation": rec.to_dict()}, 400)
        proposal = proposal_from_plan(rec, plan)
        want_live = bool(body.get("live")) and bool(body.get("confirm"))
        if want_live and not (user.get("admin") or user.get("account")):
            want_live = False
        if want_live and os.environ.get("VERCEL") and not user.get("account"):
            return _json({
                "error": "Live Bitunix needs an access code on this site.",
                "dry_run": True,
                "plan": plan,
            }, 400)
        if want_live and not venue.authenticated:
            env_hint = "BITUNIX_API_KEY / BITUNIX_SECRET_KEY" if slot == 1 else "BITUNIX_API_KEY_2 / BITUNIX_SECRET_KEY_2"
            return _json({
                "error": f"Account {slot} has no keys. Set {env_hint} in .env, then restart.",
                "dry_run": True,
                "plan": plan,
                "account": slot,
            }, 400)
        fill = {
            "direction": rec.direction,
            "symbol": rec.bitunix_symbol or rec.binance_symbol,
            "plan": plan.get("label"),
            "leverage": plan.get("leverage"),
            "stop": plan.get("stop"),
            "take_profit": plan.get("take_profit"),
            "venue": "bitunix" if want_live else "paper",
            "account": slot if want_live else None,
            "at": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        }
        if want_live:
            try:
                placed = place_plan(venue, rec, plan)
            except VenueError as exc:
                return _json({"error": str(exc), "dry_run": True, "plan": plan, "account": slot}, 400)
            fill["order_id"] = placed.get("order_id")
            fill["venue"] = "bitunix"
            payload = {
                "sent": True, "dry_run": False, "venue": "bitunix",
                "account": slot,
                "account_name": getattr(venue, "account_name", f"account-{slot}"),
                "order": {"id": placed["order_id"], "message": placed.get("message") or ""},
                "plan": plan, "recommendation": rec.to_dict(),
                "qty": placed.get("qty"),
                "fills": passkeys.save_fill(user["id"], fill),
            }
            return _json(payload)
        return _json({
            "sent": False, "dry_run": True, "venue": "paper", "account": slot,
            "plan": plan, "proposal": proposal.to_dict(), "recommendation": rec.to_dict(),
            "fills": passkeys.save_fill(user["id"], fill),
            "hint": "Saved to your passkey account. Live Bitunix is operator-only.",
        })
    if path == "/api/signals" and method == "GET":
        return _json({"signals": list_signals()})
    if path == "/api/signals" and method == "POST":
        rows = body.get("signals") if isinstance(body.get("signals"), list) else []
        return _json({"ok": True, "signals": replace_signals(rows)})
    if path == "/api/telegram" and method == "GET":
        if not user or not user.get("admin"):
            return _json({"status": "signed_out", "error": "operator only"}, 401)
        action = (query.get("action") or ["status"])[0]
        if action == "qr":
            return _json(bridge.ensure_qr())
        if action == "chats":
            return _json(bridge.chats())
        if action == "scan":
            result = bridge.scan_today()
            result["signals"] = publish_live()
            result["account"] = bridge.snapshot()
            return _json(result)
        return _json(bridge.snapshot())
    if path == "/api/whatsapp" and method == "GET":
        if not user or not user.get("admin"):
            return _json({"status": "signed_out", "error": "operator only"}, 401)
        action = (query.get("action") or ["status"])[0]
        if action == "qr":
            return _json(wa_bridge.ensure_qr())
        return _json(wa_bridge.snapshot())
    if path == "/api/whatsapp" and method == "POST":
        if not user or not user.get("admin"):
            return _json({"error": "operator only"}, 401)
        return _json(wa_bridge.ensure_qr())
    if path == "/api/telegram" and method == "POST":
        action = body.get("action")
        if action in ("watch", "password", "qr"):
            if not user or not user.get("admin"):
                return _json({"error": "operator only"}, 401)
        if action == "watch":
            return _json(bridge.set_watch(str(body.get("chat_id") or ""), body.get("title") or ""))
        if action == "password":
            return _json(bridge.set_password(str(body.get("password") or "")))
        if action == "qr":
            return _json(bridge.ensure_qr())
        item = ingest_update(body if "update_id" in body or "channel_post" in body
                             else {"message": {"text": body.get("text") or body.get("message"),
                                               "message_id": body.get("update_id", 0),
                                               "chat": {"id": "manual"}}})
        if item is None and body.get("message") and not isinstance(body.get("message"), dict):
            item = ingest(str(body.get("message")), source="telegram")
        return _json({"ok": True, "item": item})
    if path not in ("/api/health", "/api/me", "/api/book", "/api/auth/register", "/api/auth/login",
                    "/api/auth/logout", "/api/auth/code", "/api/whatsapp", "/api/account", "/api/hackathon", "/api/tools", "/api/demo",
                    "/api/briefing", "/api/cycle", "/api/signal", "/api/inbox",
                    "/api/recommend", "/api/execute", "/api/telegram", "/api/signals"):
        return _json({"error": "not found"}, 404)
    return _json({"error": "method not allowed"}, 405)


class Handler(BaseHTTPRequestHandler):
    web_root = ""

    def log_message(self, fmt, *args):
        sys_stderr = __import__("sys").stderr
        sys_stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, content_type, body, extra=None):
        extra = extra or {}
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra.items():
            if key.lower() == "set-cookie" and isinstance(value, (list, tuple)):
                for item in value:
                    self.send_header("Set-Cookie", item)
            else:
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            result = dispatch("GET", parsed.path, parse_qs(parsed.query), {}, dict(self.headers))
            status, ctype, body, *rest = result
            self._send(status, ctype, body, rest[0] if rest else {})
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
        result = dispatch("POST", parsed.path, parse_qs(parsed.query), payload, dict(self.headers))
        status, ctype, body, *rest = result
        self._send(status, ctype, body, rest[0] if rest else {})

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
                 ".js": "application/javascript", ".svg": "image/svg+xml",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".webp": "image/webp", ".ico": "image/x-icon",
                 ".json": "application/json; charset=utf-8"}
        with open(full, "rb") as fh:
            self._send(200, types.get(ext, "application/octet-stream"), fh.read())

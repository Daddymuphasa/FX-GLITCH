"""Access codes that unlock a Bitunix slot. Codes stay in .env, never in the browser."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path

from ..venues.bitunix import SLOTS, access_code, slot_credentials

ROOT = Path(__file__).resolve().parents[2]
COOKIE = "fxg_acc"
TTL = 30 * 24 * 3600


def _secret() -> bytes:
    raw = (os.environ.get("FXGLITCH_SESSION_SECRET") or "").strip()
    if raw:
        return raw.encode()
    path = ROOT / "data" / "session.secret"
    try:
        if path.exists():
            return path.read_bytes().strip()
    except OSError:
        pass
    blob = os.urandom(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        return blob
    except OSError:
        return b"fxglitch-dev-secret"


def unlock(code: str) -> dict | None:
    """Return a public user dict if the code matches a slot. Never echo the code."""
    given = (code or "").strip().encode()
    if not given:
        return None
    operator = (os.environ.get("FXGLITCH_OPERATOR_ACCESS") or "").strip().encode()
    if operator and hmac.compare_digest(given, operator):
        _k, _s, name = slot_credentials(1)
        return {
            "signed_in": True,
            "id": "operator",
            "name": name or "operator",
            "admin": True,
            "account": 1,
            "via": "access",
        }
    for slot in SLOTS:
        expected = access_code(slot).encode()
        if not expected:
            continue
        if hmac.compare_digest(given, expected):
            _k, _s, name = slot_credentials(slot)
            admin = (not operator) and slot == 1
            return {
                "signed_in": True,
                "id": f"slot-{slot}",
                "name": name,
                "admin": admin,
                "account": slot,
                "via": "access",
            }
    return None


def mint(user: dict) -> str:
    exp = int(time.time()) + TTL
    body = f"{int(user['account'])}|{int(bool(user.get('admin')))}|{exp}"
    mac = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}|{mac}"


def read_token(token: str) -> dict | None:
    parts = (token or "").split("|")
    if len(parts) != 4:
        return None
    slot_s, admin_s, exp_s, mac = parts
    body = f"{slot_s}|{admin_s}|{exp_s}"
    expect = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, expect):
        return None
    try:
        if int(exp_s) < int(time.time()):
            return None
        slot = int(slot_s)
        admin = bool(int(admin_s))
    except ValueError:
        return None
    if slot not in SLOTS:
        return None
    _k, _s, name = slot_credentials(slot)
    return {
        "signed_in": True,
        "id": f"slot-{slot}",
        "name": name,
        "admin": admin,
        "account": slot,
        "via": "access",
    }


def parse_cookie(headers: dict) -> str:
    raw = headers.get("Cookie") or headers.get("cookie") or ""
    for part in raw.split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        if key == COOKIE:
            return value.strip()
    return ""


def cookie_header(token: str, *, clear: bool = False, secure: bool = False) -> str:
    if clear:
        return f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={TTL}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def current_user(headers: dict | None) -> dict | None:
    token = parse_cookie(headers or {})
    if not token:
        return None
    return read_token(token)

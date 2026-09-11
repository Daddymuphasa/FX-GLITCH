"""Passkey (WebAuthn) accounts for the desk.

Credentials live in data/auth.json. Sessions are httpOnly cookies.
Nothing in here is an exchange API key.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = threading.Lock()
COOKIE = "fxg_sid"
SESSION_TTL = 30 * 24 * 3600
FLOW_TTL = 300

def _auth_path() -> Path:
    if os.environ.get("VERCEL"):
        return Path("/tmp/fxglitch-auth.json")
    return Path(os.environ.get("FXGLITCH_AUTH", str(ROOT / "data" / "auth.json")))


def _book_dir() -> Path:
    if os.environ.get("VERCEL"):
        return Path("/tmp/fxglitch-books")
    return Path(os.environ.get("FXGLITCH_BOOKS", str(ROOT / "data" / "books")))


def _empty() -> dict:
    return {"users": [], "sessions": {}, "flows": {}}


def _load() -> dict:
    try:
        data = json.loads(_auth_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and "users" in data:
            data.setdefault("sessions", {})
            data.setdefault("flows", {})
            return data
    except (OSError, ValueError):
        pass
    return _empty()


def _save(data: dict) -> None:
    path = _auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _now() -> float:
    return time.time()


def _purge(data: dict) -> None:
    now = _now()
    data["sessions"] = {
        k: v for k, v in data.get("sessions", {}).items()
        if float(v.get("exp") or 0) > now
    }
    data["flows"] = {
        k: v for k, v in data.get("flows", {}).items()
        if float(v.get("exp") or 0) > now
    }


def _b64(raw: bytes) -> str:
    from webauthn.helpers import bytes_to_base64url
    return bytes_to_base64url(raw)


def _unb64(text: str) -> bytes:
    from webauthn.helpers import base64url_to_bytes
    return base64url_to_bytes(text)


def rp_id_for(host: str) -> str:
    host = (host or "").split(":")[0].strip().lower()
    if host in ("", "127.0.0.1", "::1", "0.0.0.0"):
        return "localhost"
    return host


def origin_for(headers: dict, host: str) -> str:
    origin = (headers.get("Origin") or headers.get("origin") or "").strip()
    if origin:
        return origin.rstrip("/")
    proto = "https"
    forwarded = (headers.get("X-Forwarded-Proto") or headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if forwarded:
        proto = forwarded
    elif rp_id_for(host) == "localhost":
        proto = "http"
    hostname = host if ":" in (host or "") else f"{host or 'localhost'}:8765"
    if hostname.startswith("127.0.0.1"):
        hostname = hostname.replace("127.0.0.1", "localhost", 1)
    return f"{proto}://{hostname}"


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
    parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={SESSION_TTL}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def current_user(headers: dict) -> dict | None:
    token = parse_cookie(headers or {})
    if not token:
        return None
    with LOCK:
        data = _load()
        _purge(data)
        row = data.get("sessions", {}).get(token)
        if not row:
            return None
        for user in data["users"]:
            if user["id"] == row.get("user_id"):
                return {
                    "id": user["id"],
                    "name": user["name"],
                    "admin": bool(user.get("admin")),
                }
    return None


def public_user(user: dict | None) -> dict:
    if not user:
        return {"signed_in": False, "admin": False}
    return {"signed_in": True, "id": user["id"], "name": user["name"], "admin": bool(user.get("admin"))}


def _user_by_name(data: dict, name: str) -> dict | None:
    wanted = name.strip().lower()
    for user in data["users"]:
        if user["name"].strip().lower() == wanted:
            return user
    return None


def _user_by_cred(data: dict, cred_id: str) -> tuple[dict, dict] | tuple[None, None]:
    for user in data["users"]:
        for cred in user.get("credentials") or []:
            if cred.get("id") == cred_id:
                return user, cred
    return None, None


def begin_register(name: str, headers: dict, host: str) -> dict:
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    name = " ".join((name or "").split())
    if len(name) < 2 or len(name) > 32:
        raise ValueError("Name must be 2–32 characters.")
    rp_id = rp_id_for(host)
    with LOCK:
        data = _load()
        _purge(data)
        existing = _user_by_name(data, name)
        user_id = existing["id"] if existing else secrets.token_hex(16)
        exclude = []
        if existing:
            for cred in existing.get("credentials") or []:
                exclude.append(PublicKeyCredentialDescriptor(id=_unb64(cred["id"])))
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name="FX-GLITCH",
            user_name=name,
            user_id=bytes.fromhex(user_id) if len(user_id) == 32 else user_id.encode(),
            user_display_name=name,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            exclude_credentials=exclude or None,
        )
        flow_id = secrets.token_urlsafe(16)
        data["flows"][flow_id] = {
            "type": "register",
            "name": name,
            "user_id": user_id,
            "challenge": _b64(options.challenge),
            "exp": _now() + FLOW_TTL,
        }
        _save(data)
    return {"flow_id": flow_id, "options": json.loads(options_to_json(options))}


def finish_register(flow_id: str, credential: dict, headers: dict, host: str) -> tuple[dict, str]:
    from webauthn import verify_registration_response

    with LOCK:
        data = _load()
        _purge(data)
        flow = data.get("flows", {}).pop(flow_id or "", None)
        if not flow or flow.get("type") != "register":
            raise ValueError("Passkey setup expired. Try again.")
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=_unb64(flow["challenge"]),
            expected_rp_id=rp_id_for(host),
            expected_origin=origin_for(headers, host),
            require_user_verification=False,
        )
        cred = {
            "id": _b64(verified.credential_id),
            "public_key": _b64(verified.credential_public_key),
            "sign_count": int(verified.sign_count),
        }
        user = None
        for row in data["users"]:
            if row["id"] == flow["user_id"]:
                user = row
                break
        if user is None:
            user = {
                "id": flow["user_id"],
                "name": flow["name"],
                "admin": not any(u.get("admin") for u in data["users"]),
                "credentials": [],
            }
            data["users"].append(user)
        user["credentials"] = [c for c in user.get("credentials") or [] if c.get("id") != cred["id"]]
        user["credentials"].append(cred)
        token = secrets.token_urlsafe(24)
        data["sessions"][token] = {
            "user_id": user["id"],
            "exp": _now() + SESSION_TTL,
        }
        _save(data)
    return public_user({
        "id": user["id"],
        "name": user["name"],
        "admin": user.get("admin"),
    }), token


def begin_login(name: str, headers: dict, host: str) -> dict:
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import (
        PublicKeyCredentialDescriptor,
        UserVerificationRequirement,
    )

    rp_id = rp_id_for(host)
    allow = None
    user_id = ""
    with LOCK:
        data = _load()
        _purge(data)
        if name.strip():
            user = _user_by_name(data, name)
            if user is None:
                raise ValueError("No passkey on this desk for that name.")
            user_id = user["id"]
            allow = [
                PublicKeyCredentialDescriptor(id=_unb64(c["id"]))
                for c in user.get("credentials") or []
            ]
            if not allow:
                raise ValueError("That account has no passkey yet.")
        options = generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=allow,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        flow_id = secrets.token_urlsafe(16)
        data["flows"][flow_id] = {
            "type": "login",
            "user_id": user_id,
            "challenge": _b64(options.challenge),
            "exp": _now() + FLOW_TTL,
        }
        _save(data)
    return {"flow_id": flow_id, "options": json.loads(options_to_json(options))}


def finish_login(flow_id: str, credential: dict, headers: dict, host: str) -> tuple[dict, str]:
    from webauthn import verify_authentication_response

    raw_id = credential.get("rawId") or credential.get("id") or ""
    with LOCK:
        data = _load()
        _purge(data)
        flow = data.get("flows", {}).pop(flow_id or "", None)
        if not flow or flow.get("type") != "login":
            raise ValueError("Passkey sign-in expired. Try again.")
        user, cred = _user_by_cred(data, raw_id)
        if user is None or cred is None:
            raise ValueError("This passkey is not registered on this desk.")
        if flow.get("user_id") and flow["user_id"] != user["id"]:
            raise ValueError("Passkey does not match that name.")
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=_unb64(flow["challenge"]),
            expected_rp_id=rp_id_for(host),
            expected_origin=origin_for(headers, host),
            credential_public_key=_unb64(cred["public_key"]),
            credential_current_sign_count=int(cred.get("sign_count") or 0),
            require_user_verification=False,
        )
        cred["sign_count"] = int(verified.new_sign_count)
        token = secrets.token_urlsafe(24)
        data["sessions"][token] = {
            "user_id": user["id"],
            "exp": _now() + SESSION_TTL,
        }
        _save(data)
    return public_user({
        "id": user["id"],
        "name": user["name"],
        "admin": user.get("admin"),
    }), token


def logout(headers: dict) -> None:
    token = parse_cookie(headers or {})
    if not token:
        return
    with LOCK:
        data = _load()
        data.get("sessions", {}).pop(token, None)
        _save(data)


def book_path(user_id: str) -> Path:
    folder = _book_dir()
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in user_id if ch.isalnum())[:40]
    return folder / f"{safe}.json"


def load_book(user_id: str) -> list:
    path = book_path(user_id)
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def save_fill(user_id: str, fill: dict) -> list:
    rows = load_book(user_id)
    rows.insert(0, fill)
    rows = rows[:80]
    path = book_path(user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    tmp.replace(path)
    return rows

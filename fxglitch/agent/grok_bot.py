"""FX-GLITCH Grok operator bot (Telegram DMs).

Natural-language desk control via xAI Grok + desk_tools.
Cosmas group ingestion stays on telegram_user (Telethon); this bot is the operator surface.

Env:
  XAI_API_KEY
  FXGLITCH_GROK_BOT_TOKEN  (falls back to TELEGRAM_BOT_TOKEN)
  FXGLITCH_GROK_BOT_ALLOW_IDS  comma telegram user ids (optional; else access-code gate)
  FXGLITCH_GROK_BOT=1 to enable
  FXGLITCH_GROK_ALERT_CHAT_ID  optional chat for fill alerts
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import access as access_codes
from . import desk_tools
from . import grok_client

ROOT = Path(__file__).resolve().parents[2]
STATUS = ROOT / "data" / "grok_bot_status.json"
OFFSET = ROOT / "data" / "grok_bot_offset.json"
SESSIONS = ROOT / "data" / "grok_bot_sessions.json"
ALERT_QUEUE = ROOT / "data" / "grok_bot_alerts.json"

SYSTEM = """You are the FX-GLITCH desk operator assistant running on the live VPS.

Standing rules:
- Cosmas Telegram signals are primary. Prefer Cosmas over random discretionary trades.
- Auto-execute uses HIGH RISK / HIGH REWARD daredevil only (15x floor, CROSS, AUTO_RISK≈5, MIN_LIVE_RR=0.5).
- AUTO_SLOTS are 2,3,4,5 (merrijeh, ekeson, kazi, KarlaGod). KarlaGod needs funding if equity is 0.
- Never loosen stop-losses. Trail only tightens.
- Never invent meme coin names. Never print API keys or secrets.
- Dangerous actions (execute_available, trail_stops, clear_daily_halt, run_learn) require confirm=true in the tool call after the user clearly asks.
- Be concise. Lead with the answer. Use tools for live data.
"""


def _enabled() -> bool:
    return (os.environ.get("FXGLITCH_GROK_BOT") or "").strip() in ("1", "true", "yes", "on")


def _token() -> str:
    return (
        os.environ.get("FXGLITCH_GROK_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or ""
    ).strip()


def _allow_ids() -> set[str]:
    raw = (os.environ.get("FXGLITCH_GROK_BOT_ALLOW_IDS") or "").strip()
    if not raw:
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _api(method: str, **params):
    token = _token()
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {"User-Agent": "FX-GLITCH/grok-bot"}
    if params:
        # Prefer POST JSON for sendMessage with long text
        if method.startswith("send"):
            data = json.dumps(params).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _send(chat_id: int | str, text: str) -> None:
    text = (text or "")[:3900]
    if not text:
        return
    try:
        _api("sendMessage", chat_id=chat_id, text=text)
    except Exception:
        pass


def _sessions() -> dict:
    return _read_json(SESSIONS, {})


def _save_sessions(data: dict) -> None:
    _write_json(SESSIONS, data)


def _authorized(user_id: str, chat_id: str) -> tuple[bool, str]:
    allow = _allow_ids()
    if allow and user_id in allow:
        return True, "allowlist"
    sessions = _sessions()
    sess = sessions.get(user_id) or {}
    if sess.get("unlocked"):
        return True, sess.get("name") or "unlocked"
    return False, ""


def _try_unlock(user_id: str, text: str) -> str | None:
    found = access_codes.unlock(text.strip())
    if not found:
        return None
    sessions = _sessions()
    sessions[user_id] = {
        "unlocked": True,
        "name": found.get("name"),
        "account": found.get("account"),
        "at": _now(),
    }
    _save_sessions(sessions)
    return found.get("name") or "account"


def _run_tools(user_text: str) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_text},
    ]
    # Up to 4 tool rounds
    for _ in range(4):
        msg = grok_client.chat(messages, tools=desk_tools.TOOL_SPECS)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return (msg.get("content") or "").strip() or "(no reply)"
        messages.append(msg)
        for tc in tool_calls:
            fn = (tc.get("function") or {})
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                args = {}
            result = desk_tools.call_tool(name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": json.dumps(result, default=str)[:8000],
                }
            )
    # Final pass without tools
    msg = grok_client.chat(messages)
    return (msg.get("content") or "").strip() or "(no reply)"


def queue_alert(text: str) -> None:
    q = _read_json(ALERT_QUEUE, [])
    if not isinstance(q, list):
        q = []
    q.append({"text": text, "at": _now()})
    _write_json(ALERT_QUEUE, q[-50:])


def _flush_alerts() -> None:
    chat = (os.environ.get("FXGLITCH_GROK_ALERT_CHAT_ID") or "").strip()
    if not chat:
        return
    q = _read_json(ALERT_QUEUE, [])
    if not q:
        return
    _write_json(ALERT_QUEUE, [])
    for item in q:
        _send(chat, item.get("text") or "")


class GrokBotBridge:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if os.environ.get("VERCEL"):
            return
        if not _enabled():
            _write_json(STATUS, {"status": "disabled", "at": _now(), "hint": "Set FXGLITCH_GROK_BOT=1"})
            return
        if not _token():
            _write_json(STATUS, {"status": "no_token", "at": _now()})
            return
        if not (os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")):
            _write_json(STATUS, {"status": "no_xai_key", "at": _now(), "hint": "Set XAI_API_KEY"})
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="grok-bot", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        _write_json(STATUS, {"status": "starting", "at": _now()})
        offset = int(_read_json(OFFSET, {}).get("offset") or 0)
        while not self._stop.is_set():
            try:
                _flush_alerts()
                data = _api(
                    "getUpdates",
                    timeout=25,
                    offset=offset,
                    allowed_updates=json.dumps(["message"]),
                )
                if not data.get("ok"):
                    _write_json(STATUS, {"status": "error", "error": str(data)[:200], "at": _now()})
                    time.sleep(5)
                    continue
                for upd in data.get("result") or []:
                    msg = upd.get("message") or {}
                    chat = msg.get("chat") or {}
                    chat_id = chat.get("id")
                    user = msg.get("from") or {}
                    user_id = str(user.get("id") or "")
                    text = (msg.get("text") or "").strip()
                    offset = int(upd.get("update_id", 0)) + 1
                    _write_json(OFFSET, {"offset": offset})
                    if not text or chat_id is None:
                        continue
                    # Private chats only for operator NL
                    if chat.get("type") not in ("private",):
                        continue
                    ok, who = _authorized(user_id, str(chat_id))
                    if not ok:
                        name = _try_unlock(user_id, text)
                        if name:
                            _send(chat_id, f"Unlocked *{name}*. Ask for status, available trades, execute, trail, learn.")
                        else:
                            _send(chat_id, "Send your FX-GLITCH access code to unlock the Grok desk bot.")
                        continue
                    if text.lower() in ("help", "/help", "start", "/start"):
                        _send(
                            chat_id,
                            "FX-GLITCH Grok desk bot.\n"
                            "Try: status · available · execute available · trail NIL by $5 · learn summary · clear halt\n"
                            f"Signed in as {who}.",
                        )
                        continue
                    try:
                        reply = _run_tools(text)
                    except Exception as e:
                        reply = f"Error: {type(e).__name__}: {e}"
                    _send(chat_id, reply)
                _write_json(STATUS, {"status": "watching", "offset": offset, "at": _now()})
            except Exception as exc:
                _write_json(STATUS, {"status": "error", "error": str(exc)[:300], "at": _now()})
                time.sleep(4)


bridge = GrokBotBridge()

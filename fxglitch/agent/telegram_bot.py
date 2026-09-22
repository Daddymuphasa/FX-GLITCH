"""24/7 group watch via a Telegram *bot* (works on a VPS, no QR, laptop can be off).

Set TELEGRAM_BOT_TOKEN. Add the bot to the Cosmas group. Disable privacy
in BotFather (/setprivacy → Disable) so it sees every post, not only commands.
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

from ..signal_copy import parse_signal
from .inbox import ingest, publish_live
from .plans import fetch_mark, live_entry_check, recommend
from .telegram_user import _load_watch, _same_chat, _valid_chat_id

ROOT = Path(__file__).resolve().parents[2]
STATUS = ROOT / "data" / "telegram_bot_status.json"
OFFSET = ROOT / "data" / "telegram_bot_offset.json"


def _token() -> str:
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_status(payload: dict) -> None:
    try:
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def _offset() -> int:
    try:
        if OFFSET.exists():
            return int(json.loads(OFFSET.read_text(encoding="utf-8")).get("offset") or 0)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return 0


def _save_offset(value: int) -> None:
    try:
        OFFSET.write_text(json.dumps({"offset": value}), encoding="utf-8")
    except OSError:
        pass


def _api(method: str, **params):
    token = _token()
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "FX-GLITCH/desk"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ingest_text(text: str, *, telegram_id: str, chat: str, posted_at: str | None) -> None:
    parsed = parse_signal(text)
    if not parsed.symbol or not parsed.direction:
        return
    if parsed.stop_adjustment and not parsed.stop_loss:
        return
    rec = recommend(text, equity=1000.0)
    mark = rec.mark or fetch_mark(rec.binance_symbol or parsed.symbol)
    if mark and rec.entry is None:
        rec = recommend(text, equity=1000.0, mark=mark)
    check = live_entry_check(
        direction=rec.direction, entry=rec.entry, stop=rec.stop,
        take_profits=rec.take_profits, mark=mark,
    )
    ingest(
        text,
        source="telegram-bot",
        telegram_id=telegram_id,
        chat=chat,
        posted_at=posted_at,
        extra={
            "still_good": check["ok"],
            "still_good_reason": check["reason"],
            "mark": check["mark"],
            "live_rr": check["live_rr"],
        },
    )
    publish_live()
    from .auto_trade import maybe_execute
    maybe_execute(text, telegram_id=telegram_id, posted_at=posted_at)


class BotBridge:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if os.environ.get("VERCEL"):
            return
        if not _token():
            _write_status({"status": "no_token", "at": _now(),
                           "hint": "Set TELEGRAM_BOT_TOKEN for 24/7 group watch on the VPS."})
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="tg-bot", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        _write_status({"status": "starting", "at": _now()})
        offset = _offset()
        while not self._stop.is_set():
            try:
                data = _api("getUpdates", timeout=25, offset=offset, allowed_updates=json.dumps(
                    ["message", "channel_post", "edited_message", "edited_channel_post"]
                ))
                if not data.get("ok"):
                    _write_status({"status": "error", "error": str(data)[:200], "at": _now()})
                    time.sleep(5)
                    continue
                watch = _load_watch().get("chat_id")
                for upd in data.get("result") or []:
                    offset = int(upd.get("update_id", 0)) + 1
                    _save_offset(offset)
                    msg = (upd.get("message") or upd.get("channel_post")
                           or upd.get("edited_message") or upd.get("edited_channel_post") or {})
                    chat = msg.get("chat") or {}
                    chat_id = chat.get("id")
                    if watch and _valid_chat_id(watch) and not _same_chat(chat_id, watch):
                        continue
                    text = (msg.get("text") or msg.get("caption") or "").strip()
                    if not text:
                        continue
                    title = chat.get("title") or chat.get("username") or str(chat_id)
                    posted = None
                    if msg.get("date"):
                        posted = datetime.fromtimestamp(int(msg["date"]), tz=timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        )
                    _ingest_text(
                        text,
                        telegram_id=f"{chat_id}:{msg.get('message_id')}",
                        chat=title,
                        posted_at=posted,
                    )
                _write_status({
                    "status": "watching",
                    "watch": _load_watch(),
                    "offset": offset,
                    "at": _now(),
                })
            except Exception as exc:
                _write_status({"status": "error", "error": str(exc)[:300], "at": _now()})
                time.sleep(4)


bridge = BotBridge()

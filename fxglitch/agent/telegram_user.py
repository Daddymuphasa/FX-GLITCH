"""Link a real Telegram user account via QR and watch a group it already joined.

This is the official Telethon QR login (same idea as Telegram Desktop
"Link Desktop Device"). It does not scrape a group you are not in. The
account you scan with must already be a member of the Bitunix group.

Vercel cannot hold this session. Run `python serve.py` on the machine
that should keep listening.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..signal_copy import parse_signal
from .inbox import ingest
from .plans import fetch_mark, live_entry_check, recommend

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "data" / "telegram"
WATCH_PATH = ROOT / "data" / "telegram_watch.json"


def _creds():
    raw_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    raw_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not raw_id or not raw_hash:
        return None
    try:
        return int(raw_id), raw_hash
    except ValueError:
        return None


def _qr_image(url: str) -> str:
    import qrcode
    from qrcode.image.svg import SvgPathImage
    img = qrcode.make(url, image_factory=SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


def _same_chat(left, right) -> bool:
    a, b = str(left or "").strip(), str(right or "").strip()
    if a and a == b:
        return True
    da = "".join(c for c in a if c.isdigit())
    db = "".join(c for c in b if c.isdigit())
    return bool(da and da == db)


def _message_text(msg) -> str:
    if msg is None:
        return ""
    text = getattr(msg, "raw_text", None) or getattr(msg, "message", None) or ""
    if not isinstance(text, str):
        text = str(text or "")
    if text.strip():
        return text.strip()
    cap = getattr(msg, "caption", None) or ""
    return str(cap).strip()


def _valid_chat_id(value) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        int(text)
    except (TypeError, ValueError):
        return False
    return True


def _load_watch() -> dict:
    try:
        if WATCH_PATH.exists():
            data = json.loads(WATCH_PATH.read_text(encoding="utf-8"))
            if _valid_chat_id(data.get("chat_id")):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_watch(data: dict) -> None:
    WATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCH_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


class TelegramBridge:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client = None
        self._qr = None
        self._qr_url = ""
        self._qr_png = ""
        self._status = "idle"
        self._error = ""
        self._me: dict | None = None
        self._need_password = False
        self._lock = threading.Lock()
        self._wait_task: asyncio.Task | None = None
        self._qr_started = 0.0

    def start(self) -> None:
        if os.environ.get("VERCEL"):
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, name="tg-bridge", daemon=True)
        self._thread.start()
        session_file = Path(str(SESSION) + ".session")
        if session_file.exists():
            try:
                self._call(self._boot(), timeout=45)
            except Exception as exc:
                self._error = str(exc)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coro, timeout: float = 30):
        if self._loop is None:
            self.start()
            for _ in range(50):
                if self._loop is not None:
                    break
                threading.Event().wait(0.05)
        if self._loop is None:
            raise RuntimeError("telegram loop did not start")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    async def _boot(self) -> None:
        creds = _creds()
        if creds is None:
            self._status = "need_api"
            return
        try:
            from telethon import TelegramClient, events
        except ImportError:
            self._status = "need_library"
            return
        api_id, api_hash = creds
        SESSION.parent.mkdir(parents=True, exist_ok=True)
        self._client = TelegramClient(str(SESSION), api_id, api_hash)
        await self._client.connect()
        if await self._client.is_user_authorized():
            await self._mark_linked()
            await self._listen(events)
            return
        self._status = "need_scan"
        await self._new_qr()

    async def _new_qr(self) -> None:
        if self._client is None:
            return
        if self._wait_task is not None:
            self._wait_task.cancel()
            self._wait_task = None
        self._qr = await self._client.qr_login()
        await self._publish_qr()
        self._status = "need_scan"
        self._error = ""
        self._wait_task = asyncio.create_task(self._wait_qr())

    async def _publish_qr(self) -> None:
        import time
        self._qr_url = self._qr.url if self._qr is not None else ""
        self._qr_started = time.time()
        try:
            self._qr_png = _qr_image(self._qr_url) if self._qr_url else ""
        except Exception:
            self._qr_png = ""

    async def _refresh_token(self) -> None:
        try:
            if self._qr is not None:
                self._qr = await self._qr.recreate()
            else:
                self._qr = await self._client.qr_login()
        except Exception:
            self._qr = await self._client.qr_login()
        await self._publish_qr()
        self._error = ""

    async def _wait_qr(self) -> None:
        from telethon.errors import SessionPasswordNeededError
        while self._status == "need_scan" and self._qr is not None:
            try:
                # Telegram tokens die in ~30s. Recreate before that.
                await asyncio.wait_for(self._qr.wait(), timeout=20)
            except SessionPasswordNeededError:
                self._need_password = True
                self._status = "need_password"
                return
            except asyncio.CancelledError:
                return
            except asyncio.TimeoutError:
                await self._refresh_token()
                continue
            except Exception as exc:
                self._error = str(exc)
                await self._refresh_token()
                await asyncio.sleep(1)
                continue
            from telethon import events
            await self._mark_linked()
            await self._listen(events)
            return

    async def _mark_linked(self) -> None:
        me = await self._client.get_me()
        self._me = {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "phone": None,
        }
        self._status = "linked"
        self._need_password = False
        self._qr = None

    async def _listen(self, events) -> None:
        watch = _load_watch().get("chat_id")

        @self._client.on(events.NewMessage())
        async def _on_message(event):
            wanted = _load_watch().get("chat_id")
            if wanted and not _same_chat(event.chat_id, wanted):
                return
            text = _message_text(getattr(event, "message", None) or event)
            if not text:
                return
            parsed = parse_signal(text)
            if not parsed.symbol or not parsed.direction:
                return
            title = ""
            try:
                chat = await event.get_chat()
                title = getattr(chat, "title", None) or getattr(chat, "username", "") or ""
            except Exception:
                title = str(event.chat_id)
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
                source="telegram",
                telegram_id=f"{event.chat_id}:{event.id}",
                chat=title,
                extra={
                    "still_good": check["ok"],
                    "still_good_reason": check["reason"],
                    "mark": check["mark"],
                    "live_rr": check["live_rr"],
                },
            )
            from .inbox import publish_live
            await asyncio.to_thread(publish_live)

        if _valid_chat_id(watch):
            self._status = "watching"
            asyncio.create_task(self._scan_today())
            asyncio.create_task(self._keep_scanning())

    def _today_start(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(hours=72)

    async def _keep_scanning(self) -> None:
        while True:
            await asyncio.sleep(120)
            try:
                await self._scan_today()
                from .inbox import publish_live
                publish_live()
            except Exception:
                pass

    async def _scan_today(self) -> dict:
        watch = _load_watch()
        chat_id = watch.get("chat_id")
        if not _valid_chat_id(chat_id) or self._client is None:
            return {"ok": False, "error": "not watching a group yet", "scanned": 0, "kept": 0}
        since = self._today_start()
        entity = int(str(chat_id).strip())
        scanned = 0
        kept = 0
        skipped = 0
        seen = 0
        samples: list[str] = []
        try:
            async for msg in self._client.iter_messages(entity, limit=500):
                msg_time = msg.date
                if msg_time.tzinfo is None:
                    msg_time = msg_time.replace(tzinfo=timezone.utc)
                else:
                    msg_time = msg_time.astimezone(timezone.utc)
                if msg_time < since:
                    break
                seen += 1
                text = _message_text(msg)
                if text and len(samples) < 8:
                    samples.append(text[:280])
                if not text:
                    skipped += 1
                    continue
                parsed = parse_signal(text)
                if not parsed.symbol or not parsed.direction or parsed.stop_adjustment:
                    skipped += 1
                    continue
                scanned += 1
                posted = msg_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                rec = recommend(text, equity=1000.0)
                mark = rec.mark or fetch_mark(rec.binance_symbol or parsed.symbol)
                if mark and rec.entry is None:
                    rec = recommend(text, equity=1000.0, mark=mark)
                check = live_entry_check(
                    direction=rec.direction,
                    entry=rec.entry,
                    stop=rec.stop,
                    take_profits=rec.take_profits,
                    mark=mark,
                )
                title = watch.get("title") or ""
                extra = {
                    "still_good": check["ok"],
                    "still_good_reason": check["reason"],
                    "mark": check["mark"],
                    "live_rr": check["live_rr"],
                    "posted_at": posted,
                }
                ingest(
                    text,
                    source="telegram",
                    telegram_id=f"{chat_id}:{msg.id}",
                    chat=title,
                    posted_at=posted,
                    extra=extra,
                )
                if check["ok"]:
                    kept += 1
        except Exception as exc:
            return {"ok": False, "error": str(exc), "scanned": scanned, "kept": kept,
                    "skipped": skipped, "seen": seen, "samples": samples}
        return {"ok": True, "error": "", "scanned": scanned, "kept": kept, "skipped": skipped,
                "seen": seen, "samples": samples,
                "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "chat": watch}

    async def _list_chats(self) -> list[dict]:
        out = []
        async for dialog in self._client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                out.append({
                    "id": str(dialog.id),
                    "title": dialog.name,
                    "unread": dialog.unread_count,
                })
        return out

    async def _submit_password(self, password: str) -> None:
        await self._client.sign_in(password=password)
        from telethon import events
        await self._mark_linked()
        await self._listen(events)

    def snapshot(self) -> dict:
        watch = _load_watch()
        return {
            "status": self._status,
            "error": self._error,
            "qr": self._qr_png,
            "qr_url": self._qr_url,
            "user": self._me,
            "watch": watch,
            "need_api": self._status == "need_api",
            "need_library": self._status == "need_library",
            "vercel": bool(os.environ.get("VERCEL")),
            "qr_age": int(__import__("time").time() - self._qr_started) if self._qr_started else None,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def ensure_qr(self) -> dict:
        if os.environ.get("VERCEL"):
            snap = self.snapshot()
            snap["status"] = "vercel"
            snap["error"] = "QR login needs python serve.py on your PC. Vercel cannot keep a Telegram session."
            return snap
        self.start()
        try:
            if self._status in ("linked", "watching"):
                return self.snapshot()
            if self._client is None:
                self._call(self._boot(), timeout=45)
            else:
                # Always mint a fresh token. Telegram expires the old one in ~30s.
                self._call(self._new_qr(), timeout=30)
        except Exception as exc:
            self._error = str(exc)
        return self.snapshot()

    def scan_today(self) -> dict:
        if self._status not in ("linked", "watching"):
            return {"ok": False, "error": "link a Telegram account first", "scanned": 0, "kept": 0}
        try:
            result = self._call(self._scan_today(), timeout=90)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "scanned": 0, "kept": 0}
        return result

    def chats(self) -> dict:
        if self._status not in ("linked", "watching"):
            return {"chats": [], **self.snapshot()}
        try:
            chats = self._call(self._list_chats(), timeout=45)
        except Exception as exc:
            return {"chats": [], "error": str(exc), **self.snapshot()}
        snap = self.snapshot()
        snap["chats"] = chats
        return snap

    def set_watch(self, chat_id: str, title: str = "") -> dict:
        if not _valid_chat_id(chat_id):
            snap = self.snapshot()
            snap["error"] = "Load my groups and pick the Bitunix chat first."
            return snap
        _save_watch({"chat_id": str(chat_id).strip(), "title": title or ""})
        if self._status in ("linked", "watching"):
            self._status = "watching"
        return self.snapshot()

    def set_password(self, password: str) -> dict:
        self._call(self._submit_password(password))
        return self.snapshot()


def _forward(url: str, text: str) -> None:
    import urllib.request
    req = urllib.request.Request(
        url,
        data=json.dumps({"message": text}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "FX-GLITCH/telegram"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:
        pass


bridge = TelegramBridge()

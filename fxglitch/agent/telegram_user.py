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
from datetime import datetime, timezone
from pathlib import Path

from .inbox import ingest

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


def _load_watch() -> dict:
    try:
        if WATCH_PATH.exists():
            return json.loads(WATCH_PATH.read_text(encoding="utf-8"))
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

    def start(self) -> None:
        if os.environ.get("VERCEL"):
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, name="tg-bridge", daemon=True)
        self._thread.start()

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
        self._qr = await self._client.qr_login()
        self._qr_url = self._qr.url
        try:
            self._qr_png = _qr_image(self._qr.url)
        except Exception:
            self._qr_png = ""
        self._status = "need_scan"
        self._error = ""
        asyncio.create_task(self._wait_qr())

    async def _wait_qr(self) -> None:
        from telethon.errors import SessionPasswordNeededError
        try:
            await self._qr.wait(timeout=90)
        except SessionPasswordNeededError:
            self._need_password = True
            self._status = "need_password"
            return
        except Exception as exc:
            self._status = "need_scan"
            self._error = str(exc)
            try:
                if self._qr is not None:
                    self._qr = await self._qr.recreate()
                    self._qr_url = self._qr.url
                    self._qr_png = _qr_image(self._qr.url)
                    asyncio.create_task(self._wait_qr())
            except Exception:
                pass
            return
        from telethon import events
        await self._mark_linked()
        await self._listen(events)

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
            if wanted and str(event.chat_id) != str(wanted):
                return
            text = event.raw_text or ""
            if not text.strip():
                return
            title = ""
            try:
                chat = await event.get_chat()
                title = getattr(chat, "title", None) or getattr(chat, "username", "") or ""
            except Exception:
                title = str(event.chat_id)
            item = ingest(
                text,
                source="telegram",
                telegram_id=f"{event.chat_id}:{event.id}",
                chat=title,
            )
            hook = os.environ.get("FXGLITCH_INBOX_WEBHOOK", "").strip()
            if hook:
                await asyncio.to_thread(_forward, hook, text)

        if watch:
            self._status = "watching"

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
            if self._client is None:
                self._call(self._boot(), timeout=45)
            elif self._status == "need_scan" and not self._qr_png:
                self._call(self._new_qr(), timeout=30)
        except Exception as exc:
            self._error = str(exc)
        return self.snapshot()

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
        _save_watch({"chat_id": str(chat_id), "title": title})
        if self._status == "linked":
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

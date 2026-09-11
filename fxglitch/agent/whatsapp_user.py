"""Link WhatsApp as a companion device (QR) and run the desk agent.

Scan: WhatsApp → Settings → Linked devices → Link a device.
Vercel cannot hold this session. Run python serve.py on the desk.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import threading
from pathlib import Path

from .wa_dialog import HELP, format_signals, handle

ROOT = Path(__file__).resolve().parents[2]
AUTH = ROOT / "data" / "whatsapp_auth.json"
KEYS = ROOT / "data" / "whatsapp_auth.json.keys"
DB = ROOT / "data" / "whatsapp.db"
USERS = ROOT / "data" / "whatsapp_users.json"


def _qr_image(payload: str) -> str:
    import qrcode
    from qrcode.image.svg import SvgPathImage
    img = qrcode.make(payload, image_factory=SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


class WhatsAppBridge:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client = None
        self._status = "idle"
        self._error = ""
        self._qr = ""
        self._me = ""
        self._users: dict[str, dict] = {}
        self._load_users()

    def _load_users(self) -> None:
        try:
            if USERS.exists():
                data = json.loads(USERS.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._users = data
        except (OSError, json.JSONDecodeError):
            self._users = {}

    def _save_users(self) -> None:
        USERS.parent.mkdir(parents=True, exist_ok=True)
        USERS.write_text(json.dumps(self._users, indent=2), encoding="utf-8")

    def start(self) -> None:
        if os.environ.get("VERCEL"):
            self._status = "vercel"
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, name="wa-bridge", daemon=True)
        self._thread.start()
        try:
            self._call(self._boot(), timeout=20)
        except Exception as exc:
            self._error = str(exc)[:300]
            if self._status == "idle":
                self._status = "need_library"

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
            raise RuntimeError("whatsapp loop did not start")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    async def _boot(self) -> None:
        try:
            from piwapp import AuthenticationCreds, Client, ConnectionConfig
            from piwapp.events import WAEventType
        except ImportError:
            self._status = "need_library"
            self._error = "pip install piwapp"
            return
        AUTH.parent.mkdir(parents=True, exist_ok=True)
        if AUTH.exists():
            creds = AuthenticationCreds.from_json(AUTH.read_text(encoding="utf-8"))
        else:
            creds = AuthenticationCreds.initial()
            AUTH.write_text(creds.to_json(), encoding="utf-8")

        def save_creds(c):
            AUTH.write_text(c.to_json(), encoding="utf-8")

        client = Client(
            creds,
            ConnectionConfig(),
            on_creds_update=save_creds,
            keys_path=str(KEYS),
            db_path=str(DB),
        )
        client.on("connection.update", self._on_connection)

        def on_messages(payload):
            messages = getattr(payload, "messages", None) or payload.get("messages") if isinstance(payload, dict) else []
            for msg in messages or []:
                asyncio.create_task(self._on_message(msg))

        client.events.on(WAEventType.MESSAGES_UPSERT, on_messages)
        self._client = client
        self._status = "connecting"
        await client.start()

    def _on_connection(self, update: dict) -> None:
        qr = update.get("qr")
        if qr:
            self._qr = _qr_image(qr)
            self._status = "need_scan"
            self._error = ""
        if update.get("connection") == "open":
            me = (update.get("me") or {}).get("id") or ""
            self._me = str(me)
            self._status = "linked"
            self._qr = ""
            self._error = ""

    async def _on_message(self, msg: dict) -> None:
        key = msg.get("key") or {}
        if key.get("fromMe"):
            return
        jid = str(key.get("remoteJid") or "")
        if not jid or jid.endswith("@g.us") or jid.endswith("@broadcast"):
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        session = self._users.setdefault(jid, {})
        try:
            reply = handle(session, text)
        except Exception as exc:
            reply = f"Desk error: {exc}"[:400]
        self._save_users()
        await self._send(jid, reply)

    async def _send(self, jid: str, text: str) -> None:
        if not self._client or not text:
            return
        await self._client.send_text(jid, text)

    def ensure_qr(self) -> dict:
        if self._status in ("idle", "need_library"):
            try:
                self.start()
            except Exception as exc:
                self._error = str(exc)[:300]
        return self.snapshot()

    def snapshot(self) -> dict:
        return {
            "status": self._status,
            "error": self._error,
            "qr": self._qr,
            "me": self._me,
            "users": sum(1 for u in self._users.values() if u.get("account")),
        }

    def notify_signals(self, rows: list) -> None:
        if self._status != "linked" or not self._client or not self._loop:
            return
        text = "New setups.\n" + format_signals(rows)
        for jid, session in list(self._users.items()):
            if not session.get("account"):
                continue
            asyncio.run_coroutine_threadsafe(self._send(jid, text), self._loop)


bridge = WhatsAppBridge()

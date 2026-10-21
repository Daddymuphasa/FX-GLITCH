"""Generate a Telegram Link-Desktop QR on this PC, then we copy the session to the VPS."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

SESSION = ROOT / "data" / "telegram"
OUT = ROOT / "data" / "tg-link"
OUT.mkdir(parents=True, exist_ok=True)
QR_PNG = OUT / "qr.png"
STATUS = OUT / "status.txt"


def _creds():
    return int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"]


async def main() -> None:
    api_id, api_hash = _creds()
    client = __import__('fxglitch.tgnet',fromlist=['x']).make_client(str(SESSION), api_id, api_hash)
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        STATUS.write_text(f"LINKED @{me.username or me.first_name}\n", encoding="utf-8")
        print("ALREADY_LINKED", me.username or me.first_name)
        await client.disconnect()
        return
    import qrcode

    async def save_qr(token) -> None:
        if token is None or not getattr(token, "url", None):
            return
        qrcode.make(token.url).save(QR_PNG)
        (OUT / "index.html").write_text(
            "<!doctype html><meta charset=utf-8><title>Telegram QR</title>"
            "<body style='background:#111;color:#eee;text-align:center;font-family:sans-serif'>"
            "<p>Telegram → Settings → Devices → Link desktop device</p>"
            "<p><img id=q src='qr.png' width=360 style='background:#fff;padding:12px;border-radius:12px'></p>"
            "<script>setInterval(function(){document.getElementById('q').src='qr.png?t='+Date.now()},2000)</script>",
            encoding="utf-8",
        )
        print("QR_WRITTEN", QR_PNG)
        STATUS.write_text("NEED_SCAN\n", encoding="utf-8")

    qr = await client.qr_login()
    await save_qr(qr)
    try:
        while True:
            if qr is None:
                qr = await client.qr_login()
                await save_qr(qr)
            try:
                await qr.wait(timeout=15)
                break
            except asyncio.TimeoutError:
                try:
                    qr = await qr.recreate()
                except Exception:
                    qr = await client.qr_login()
                await save_qr(qr)
    except SessionPasswordNeededError:
        STATUS.write_text("NEED_PASSWORD\n", encoding="utf-8")
        print("NEED_PASSWORD")
        await client.disconnect()
        return
    me = await client.get_me()
    STATUS.write_text(f"LINKED @{me.username or me.first_name}\n", encoding="utf-8")
    print("LINKED", me.username or me.first_name)
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

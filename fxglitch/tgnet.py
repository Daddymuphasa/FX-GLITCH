"""Telegram client factory for the box: forces MTProto port 5222 (box blocks 443/80).

TELEGRAM_PORT (default 5222) and optional TELEGRAM_PROXY (socks5://host:port).
"""
from __future__ import annotations

import os
from urllib.parse import urlparse


def _proxy():
    raw = (os.environ.get("TELEGRAM_PROXY") or "").strip()
    if not raw:
        return None
    u = urlparse(raw)
    kind = (u.scheme or "socks5").lower()
    return (kind, u.hostname, u.port or 1080, True, u.username, u.password)


def make_client(session, api_id, api_hash, **kw):
    from telethon import TelegramClient

    port = int(os.environ.get("TELEGRAM_PORT") or "5222")
    proxy = _proxy()
    if proxy:
        kw.setdefault("proxy", proxy)
    client = TelegramClient(str(session), int(api_id), api_hash, **kw)
    sess = client.session
    try:
        dc = sess.dc_id or 2
        addr = sess.server_address or "149.154.167.51"
        if sess.port != port:
            sess.set_dc(dc, addr, port)
    except Exception:
        pass
    return client

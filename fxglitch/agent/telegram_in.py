"""Ingest Telegram Bot API updates. The bot must be in the group.

Telegram will not let a bot read a private group's history unless:
- the bot is a member, and
- privacy mode is off (/setprivacy in BotFather) or it is admin on a channel.

We never scrape a group we are not invited to. Set TELEGRAM_BOT_TOKEN and
optionally TELEGRAM_CHAT_ID (the Bitunix futures group id).
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .inbox import ingest

API = "https://api.telegram.org/bot{token}/{method}"


def extract_text(update: dict) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (text, telegram_id, chat_id, chat_title) from one Update object."""
    msg = update.get("message") or update.get("channel_post") or update.get("edited_message")
    if not isinstance(msg, dict):
        return None, None, None, None
    text = msg.get("text") or msg.get("caption")
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", "")) if chat.get("id") is not None else None
    title = chat.get("title") or chat.get("username")
    mid = msg.get("message_id")
    telegram_id = f"{chat_id}:{mid}" if chat_id and mid is not None else None
    return (str(text) if text else None), telegram_id, chat_id, title


def allowed_chat(chat_id: str | None) -> bool:
    wanted = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not wanted:
        return True
    return str(chat_id) == wanted


def ingest_update(update: dict) -> dict | None:
    text, telegram_id, chat_id, title = extract_text(update)
    if not text:
        return None
    if not allowed_chat(chat_id):
        return None
    return ingest(text, source="telegram", telegram_id=telegram_id, chat=title or chat_id)


def bot_get(token: str, method: str, query: dict | None = None) -> dict:
    encoded = urllib.parse.urlencode(query or {})
    url = API.format(token=token, method=method)
    if encoded:
        url = f"{url}?{encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": "FX-GLITCH/telegram"})
    with urllib.request.urlopen(req, timeout=35) as response:
        return json.loads(response.read().decode())

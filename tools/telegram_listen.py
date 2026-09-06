"""Long-poll a Telegram bot and drop Bitunix-group signals into the inbox.

    1. Talk to @BotFather, create a bot, copy the token.
    2. Disable privacy: /setprivacy → Disable (or add the bot as channel admin).
    3. Add the bot to the Bitunix futures group.
    4. Send a message in the group, then run this once to print chat ids:
         python tools/telegram_listen.py --discover
    5. Set TELEGRAM_CHAT_ID to that group id and run:
         python tools/telegram_listen.py

This process must stay running. Vercel cannot long-poll; it receives webhooks
at POST /api/telegram instead.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fxglitch.agent.telegram_in import bot_get, extract_text, ingest_update, allowed_chat  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Listen to a Telegram bot for futures signals")
    p.add_argument("--discover", action="store_true", help="print chat ids from recent updates and exit")
    p.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    args = p.parse_args()
    if not args.token:
        sys.exit("Set TELEGRAM_BOT_TOKEN (or pass --token). Create a bot with @BotFather.")

    offset = 0
    print("listening for Telegram updates. Ctrl+C to stop.")
    if os.environ.get("TELEGRAM_CHAT_ID"):
        print(f"filtering chat id {os.environ['TELEGRAM_CHAT_ID']}")
    try:
        while True:
            data = bot_get(args.token, "getUpdates", {"timeout": 25, "offset": offset})
            if not data.get("ok"):
                print("telegram error:", data)
                time.sleep(3)
                continue
            for update in data.get("result") or []:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                text, telegram_id, chat_id, title = extract_text(update)
                if args.discover:
                    print(f"chat_id={chat_id} title={title!r} id={telegram_id} text={(text or '')[:80]!r}")
                    continue
                if not text or not allowed_chat(chat_id):
                    continue
                item = ingest_update(update)
                if item:
                    print(f"inbox {item['direction'] or '?'} {item['binance_symbol'] or item['bitunix_symbol']}")
            if args.discover:
                return
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

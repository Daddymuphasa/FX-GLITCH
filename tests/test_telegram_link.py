"""Watch-config persistence for the Telegram QR linker."""

import os
import sys
import tempfile
import unittest
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fxglitch.agent.telegram_user as tg


class TestWatchConfig(unittest.TestCase):
    def setUp(self):
        self._old = tg.WATCH_PATH
        fd, name = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        tg.WATCH_PATH = Path(name)

    def tearDown(self):
        try:
            tg.WATCH_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        tg.WATCH_PATH = self._old

    def test_set_watch_roundtrip(self):
        snap = tg.bridge.set_watch("-100555", "Bitunix futures")
        stored = tg._load_watch()
        self.assertEqual(stored["chat_id"], "-100555")
        self.assertEqual(stored["title"], "Bitunix futures")
        self.assertEqual(snap["watch"]["chat_id"], "-100555")

    def test_snapshot_does_not_require_telethon(self):
        snap = tg.bridge.snapshot()
        self.assertIn("status", snap)

    def test_set_watch_rejects_placeholder(self):
        tg.bridge.set_watch("-100555", "Bitunix futures")
        snap = tg.bridge.set_watch("No groups yet — scan the QR first", "No groups yet")
        stored = tg._load_watch()
        self.assertEqual(stored["chat_id"], "-100555")
        self.assertIn("pick", (snap.get("error") or "").lower())

    def test_load_watch_ignores_garbage(self):
        tg.WATCH_PATH.write_text(
            '{"chat_id": "No groups yet — scan the QR first", "title": "x"}',
            encoding="utf-8",
        )
        self.assertEqual(tg._load_watch(), {})

    def test_historical_scan_only_checks_fresh_posts_live(self):
        now = datetime.now(timezone.utc)
        message = "BTCUSDT Long Entry CMP TP 120 SL 90"
        posts = [SimpleNamespace(id=2, date=now - timedelta(minutes=5), raw_text=message),
                 SimpleNamespace(id=1, date=now - timedelta(days=2), raw_text=message)]

        class Client:
            async def iter_messages(self, entity, limit):
                for post in posts:
                    yield post

        bridge = tg.TelegramBridge()
        bridge._client = Client()
        rec = SimpleNamespace(mark=None, binance_symbol="BTCUSDT", entry=None,
                              direction="LONG", stop=90, take_profits=[120])
        with patch.object(tg, "_load_watch", return_value={"chat_id": "-1001"}), \
             patch.object(tg, "recommend", return_value=rec), \
             patch.object(tg, "fetch_mark", return_value=100) as mark, \
             patch.object(tg, "ingest"), \
             patch.object(bridge, "_write_status"), \
             patch("fxglitch.agent.auto_trade.maybe_execute") as execute:
            result = asyncio.run(bridge._scan_today())
        self.assertTrue(result["ok"])
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(mark.call_count, 1)
        self.assertEqual(execute.call_count, 1)


if __name__ == "__main__":
    unittest.main()

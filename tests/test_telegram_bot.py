"""Telegram updates are acknowledged only after processing succeeds."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fxglitch.agent import telegram_bot


class TestTelegramOffset(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_offset, self.old_status = telegram_bot.OFFSET, telegram_bot.STATUS
        telegram_bot.OFFSET = Path(self.tmp.name) / "offset.json"
        telegram_bot.STATUS = Path(self.tmp.name) / "status.json"
        self.bot = telegram_bot.BotBridge()

    def tearDown(self):
        telegram_bot.OFFSET, telegram_bot.STATUS = self.old_offset, self.old_status
        self.tmp.cleanup()

    def _run_one_poll(self, handler):
        update = {"update_id": 41, "message": {
            "chat": {"id": -1001}, "message_id": 7,
            "text": "BTCUSDT Long Entry CMP TP 3 SL 1", "date": 1700000000,
        }}
        calls = 0

        def api(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"ok": True, "result": [update]}
            self.bot._stop.set()
            return {"ok": True, "result": []}

        with patch.object(telegram_bot, "_api", side_effect=api), \
             patch.object(telegram_bot, "_load_watch", return_value={}), \
             patch.object(telegram_bot, "_ingest_text", side_effect=handler), \
             patch.object(telegram_bot.time, "sleep", side_effect=lambda _: self.bot._stop.set()):
            self.bot._loop()

    def test_failed_handler_keeps_update_for_replay(self):
        self._run_one_poll(RuntimeError("temporary failure"))
        self.assertEqual(telegram_bot._offset(), 0)

    def test_processed_update_advances_offset(self):
        self._run_one_poll(None)
        self.assertEqual(telegram_bot._offset(), 42)


if __name__ == "__main__":
    unittest.main()

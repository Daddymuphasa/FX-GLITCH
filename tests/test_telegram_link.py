"""Watch-config persistence for the Telegram QR linker."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

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
        snap = bridge.snapshot()
        self.assertIn("status", snap)


if __name__ == "__main__":
    unittest.main()

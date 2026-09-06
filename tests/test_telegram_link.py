"""Watch-config persistence for the Telegram QR linker."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.agent.telegram_user import _load_watch, bridge


class TestWatchConfig(unittest.TestCase):
    def test_set_watch_roundtrip(self):
        snap = bridge.set_watch("-100555", "Bitunix futures")
        stored = _load_watch()
        self.assertEqual(stored["chat_id"], "-100555")
        self.assertEqual(stored["title"], "Bitunix futures")
        self.assertEqual(snap["watch"]["chat_id"], "-100555")

    def test_snapshot_does_not_require_telethon(self):
        snap = bridge.snapshot()
        self.assertIn("status", snap)


if __name__ == "__main__":
    unittest.main()

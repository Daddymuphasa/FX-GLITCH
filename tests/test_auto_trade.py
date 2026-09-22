import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["FXGLITCH_AUTO_TRADE"] = "1"
os.environ["FXGLITCH_AUTO_SLOTS"] = "2"

from fxglitch.agent import auto_trade


class TestAutoTrade(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        auto_trade.FILLS = self.tmp / "fills.json"
        auto_trade.LOG = self.tmp / "log.txt"

    def test_skips_old_posts(self):
        out = auto_trade.maybe_execute(
            "BTCUSDT\nShort\nEntry CMP\nTp 1\nSl 2\nLeverage 5x",
            telegram_id="old-1",
            posted_at="2020-01-01T00:00:00Z",
        )
        self.assertIsNone(out)

    def test_skips_second_time(self):
        auto_trade._save_fills({"abc": {"ok": True}})
        out = auto_trade.maybe_execute("x", telegram_id="abc", posted_at=None)
        self.assertEqual(out["ok"], True)


if __name__ == "__main__":
    unittest.main()

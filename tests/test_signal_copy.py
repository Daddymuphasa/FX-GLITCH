"""Tests for the source-agnostic external signal parser."""

import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.signal_copy import SignalDeduplicator, SignalDirection, parse_signal


class TestSignalParser(unittest.TestCase):
    def test_market_long_signal(self):
        signal = parse_signal("Kaito/usdt\nLong position\nEntry CMP\nTp 1.5\nSl 0.2500\nLeverage 10x")
        self.assertEqual(signal.symbol, "KAITOUSDT")
        self.assertEqual(signal.direction, SignalDirection.LONG)
        self.assertTrue(signal.entry_is_market)
        self.assertIsNone(signal.entry)
        self.assertEqual(signal.take_profit, Decimal("1.5"))
        self.assertEqual(signal.stop_loss, Decimal("0.2500"))
        self.assertEqual(signal.leverage, Decimal("10"))
        self.assertTrue(signal.can_open)

    def test_long_signal_with_absolute_entry_and_prices(self):
        signal = parse_signal("Pons /usdt has a whale's\nAm targeting long\nTp 1.5\nSl 0.4400\nLeverage 10x")
        self.assertEqual(signal.symbol, "PONSUSDT")
        self.assertEqual(signal.direction, SignalDirection.LONG)
        self.assertFalse(signal.entry_is_market)
        self.assertFalse(signal.can_open)
        self.assertIn("entry is missing", signal.warnings[0])

    def test_stop_adjustment_is_not_a_new_entry(self):
        signal = parse_signal("Adjust your sl to $1 on pieverse\nTp 0.3\nSl 0.93\nPieverse usdt")
        self.assertEqual(signal.symbol, "PIEVERSEUSDT")
        self.assertTrue(signal.stop_adjustment)
        self.assertFalse(signal.can_open)
        self.assertIn("direction is missing", signal.warnings)

    def test_duplicate_message_is_ignored(self):
        dedupe = SignalDeduplicator()
        self.assertTrue(dedupe.first_time("telegram:123"))
        self.assertFalse(dedupe.first_time("telegram:123"))


if __name__ == "__main__":
    unittest.main()

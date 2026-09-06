"""Telegram signal → Binance pair → four risk plans."""

import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.agent.pairs import map_to_binance, normalize_usdt
from fxglitch.agent.plans import recommend
from fxglitch.agent.telegram_in import extract_text
from fxglitch.signal_copy import parse_signal
from fxglitch.venues.base import Instrument


BTC = """BTCUSDT
Long position
Entry 100000
Tp 106000
Sl 98000
Leverage 10x"""


class TestPairs(unittest.TestCase):
    def test_bitunix_slash_normalizes(self):
        self.assertEqual(normalize_usdt("Kaito/usdt"), "KAITOUSDT")

    def test_unlisted_pair_is_not_invented(self):
        mapped = map_to_binance("FOOBARUSDT", {})
        self.assertFalse(mapped.listed)
        self.assertIn("not a Binance", mapped.note)

    def test_listed_perp(self):
        inst = {"BTCUSDT": Instrument("BTCUSDT", "BTC", "USDT", 3, 2, Decimal("0.001"),
                                      tradeable=True, max_leverage=125)}
        mapped = map_to_binance("btc/usdt", inst)
        self.assertTrue(mapped.tradeable)
        self.assertEqual(mapped.binance, "BTCUSDT")


class TestPlans(unittest.TestCase):
    def test_four_tiers_same_stop_different_size(self):
        rec = recommend(BTC, equity=1000, mark=100000)
        ids = [p["id"] for p in rec.plans]
        self.assertEqual(ids, ["daredevil", "high", "mid", "low"])
        stops = {p["stop"] for p in rec.plans}
        self.assertEqual(stops, {98000.0})
        dare, low = rec.plans[0], rec.plans[3]
        self.assertGreater(dare["leverage"], low["leverage"])
        self.assertGreater(dare["loss_if_sl"], low["loss_if_sl"])
        self.assertAlmostEqual(dare["reward_risk"], 3.0, places=4)
        self.assertTrue(low["policy_ok"])

    def test_win_is_r_multiple_not_a_winrate(self):
        rec = recommend(BTC, equity=1000)
        mid = rec.plans[2]
        self.assertAlmostEqual(mid["loss_if_sl"], 10.0, places=4)  # 1% of 1000
        self.assertAlmostEqual(mid["win_if_tp"], 30.0, places=4)   # 3R

    def test_missing_stop_has_no_plans(self):
        rec = recommend("BTCUSDT long CMP leverage 10x")
        self.assertFalse(rec.plans)

    def test_cmp_without_mark_still_builds_plans(self):
        rec = recommend(
            "PIPPIN/USDT\nLong position\nEntry CMP\nTp 0.8\nSl 0.01300\nLeverage 7x",
            equity=1000,
        )
        self.assertEqual(rec.direction, "LONG")
        self.assertTrue(rec.plans)
        self.assertTrue(any(p["policy_ok"] for p in rec.plans))


class TestTelegramExtract(unittest.TestCase):
    def test_group_message(self):
        text, tid, chat, title = extract_text({
            "message": {"message_id": 9, "text": BTC,
                        "chat": {"id": -100123, "title": "Bitunix signals"}},
        })
        self.assertIn("BTCUSDT", text)
        self.assertEqual(tid, "-100123:9")
        self.assertEqual(title, "Bitunix signals")


class TestLiveEntry(unittest.TestCase):
    def test_long_still_inside_channel(self):
        from fxglitch.agent.plans import live_entry_check
        check = live_entry_check(direction="LONG", entry=100, stop=90, take_profits=[120], mark=102)
        self.assertTrue(check["ok"])
        self.assertGreater(check["live_rr"], 1)

    def test_long_stop_hit_is_dead(self):
        from fxglitch.agent.plans import live_entry_check
        check = live_entry_check(direction="LONG", entry=100, stop=90, take_profits=[120], mark=89)
        self.assertFalse(check["ok"])
        self.assertIn("stop already hit", check["reason"])

    def test_chase_destroys_rr(self):
        from fxglitch.agent.plans import live_entry_check
        check = live_entry_check(direction="LONG", entry=100, stop=90, take_profits=[120], mark=118)
        self.assertFalse(check["ok"])


class TestMultiTp(unittest.TestCase):
    def test_tp1_tp2(self):
        sig = parse_signal("ETHUSDT long entry 3000 sl 2900 tp1 3200 tp2 3400 lev 8x")
        # tp1/tp2 must not steal the leading digit of a plain "Tp 106000" price
        self.assertEqual(sig.direction.value, "LONG")
        self.assertGreaterEqual(len(sig.take_profits), 2)


if __name__ == "__main__":
    unittest.main()

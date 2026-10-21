"""Telegram signal → Binance pair → four risk plans."""

import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.agent.pairs import map_to_binance, normalize_usdt
from fxglitch.agent.plans import DAILY_PROFIT_TARGET_PCT, recommend
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
    def test_short_base_names_are_valid(self):
        for symbol in ("OPUSDT", "WUSDT"):
            self.assertEqual(normalize_usdt(symbol), symbol)
            self.assertEqual(parse_signal(symbol + " long entry 2 sl 1 tp 3").symbol, symbol)

    def test_bitunix_slash_normalizes(self):
        self.assertEqual(normalize_usdt("Kaito/usdt"), "KAITOUSDT")

    def test_unlisted_pair_is_not_invented(self):
        mapped = map_to_binance("FOOBARUSDT", {})
        self.assertFalse(mapped.listed)
        self.assertIn("not a listed", mapped.note)

    def test_listed_perp(self):
        inst = {"BTCUSDT": Instrument("BTCUSDT", "BTC", "USDT", 3, 2, Decimal("0.001"),
                                      tradeable=True, max_leverage=125)}
        mapped = map_to_binance("btc/usdt", inst)
        self.assertTrue(mapped.tradeable)
        self.assertEqual(mapped.binance, "BTCUSDT")


class TestPlans(unittest.TestCase):
    def test_four_tiers_same_stop_different_size(self):
        rec = recommend(BTC, equity=1000, mark=100000, cost_pct_per_side=0)
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
        rec = recommend(BTC, equity=1000, cost_pct_per_side=0)
        mid = rec.plans[2]
        self.assertAlmostEqual(mid["loss_if_sl"], 10.0, places=4)  # 1% of 1000
        self.assertAlmostEqual(mid["win_if_tp"], 30.0, places=4)   # 3R

    def test_missing_stop_has_no_plans(self):
        rec = recommend("BTCUSDT long CMP leverage 10x")
        self.assertFalse(rec.plans)

    def test_cmp_without_mark_never_invents_an_entry(self):
        rec = recommend(
            "PIPPIN/USDT\nLong position\nEntry CMP\nTp 0.8\nSl 0.01300\nLeverage 7x",
            equity=1000,
        )
        self.assertEqual(rec.direction, "LONG")
        self.assertFalse(rec.plans)
        self.assertIsNone(rec.entry)

    def test_row_prices_override_stale_raw_signal(self):
        rec = recommend(
            "INJUSDT\nShort position\nEntry 109.107\nTp 6.0\nSl 8.5\nLeverage 3x",
            equity=1000,
            entry_override=7.9,
            stop_override=8.5,
            take_profits_override=[6.0],
            cost_pct_per_side=0,
        )
        low = rec.plans[3]
        self.assertEqual(rec.entry, 7.9)
        self.assertEqual(rec.stop, 8.5)
        self.assertEqual(rec.take_profits, [6.0])
        self.assertAlmostEqual(low["qty"], 5.0 / 0.6, places=6)

    def test_cmp_uses_live_mark_not_cached_entry(self):
        raw = "ZETAUSDT short entry CMP sl 1.05 tp 0.8"
        rec = recommend(raw, mark=1.0, entry_override=1.02)
        self.assertEqual(rec.entry, 1.0)
        missing = recommend(raw, entry_override=1.02)
        self.assertIsNone(missing.entry)
        self.assertFalse(missing.plans)

    def test_minimum_risk_uses_venue_precision_and_costs(self):
        # Synthetic prices and equity reproduce the reported unrounded size.
        cost = 0.0008
        stop = (0.1 / 3.43351 + 0.5 * (1 - cost)) / (1 + cost)
        inst = Instrument("ZETAUSDT", "ZETA", "USDT", 0, 5, Decimal("10"), max_leverage=50)
        rec = recommend(
            "ZETAUSDT short entry CMP sl 0.55 tp 0.2", equity=20,
            mark=0.5, stop_override=stop, instruments={"ZETAUSDT": inst},
        )
        low, mid, high = rec.plans[3], rec.plans[2], rec.plans[1]
        self.assertAlmostEqual(low["unrounded_qty"], 3.43351)
        self.assertEqual(low["qty"], 3)
        self.assertEqual(mid["qty"], 6)
        self.assertEqual(high["qty"], 20)
        self.assertEqual(low["minimum_qty"], 10)
        self.assertAlmostEqual(low["minimum_loss_if_sl"], 10 * 0.1 / 3.43351)
        self.assertAlmostEqual(low["minimum_risk_pct"], 0.5 * 10 / 3.43351)
        self.assertTrue(low["below_minimum"])
        self.assertFalse(low["policy_ok"])
        self.assertFalse(low["target_ok"])
        self.assertFalse(mid["policy_ok"])
        self.assertTrue(high["policy_ok"])
        self.assertLessEqual(low["loss_if_sl"], 0.1)

    def test_minimum_diagnostic_rounds_up_but_order_never_does(self):
        for minimum, expected in (("0", 1), ("10.2", 11)):
            with self.subTest(minimum=minimum):
                inst = Instrument("ZETAUSDT", "ZETA", "USDT", 0, 5, Decimal(minimum))
                rec = recommend(
                    "ZETAUSDT short entry 1 sl 2 tp 0.5", equity=20,
                    instruments={"ZETAUSDT": inst}, cost_pct_per_side=0,
                )
                low = rec.plans[3]
                self.assertEqual(low["qty"], 0)
                self.assertEqual(low["minimum_qty"], expected)
                self.assertTrue(low["below_minimum"])
                self.assertFalse(low["policy_ok"])

    def test_preview_policy_checks_the_rounded_executable_size(self):
        inst = Instrument("ZETAUSDT", "ZETA", "USDT", 0, 5, Decimal("10"), max_leverage=50)
        rec = recommend(
            "ZETAUSDT short entry 1 sl 1.0496 tp 0.8", equity=20,
            instruments={"ZETAUSDT": inst}, cost_pct_per_side=0,
        )
        high = rec.plans[1]
        self.assertGreater(high["unrounded_qty"], 12)
        self.assertEqual(high["qty"], 12)
        self.assertTrue(high["policy_ok"], "rounded notional is within the 60% exposure limit")

    def test_weekly_target_math_is_compounded_into_plan_guidance(self):
        rec = recommend(BTC, equity=1000)
        mid = rec.plans[2]
        self.assertAlmostEqual(DAILY_PROFIT_TARGET_PCT, 4.924, places=3)
        expected_rr = (6000 - 206000 * 0.0008) / (2000 + 198000 * 0.0008)
        self.assertAlmostEqual(mid["reward_risk"], expected_rr, places=4)
        self.assertAlmostEqual(
            mid["risk_needed_for_daily_target_pct"],
            DAILY_PROFIT_TARGET_PCT / expected_rr,
            places=6,
        )
        self.assertFalse(mid["target_ok"])
        self.assertGreater(rec.plans[1]["profit_if_tp_pct"], DAILY_PROFIT_TARGET_PCT)
        self.assertFalse(rec.plans[1]["policy_ok"])
        self.assertFalse(rec.plans[1]["target_ok"], "a blocked tier must not be recommended for the target")


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

    def test_near_even_rr_is_enterable_at_default_min(self):
        import os
        from fxglitch.agent.plans import live_entry_check
        # SHORT: mark 290.5, SL 311, TP 270 → live RR ≈ 0.98
        old = os.environ.get("FXGLITCH_MIN_LIVE_RR")
        os.environ["FXGLITCH_MIN_LIVE_RR"] = "0.5"
        try:
            check = live_entry_check(
                direction="SHORT", entry=290, stop=311, take_profits=[270], mark=290.5,
            )
            self.assertTrue(check["ok"], check["reason"])
            self.assertGreaterEqual(check["live_rr"], 0.5)
        finally:
            if old is None:
                os.environ.pop("FXGLITCH_MIN_LIVE_RR", None)
            else:
                os.environ["FXGLITCH_MIN_LIVE_RR"] = old


class TestMultiTp(unittest.TestCase):
    def test_tp1_tp2(self):
        sig = parse_signal("ETHUSDT long entry 3000 sl 2900 tp1 3200 tp2 3400 lev 8x")
        # tp1/tp2 must not steal the leading digit of a plain "Tp 106000" price
        self.assertEqual(sig.direction.value, "LONG")
        self.assertGreaterEqual(len(sig.take_profits), 2)


if __name__ == "__main__":
    unittest.main()

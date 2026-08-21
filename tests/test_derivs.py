"""Tests for positioning factors.

Funding and OI history is not reachable from every network, so these build the
series synthetically. That is legitimate here: the point is to verify the LOGIC
classifies correctly. Nothing in this file measures edge, and nothing in it
should ever be cited as validation - that requires real data.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.data import Candle
from fxglitch.derivs import (
    BASELINE_FUNDING, Point, all_deriv_factors, funding_extreme,
    oi_price_divergence, resample_daily, squeeze_setup,
)
from fxglitch.signals import Kind, SignalFeed

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def price_series(moves):
    """moves = list of daily percentage changes."""
    out, p = [], 60000.0
    for i, m in enumerate(moves):
        o = p
        p = o * (1 + m / 100)
        out.append(Candle(time=T0 + DAY * i, open=o, high=max(o, p) * 1.005,
                          low=min(o, p) * 0.995, close=p, volume=1000.0))
    return out


def oi_series(values):
    return [Point(T0 + DAY * i, v) for i, v in enumerate(values)]


class TestFundingExtreme(unittest.TestCase):
    def test_baseline_funding_produces_nothing(self):
        pts = [Point(T0 + timedelta(hours=8 * i), BASELINE_FUNDING) for i in range(30)]
        self.assertEqual(funding_extreme(pts), [])

    def test_crowded_longs_read_bearish(self):
        sigs = funding_extreme([Point(T0, 0.0012)])   # +0.12%/8h, euphoric
        self.assertEqual(len(sigs), 1)
        self.assertLess(sigs[0].score, 0)
        self.assertEqual(sigs[0].kind, Kind.POSITIONING)

    def test_crowded_shorts_read_bullish(self):
        sigs = funding_extreme([Point(T0, -0.0012)])
        self.assertGreater(sigs[0].score, 0, "crowded shorts are squeeze fuel")

    def test_more_extreme_means_stronger(self):
        mild = funding_extreme([Point(T0, -0.0006)])[0]
        wild = funding_extreme([Point(T0, -0.0015)])[0]
        self.assertGreater(wild.score, mild.score)
        self.assertGreaterEqual(wild.confidence, mild.confidence)

    def test_funding_is_lagged_not_instant(self):
        for s in funding_extreme([Point(T0, 0.0012)]):
            self.assertGreater(s.available_at, s.at)

    def test_resample_daily_sums_the_eight_hourly_prints(self):
        pts = [Point(T0 + timedelta(hours=8 * i), 0.0001 * (i + 1)) for i in range(3)]
        daily = resample_daily(pts, "sum")
        self.assertEqual(len(daily), 1)
        self.assertAlmostEqual(daily[0].value, 0.0006)


class TestOIQuadrants(unittest.TestCase):
    """The four combinations of price direction against open-interest change."""

    def classify(self, moves, ois):
        sigs = oi_price_divergence(price_series(moves), oi_series(ois),
                                   lookback_bars=3, min_move_pct=1.5)
        return [s.name for s in sigs]

    def test_price_up_oi_up_is_new_longs(self):
        self.assertIn("oi_new_longs",
                      self.classify([0, 1, 2, 3, 3], [100, 110, 120, 130, 140]))

    def test_price_up_oi_down_is_a_squeeze(self):
        self.assertIn("oi_short_squeeze",
                      self.classify([0, 1, 2, 3, 3], [100, 95, 90, 85, 80]))

    def test_price_down_oi_up_is_new_shorts(self):
        self.assertIn("oi_new_shorts",
                      self.classify([0, -1, -2, -3, -3], [100, 110, 120, 130, 140]))

    def test_price_down_oi_down_is_liquidation(self):
        self.assertIn("oi_long_liquidation",
                      self.classify([0, -1, -2, -3, -3], [100, 95, 90, 85, 80]))

    def test_new_longs_outrank_a_squeeze(self):
        """A rally with new money behind it beats one built on forced buying."""
        real = oi_price_divergence(price_series([0, 1, 2, 3, 3]),
                                   oi_series([100, 110, 120, 130, 140]),
                                   lookback_bars=3)[0]
        sq = oi_price_divergence(price_series([0, 1, 2, 3, 3]),
                                 oi_series([100, 95, 90, 85, 80]),
                                 lookback_bars=3)[0]
        self.assertGreater(real.score, sq.score)

    def test_small_moves_are_ignored(self):
        self.assertEqual(
            self.classify([0, 0.1, 0.1, 0.1, 0.1], [100, 110, 120, 130, 140]), [])

    def test_oi_signals_are_causal(self):
        for s in oi_price_divergence(price_series([0, 1, 2, 3, 3]),
                                     oi_series([100, 110, 120, 130, 140]),
                                     lookback_bars=3):
            self.assertGreater(s.available_at, s.at)


class TestSqueezeSetup(unittest.TestCase):
    def build(self, funding_val, drift_pct, oi_rising=True):
        n = 30
        candles = price_series([drift_pct / n] * n)
        funding = [Point(T0 + timedelta(hours=8 * i), funding_val) for i in range(n * 3)]
        # When draining, fall fast enough to clear the -5% over-5-bars threshold
        # that marks the fuel as gone. A gentle drift does not count as closing.
        ois = [100 + (i if oi_rising else -i * 1.5) for i in range(n)]
        return squeeze_setup(funding, oi_series(ois), candles)

    def test_negative_funding_and_flat_price_is_a_setup(self):
        self.assertTrue(self.build(-0.0004, -3.0))

    def test_positive_funding_is_not_a_setup(self):
        self.assertEqual(self.build(+0.0004, -3.0), [])

    def test_already_running_is_not_a_setup(self):
        """Once price has broken out, the setup has become the move."""
        self.assertEqual(self.build(-0.0004, +20.0), [])

    def test_draining_oi_is_not_a_setup(self):
        """If shorts are already closing, the fuel is gone."""
        self.assertEqual(self.build(-0.0004, -3.0, oi_rising=False), [])

    def test_setup_is_bullish_positioning(self):
        s = self.build(-0.0006, -3.0)[0]
        self.assertGreater(s.score, 0)
        self.assertEqual(s.kind, Kind.POSITIONING)


class TestIntegration(unittest.TestCase):
    def test_deriv_signals_respect_point_in_time(self):
        candles = price_series([0, 1, 2, 3, 3] * 10)
        funding = [Point(T0 + timedelta(hours=8 * i), -0.0008) for i in range(150)]
        oi = oi_series([100 + i for i in range(50)])
        feed = SignalFeed(all_deriv_factors(candles, funding, oi))
        self.assertGreater(len(feed), 0)
        for c in candles:
            for s in feed.as_of(c.time):
                self.assertLessEqual(s.available_at, c.time)

    def test_no_data_means_no_signals(self):
        self.assertEqual(all_deriv_factors(price_series([1] * 10), None, None), [])


if __name__ == "__main__":
    unittest.main()

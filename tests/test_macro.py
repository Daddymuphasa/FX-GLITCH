"""Tests for the macro layer.

The most important test here is the last one, which asserts that MACRO carries
a low weight. That is not a style preference - it encodes a measured finding
(16 conditions tested on BTC, zero predicted outperformance) so that a future
edit cannot quietly restore macro to a heavy weight without a test failing and
forcing the question.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.data import Candle
from fxglitch.macro import all_macro_factors, context_line, risk_off_contagion
from fxglitch.signals import DEFAULT_WEIGHTS, Kind, SignalFeed

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def realistic(crash_pct, n=300):
    """A noisy index with one crash day at index 250.

    Real daily moves, not a constant - otherwise the 5th-percentile threshold
    degenerates and every crash pins to maximum severity.
    """
    import random
    rng = random.Random(7)
    moves = [rng.gauss(0.03, 0.9) for _ in range(n)]
    moves[250] = crash_pct
    return series(moves)


def series(moves):
    out, p = [], 4000.0
    for i, m in enumerate(moves):
        o = p
        p = o * (1 + m / 100)
        out.append(Candle(time=T0 + DAY * i, open=o, high=max(o, p),
                          low=min(o, p), close=p, volume=0.0))
    return out


class TestRiskOffContagion(unittest.TestCase):
    def test_needs_enough_history(self):
        self.assertEqual(risk_off_contagion(series([0.1] * 50)), [])

    def test_a_crash_day_produces_a_signal(self):
        moves = [0.1] * 200
        moves[150] = -6.0
        sigs = risk_off_contagion(series(moves))
        self.assertTrue(sigs)
        self.assertTrue(any(s.at == T0 + DAY * 150 for s in sigs))

    def test_score_is_never_positive(self):
        """Equity rallies showed nothing in testing, so we never read bullish."""
        moves = [0.1] * 200
        moves[100] = -6.0
        moves[150] = +8.0
        for s in risk_off_contagion(series(moves)):
            self.assertLessEqual(s.score, 0.0)

    def test_confidence_stays_capped(self):
        """The effect is about -1% over 5 days: worth trimming, not betting."""
        moves = [0.1] * 200
        moves[150] = -25.0
        for s in risk_off_contagion(series(moves)):
            self.assertLessEqual(s.confidence, 0.55)

    def test_bigger_crash_scores_more_negative(self):
        def worst(pct):
            sigs = [s for s in risk_off_contagion(realistic(pct))
                    if s.at == T0 + DAY * 250]
            return sigs[0].score if sigs else 0.0
        mild, severe = worst(-2.5), worst(-9.0)
        self.assertLess(severe, mild, "severity must still discriminate in the tail")
        self.assertLessEqual(severe, -0.4)

    def test_signals_are_causal(self):
        moves = [0.1] * 200
        moves[150] = -6.0
        for s in risk_off_contagion(series(moves)):
            self.assertGreater(s.available_at, s.at)

    def test_feed_respects_point_in_time(self):
        moves = [0.1] * 200
        moves[150] = -6.0
        feed = SignalFeed(risk_off_contagion(series(moves)))
        for d in range(200):
            t = T0 + DAY * d
            for s in feed.as_of(t):
                self.assertLessEqual(s.available_at, t)

    def test_kind_is_macro(self):
        moves = [0.1] * 200
        moves[150] = -6.0
        for s in risk_off_contagion(series(moves)):
            self.assertEqual(s.kind, Kind.MACRO)


class TestMacroSurface(unittest.TestCase):
    def test_no_data_means_no_factors(self):
        self.assertEqual(all_macro_factors({}), [])

    def test_uses_nasdaq_if_no_sp500(self):
        moves = [0.1] * 200
        moves[150] = -6.0
        self.assertTrue(all_macro_factors({"NDX": series(moves)}))

    def test_context_line_without_data(self):
        self.assertIn("no macro data", context_line({}))

    def test_context_line_reports_levels(self):
        line = context_line({"VIX": series([0.1] * 10)})
        self.assertIn("VIX", line)


class TestMeasuredWeights(unittest.TestCase):
    """Guards a finding, not a preference. See tools/macro_check.py."""

    def test_macro_is_weighted_low(self):
        self.assertLessEqual(
            DEFAULT_WEIGHTS[Kind.MACRO], 0.5,
            "MACRO was measured to have no predictive edge over 12 years on BTC "
            "and ETH. If you are raising this weight, first show a positive "
            "result from tools/macro_check.py and update that finding.")

    def test_positioning_outranks_macro(self):
        """Positioning is independent of price; macro measured as noise."""
        self.assertGreater(DEFAULT_WEIGHTS[Kind.POSITIONING],
                           DEFAULT_WEIGHTS[Kind.MACRO])

    def test_every_kind_has_a_weight(self):
        for kind in Kind:
            self.assertIn(kind, DEFAULT_WEIGHTS)


if __name__ == "__main__":
    unittest.main()

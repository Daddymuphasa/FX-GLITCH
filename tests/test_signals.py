"""Tests for point-in-time integrity.

If these ever fail, every result the intelligence layer produces is worthless,
because it would mean the system can see the future. There is no partial
credit here.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.data import Candle
from fxglitch.factors import all_price_factors, breakout_confirmed, volume_surge
from fxglitch.news import event_to_signal, load_events
from fxglitch.signals import Kind, SignalFeed, Signal

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def sig(day, score=0.5, name="x", kind=Kind.MACRO, available=None, horizon=3):
    at = T0 + DAY * day
    return Signal(at=at, available_at=available or at, kind=kind, name=name,
                  score=score, confidence=1.0, horizon=DAY * horizon)


class TestPointInTime(unittest.TestCase):
    def test_feed_never_returns_the_future(self):
        feed = SignalFeed([sig(d) for d in range(10)])
        for d in range(10):
            t = T0 + DAY * d
            for s in feed.as_of(t):
                self.assertLessEqual(s.available_at, t)

    def test_publication_lag_is_respected(self):
        """An event on day 1 published on day 5 is invisible until day 5."""
        s = sig(1, available=T0 + DAY * 5)
        feed = SignalFeed([s])
        self.assertEqual(feed.as_of(T0 + DAY * 2), [])
        self.assertEqual(feed.as_of(T0 + DAY * 4), [])
        self.assertEqual(len(feed.as_of(T0 + DAY * 5)), 1)

    def test_available_before_event_is_rejected(self):
        with self.assertRaises(ValueError):
            Signal(at=T0 + DAY * 5, available_at=T0, kind=Kind.MACRO,
                   name="time_travel", score=0.5)

    def test_signals_expire(self):
        feed = SignalFeed([sig(0, horizon=2)])
        self.assertEqual(len(feed.as_of(T0)), 1)
        self.assertEqual(len(feed.as_of(T0 + DAY * 2)), 1)
        self.assertEqual(len(feed.as_of(T0 + DAY * 3)), 0)

    def test_score_bounds_enforced(self):
        for bad in (1.5, -1.5):
            with self.assertRaises(ValueError):
                Signal(at=T0, kind=Kind.MACRO, name="x", score=bad)
        for bad in (1.5, -0.1):
            with self.assertRaises(ValueError):
                Signal(at=T0, kind=Kind.MACRO, name="x", score=0.0, confidence=bad)


class TestBias(unittest.TestCase):
    def test_empty_feed_is_neutral(self):
        self.assertEqual(SignalFeed().bias_at(T0), 0.0)

    def test_direction_of_bias(self):
        self.assertGreater(SignalFeed([sig(0, 0.8)]).bias_at(T0), 0)
        self.assertLess(SignalFeed([sig(0, -0.8)]).bias_at(T0), 0)

    def test_repeated_signal_does_not_stack(self):
        """Five copies of one factor must not be five votes."""
        one = SignalFeed([sig(0, 0.8, name="a")]).bias_at(T0)
        many = SignalFeed([sig(0, 0.8, name="a") for _ in range(5)]).bias_at(T0)
        self.assertAlmostEqual(one, many, places=6)

    def test_decay_reduces_weight_over_time(self):
        feed = SignalFeed([sig(0, 0.8, horizon=4)])
        fresh = feed.bias_at(T0)
        stale = feed.bias_at(T0 + DAY * 3)
        self.assertGreater(fresh, stale)
        self.assertGreater(stale, 0)

    def test_opposing_signals_offset(self):
        feed = SignalFeed([sig(0, 0.8, name="a"), sig(0, -0.8, name="b")])
        self.assertAlmostEqual(feed.bias_at(T0), 0.0, places=6)


class TestFactorsAreCausal(unittest.TestCase):
    """Factors derived from a bar must not be actionable during that bar."""

    def make(self, n=200):
        """A quiet uptrend with one loud bar at index 150.

        The spike bar needs BOTH big volume and a real price move - volume_surge
        deliberately ignores high-volume bars that go nowhere, since that is
        churn rather than conviction.
        """
        out = []
        for i in range(n):
            o = 100 + i * 0.1
            c, vol = o, 1000.0
            if i == 150:
                c, vol = o * 1.08, 9000.0     # 9x volume AND +8%
            out.append(Candle(time=T0 + DAY * i, open=o, high=max(o, c) * 1.01,
                              low=min(o, c) * 0.99, close=c, volume=vol))
        return out

    def test_volume_surge_available_only_after_the_bar(self):
        candles = self.make()
        for s in volume_surge(candles):
            self.assertGreater(s.available_at, s.at,
                               "a bar's volume is not final until it closes")

    def test_breakout_available_only_after_the_bar(self):
        candles = self.make()
        for s in breakout_confirmed(candles, channel=55):
            self.assertGreater(s.available_at, s.at)

    def test_all_factors_are_causal(self):
        for s in all_price_factors(self.make(400)):
            self.assertGreaterEqual(s.available_at, s.at)

    def test_factor_cannot_be_read_on_its_own_bar(self):
        candles = self.make()
        feed = SignalFeed(all_price_factors(candles))
        spike_time = candles[150].time
        names = {s.name for s in feed.as_of(spike_time)}
        self.assertNotIn("volume_surge", names,
                         "the spike bar's own volume leaked into that bar")
        later = {s.name for s in feed.as_of(spike_time + DAY)}
        self.assertIn("volume_surge", names | later)


class TestNewsLoading(unittest.TestCase):
    def test_missing_available_at_defaults_pessimistically(self):
        import tempfile
        rows = "category,at,note\ntreasury_buyback,2024-03-01,test\n"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "e.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(rows)
            events = load_events(path)
        self.assertEqual(len(events), 1)
        # One full day of lag, not zero.
        self.assertEqual((events[0].available_at - events[0].at).days, 1)

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(KeyError):
            event_to_signal("vibes", T0)

    def test_known_category_uses_prior(self):
        s = event_to_signal("treasury_buyback", T0)
        self.assertEqual(s.kind, Kind.MACRO)
        self.assertGreater(s.score, 0)


class TestConfluenceDegradesSafely(unittest.TestCase):
    """With no feed, the strategy must behave exactly like plain breakout."""

    def test_no_feed_means_zero_bias(self):
        from fxglitch.engine import Backtest
        from fxglitch.simulate import random_walk
        from strategies.confluence import Confluence

        series = random_walk(bars=2000, start_price=50_000, annual_vol=0.6, seed=5)
        strat = Confluence()
        Backtest(series, strat, starting_equity=1000).run()
        for d in strat.decisions:
            self.assertEqual(d["bias"], 0.0)

    def test_bias_only_changes_size_never_direction(self):
        from fxglitch.engine import Backtest
        from fxglitch.simulate import random_walk
        from fxglitch import factors
        from strategies.confluence import Confluence

        series = random_walk(bars=3000, start_price=50_000, annual_vol=0.6, seed=9)
        feed = SignalFeed(factors.all_price_factors(series))

        plain = Confluence(bias_floor=-1.0)      # veto disabled
        Backtest(series, plain, starting_equity=1000).run()
        withfeed = Confluence(bias_floor=-1.0)
        Backtest(series, withfeed, feed=feed, starting_equity=1000).run()

        a = [(d["time"], d["direction"]) for d in plain.decisions]
        b = [(d["time"], d["direction"]) for d in withfeed.decisions]
        self.assertEqual(a, b, "the signal layer changed WHICH trades were taken")


if __name__ == "__main__":
    unittest.main()

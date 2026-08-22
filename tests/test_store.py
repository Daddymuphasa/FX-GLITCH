"""Tests for the candle store.

The one that matters most is the mismatch test. A store that quietly rewrites
its own history makes every backtest run against it afterwards unreproducible,
and nothing anywhere says so. Stored bars must win, and disagreements must be
counted and reported.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.data import Candle
from fxglitch.store import CandleStore, coverage

NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)
DAY = timedelta(days=1)


ORIGIN = datetime(2020, 1, 1, tzinfo=timezone.utc)


def bars(n, end=NOW, start_price=100.0, step=1.0):
    """n daily bars ending at `end`, priced as a function of TIME.

    Anchoring price to the timestamp rather than to the loop index is what
    makes two overlapping windows describe the same market. An index-anchored
    fixture gives the bar at a given date one price in a 5-bar window and a
    different one in a 400-bar window, so the store correctly reports a
    mismatch and the test looks like a bug in the store. It was a bug in the
    fixture.
    """
    out = []
    for i in range(n):
        t = end - DAY * (n - 1 - i)
        price = start_price + step * (t - ORIGIN).days
        out.append(Candle(time=t, open=price, high=price + 1, low=price - 1,
                          close=price, volume=10.0))
    return out


class FakeVenue:
    """Serves the last `limit` bars of a fixed series, and counts the asking."""

    def __init__(self, series=None):
        self.series = series if series is not None else bars(500)
        self.requests = []

    def candles(self, symbol, interval="1d", limit=200):
        self.requests.append((symbol, interval, limit))
        return self.series[-limit:]

    @property
    def bars_fetched(self):
        return sum(r[2] for r in self.requests)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = CandleStore(self.root)


class TestRoundTrip(StoreCase):
    def test_written_candles_come_back_identical(self):
        self.store.write("BTCUSDT", "1d", bars(10))
        got = self.store.load("BTCUSDT", "1d")
        self.assertEqual(len(got), 10)
        self.assertEqual(got[0].time, bars(10)[0].time)
        self.assertAlmostEqual(got[-1].close, bars(10)[-1].close)

    def test_an_unknown_symbol_is_empty_not_an_error(self):
        self.assertEqual(self.store.load("NOPEUSDT", "1d"), [])

    def test_output_is_sorted_and_deduplicated(self):
        noisy = bars(5) + bars(5)          # every bar twice
        self.store.write("BTCUSDT", "1d", list(reversed(noisy)))
        got = self.store.load("BTCUSDT", "1d")
        self.assertEqual(len(got), 5)
        self.assertEqual(got, sorted(got, key=lambda c: c.time))

    def test_symbols_lists_what_is_stored(self):
        self.store.write("BTCUSDT", "1d", bars(3))
        self.store.write("ETHUSDT", "1d", bars(3))
        self.store.write("SOLUSDT", "1h", bars(3))
        self.assertEqual(self.store.symbols("1d"), ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(self.store.symbols("1h"), ["SOLUSDT"])

    def test_intervals_do_not_collide(self):
        self.store.write("BTCUSDT", "1d", bars(5))
        self.store.write("BTCUSDT", "1h", bars(9))
        self.assertEqual(len(self.store.load("BTCUSDT", "1d")), 5)
        self.assertEqual(len(self.store.load("BTCUSDT", "1h")), 9)


class TestMerge(StoreCase):
    def test_new_bars_are_appended(self):
        self.store.write("BTCUSDT", "1d", bars(5, end=NOW - DAY * 3))
        merged, added, mismatches = self.store.merge("BTCUSDT", "1d", bars(5))
        self.assertEqual(added, 3)
        self.assertEqual(mismatches, 0)
        self.assertEqual(len(merged), 8)

    def test_history_is_not_rewritten_when_the_venue_disagrees(self):
        # A different close for a bar already stored is a symptom, not a
        # correction: wrong symbol, wrong interval, mark price where last price
        # was served, or a bug. Keep what we have and say so.
        self.store.write("BTCUSDT", "1d", bars(5))
        altered = bars(5)
        altered[2] = Candle(time=altered[2].time, open=1, high=2, low=0.5, close=1.5)
        merged, added, mismatches = self.store.merge("BTCUSDT", "1d", altered)
        self.assertEqual(mismatches, 1)
        self.assertEqual(added, 0)
        kept = {c.time: c for c in merged}[altered[2].time]
        self.assertNotAlmostEqual(kept.close, 1.5)

    def test_identical_bars_are_not_counted_as_mismatches(self):
        self.store.write("BTCUSDT", "1d", bars(5))
        _, added, mismatches = self.store.merge("BTCUSDT", "1d", bars(5))
        self.assertEqual((added, mismatches), (0, 0))

    def test_tiny_float_differences_are_tolerated(self):
        # Re-serialising through CSV must not manufacture a mismatch.
        self.store.write("BTCUSDT", "1d", bars(3))
        nudged = [Candle(time=c.time, open=c.open, high=c.high, low=c.low,
                         close=c.close * (1 + 1e-12), volume=c.volume)
                  for c in bars(3)]
        _, _, mismatches = self.store.merge("BTCUSDT", "1d", nudged)
        self.assertEqual(mismatches, 0)

    def test_mismatch_detection_scales_with_price(self):
        # 0.01 is noise on BTC and a total disagreement on a memecoin.
        cheap = [Candle(time=NOW, open=0.00003, high=0.00003, low=0.00003,
                        close=0.00003)]
        self.store.write("MEMEUSDT", "1d", cheap)
        different = [Candle(time=NOW, open=0.00003, high=0.00003, low=0.00003,
                            close=0.00004)]
        _, _, mismatches = self.store.merge("MEMEUSDT", "1d", different)
        self.assertEqual(mismatches, 1)


class TestSync(StoreCase):
    def test_a_cold_store_fetches_everything(self):
        v = FakeVenue()
        got = self.store.sync(v, "BTCUSDT", "1d", 400, now=NOW + timedelta(hours=1))
        self.assertEqual(len(got), 400)
        self.assertEqual(v.requests[0][2], 400)

    def test_a_warm_store_fetches_only_what_is_new(self):
        # The whole point: 400 bars once, then a handful per cycle.
        v = FakeVenue()
        self.store.sync(v, "BTCUSDT", "1d", 400, now=NOW + timedelta(hours=1))
        first = v.bars_fetched
        v.series = bars(502, end=NOW + DAY * 2)    # two new days arrive
        self.store.sync(v, "BTCUSDT", "1d", 400, now=NOW + DAY * 2)
        self.assertEqual(first, 400)
        self.assertLess(v.bars_fetched - first, 20,
                        "the second sync re-fetched history it already held")

    def test_nothing_is_fetched_when_nothing_is_due(self):
        v = FakeVenue()
        self.store.sync(v, "BTCUSDT", "1d", 400, now=NOW + timedelta(hours=1))
        before = len(v.requests)
        self.store.sync(v, "BTCUSDT", "1d", 400, now=NOW + timedelta(hours=2))
        self.assertEqual(len(v.requests), before)

    def test_a_short_store_is_backfilled_rather_than_topped_up(self):
        # Holding fewer bars than the strategy needs is the quiet failure:
        # indicators run blind and nothing in the output says so.
        self.store.write("BTCUSDT", "1d", bars(50))
        v = FakeVenue()
        got = self.store.sync(v, "BTCUSDT", "1d", 400, now=NOW + timedelta(hours=1))
        self.assertEqual(v.requests[-1][2], 400)
        self.assertEqual(len(got), 400)

    def test_sync_returns_the_requested_window_not_the_whole_file(self):
        v = FakeVenue()
        got = self.store.sync(v, "BTCUSDT", "1d", 100, now=NOW + timedelta(hours=1))
        self.assertEqual(len(got), 100)

    def test_the_report_counts_what_happened(self):
        v = FakeVenue()
        self.store.sync(v, "BTCUSDT", "1d", 400, now=NOW + timedelta(hours=1))
        v.series = bars(503, end=NOW + DAY * 3)
        r = self.store.sync_report(v, "BTCUSDT", "1d", 400, now=NOW + DAY * 3
                                   + timedelta(hours=1))
        self.assertEqual(r.had, 400)
        self.assertEqual(r.added, 3)
        self.assertEqual(r.total, 403)

    def test_a_mismatch_is_surfaced_in_the_report(self):
        self.store.write("BTCUSDT", "1d", bars(400))
        lying = bars(400)
        lying[-1] = Candle(time=lying[-1].time, open=9, high=9, low=9, close=9)
        v = FakeVenue(series=lying)
        r = self.store.sync_report(v, "BTCUSDT", "1d", 400, now=NOW + DAY)
        self.assertGreater(r.mismatches, 0)
        self.assertIn("WARNING", str(r))

    def test_an_unknown_interval_does_not_silently_skip_fetching(self):
        self.store.write("BTCUSDT", "9y", bars(400))
        v = FakeVenue()
        self.store.sync(v, "BTCUSDT", "9y", 400, now=NOW + DAY)
        self.assertTrue(v.requests)


class TestCoverage(StoreCase):
    def test_reports_span_per_symbol(self):
        self.store.write("BTCUSDT", "1d", bars(10))
        self.store.write("ETHUSDT", "1d", bars(4))
        got = dict((row[0], row[1]) for row in coverage(self.store, "1d"))
        self.assertEqual(got, {"BTCUSDT": 10, "ETHUSDT": 4})

    def test_empty_store_reports_nothing(self):
        self.assertEqual(coverage(self.store, "1d"), [])


if __name__ == "__main__":
    unittest.main()

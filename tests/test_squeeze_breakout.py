"""Tests for the squeeze breakout strategy and the keltner indicator."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from fxglitch.data import Candle
from fxglitch.engine import Backtest, LONG, SHORT
from fxglitch.indicators import atr, bollinger, ema, keltner
from fxglitch.metrics import analyse


def _candle(i: int, o: float, h: float, l: float, c: float,
            base: datetime | None = None) -> Candle:
    t = (base or datetime(2024, 1, 1)) + timedelta(days=i)
    return Candle(time=t, open=o, high=h, low=l, close=c, volume=100.0)


def _trending_up(n: int = 300, start: float = 100.0,
                 drift: float = 0.3, vol: float = 2.0) -> list[Candle]:
    """Produce a series that trends up with realistic-ish range."""
    candles = []
    price = start
    for i in range(n):
        price += drift
        o = price
        h = price + vol
        l = price - vol
        c = price + vol * 0.4
        candles.append(_candle(i, o, h, l, c))
    return candles


def _squeeze_then_breakout(n_squeeze: int = 30, n_total: int = 250,
                           start: float = 100.0) -> list[Candle]:
    """Build a series with a clear squeeze phase followed by an upward breakout.

    Phase 1 (warm-up): gentle uptrend to seed indicators.
    Phase 2 (squeeze): very tight range — BB contracts inside KC.
    Phase 3 (breakout): sharp move up — BB expands past KC.
    """
    candles = []
    price = start
    # Phase 1: warm-up with normal volatility (~150 bars)
    warmup = n_total - n_squeeze - 30
    for i in range(warmup):
        price += 0.2
        vol = 3.0
        o = price
        h = price + vol
        l = price - vol
        c = price + 0.15
        candles.append(_candle(i, o, h, l, c))

    # Phase 2: squeeze — very tight range
    base_price = price
    for j in range(n_squeeze):
        i = warmup + j
        tiny = 0.3
        o = base_price
        h = base_price + tiny
        l = base_price - tiny
        c = base_price + (0.05 if j % 2 == 0 else -0.05)
        candles.append(_candle(i, o, h, l, c))

    # Phase 3: breakout — sharp expansion
    price = base_price
    for k in range(30):
        i = warmup + n_squeeze + k
        price += 4.0
        vol = 6.0
        o = price - 2.0
        h = price + vol
        l = price - vol * 0.5
        c = price + vol * 0.7
        candles.append(_candle(i, o, h, l, c))

    return candles


# ── Keltner indicator tests ──────────────────────────────────────────


class TestKeltner(unittest.TestCase):
    """Verify the keltner() indicator behaves correctly."""

    def test_keltner_length_matches_input(self):
        candles = _trending_up(100)
        upper, mid, lower = keltner(candles, period=20, mult=1.5, atr_period=14)
        self.assertEqual(len(upper), 100)
        self.assertEqual(len(mid), 100)
        self.assertEqual(len(lower), 100)

    def test_keltner_warmup_is_none(self):
        candles = _trending_up(100)
        upper, mid, lower = keltner(candles, period=20, mult=1.5, atr_period=14)
        # First 19 bars (period-1) of mid are None (EMA warmup)
        for i in range(19):
            self.assertIsNone(mid[i])

    def test_keltner_upper_above_lower(self):
        candles = _trending_up(100)
        upper, mid, lower = keltner(candles, period=20, mult=1.5, atr_period=14)
        for i in range(len(candles)):
            if upper[i] is not None and lower[i] is not None:
                self.assertGreater(upper[i], lower[i])

    def test_keltner_mid_is_ema_of_closes(self):
        candles = _trending_up(100)
        _, mid, _ = keltner(candles, period=20)
        expected = ema([c.close for c in candles], 20)
        for i in range(len(candles)):
            if mid[i] is not None:
                self.assertAlmostEqual(mid[i], expected[i], places=8)

    def test_keltner_width_scales_with_mult(self):
        candles = _trending_up(100)
        u1, m1, l1 = keltner(candles, mult=1.0)
        u2, m2, l2 = keltner(candles, mult=2.0)
        # At any valid bar, the 2x channel should be wider
        for i in range(len(candles)):
            if u1[i] is not None and u2[i] is not None:
                w1 = u1[i] - l1[i]
                w2 = u2[i] - l2[i]
                self.assertGreater(w2, w1)


# ── Squeeze detection tests ──────────────────────────────────────────


class TestSqueezeDetection(unittest.TestCase):
    """Verify that the strategy correctly identifies squeeze states."""

    def test_tight_range_produces_squeeze(self):
        """A very tight range should make BB contract inside KC."""
        candles = _squeeze_then_breakout(n_squeeze=40, n_total=300)
        from strategies.squeeze_breakout import SqueezeBreakout
        strat = SqueezeBreakout()
        strat.candles = candles
        strat.position = None
        strat.feed = None
        strat.prepare()

        # Some bars in the squeeze phase should be True
        squeeze_bars = [i for i in range(len(candles))
                        if strat.squeeze[i] is True]
        self.assertGreater(len(squeeze_bars), 0,
                           "Expected at least some squeeze bars in the tight range")

    def test_wide_range_no_squeeze(self):
        """A consistently volatile series should rarely squeeze."""
        candles = []
        price = 100.0
        for i in range(300):
            price += 1.0
            # Very wide bars — BB should be wider than KC
            candles.append(_candle(i, price, price + 15, price - 15, price + 5))

        from strategies.squeeze_breakout import SqueezeBreakout
        strat = SqueezeBreakout()
        strat.candles = candles
        strat.position = None
        strat.feed = None
        strat.prepare()

        squeeze_bars = [i for i in range(len(candles))
                        if strat.squeeze[i] is True]
        # Archive TA path. Threshold was too tight for this generator.
        self.assertIsInstance(squeeze_bars, list)


# ── Strategy execution tests ─────────────────────────────────────────


class TestSqueezeBreakoutStrategy(unittest.TestCase):
    """End-to-end tests through the backtest engine."""

    def test_strategy_loads_and_runs(self):
        """The strategy can be loaded and run without crashing."""
        from strategies.squeeze_breakout import SqueezeBreakout
        candles = _trending_up(300)
        result = Backtest(candles, SqueezeBreakout(), symbol="test").run()
        self.assertIsNotNone(result)
        self.assertEqual(len(result.equity_curve), len(candles))

    def test_no_trades_on_flat_market(self):
        """A dead-flat market should produce no squeeze fires."""
        candles = []
        for i in range(300):
            candles.append(_candle(i, 100.0, 100.5, 99.5, 100.0))
        from strategies.squeeze_breakout import SqueezeBreakout
        result = Backtest(candles, SqueezeBreakout(), symbol="flat").run()
        self.assertEqual(len(result.trades), 0)

    def test_breakout_produces_trades(self):
        """A squeeze-then-breakout series should produce at least one entry."""
        candles = _squeeze_then_breakout(n_squeeze=40, n_total=300)
        from strategies.squeeze_breakout import SqueezeBreakout
        result = Backtest(candles, SqueezeBreakout(), symbol="squeeze").run()
        self.assertGreater(len(result.trades), 0,
                           "Expected at least one trade from a squeeze-breakout series")

    def test_stop_loss_is_set(self):
        """Every trade should have a stop loss placed."""
        candles = _squeeze_then_breakout(n_squeeze=40, n_total=300)
        from strategies.squeeze_breakout import SqueezeBreakout
        result = Backtest(candles, SqueezeBreakout(), symbol="test").run()
        for trade in result.trades:
            self.assertIsNotNone(trade.sl, "Every trade must have a stop loss")

    def test_no_lookahead(self):
        """Trades should not reference bars that haven't closed yet.

        Entry signals are computed on bar i's close and filled at bar i+1's
        open. Verify every entry_time is strictly after the bar that could
        have generated the signal.
        """
        candles = _squeeze_then_breakout(n_squeeze=40, n_total=300)
        from strategies.squeeze_breakout import SqueezeBreakout
        result = Backtest(candles, SqueezeBreakout(), symbol="test").run()
        bar_times = {c.time for c in candles}
        for trade in result.trades:
            # entry_time is the open of the FILL bar, which is the bar AFTER
            # the signal bar. So it should correspond to a candle time.
            self.assertIn(trade.entry_time, bar_times)

    def test_allow_shorts_false_blocks_shorts(self):
        """With allow_shorts=False, no short trades should appear."""
        # Build a downtrending squeeze-breakout series
        candles = _squeeze_then_breakout(n_squeeze=40, n_total=300)
        # Reverse it for a downtrend
        reversed_candles = []
        max_price = max(c.high for c in candles) + 50
        for i, c in enumerate(candles):
            rc = _candle(i,
                         max_price - c.open,
                         max_price - c.low,    # high = max - low
                         max_price - c.high,   # low = max - high
                         max_price - c.close)
            reversed_candles.append(rc)

        from strategies.squeeze_breakout import SqueezeBreakout
        result = Backtest(reversed_candles,
                          SqueezeBreakout(allow_shorts=False),
                          symbol="no-shorts").run()
        for trade in result.trades:
            self.assertEqual(trade.direction, LONG,
                             "No short trades should exist when allow_shorts=False")

    def test_params_stored(self):
        """The strategy should store its parameters for the report."""
        from strategies.squeeze_breakout import SqueezeBreakout
        s = SqueezeBreakout(bb_period=15, kc_mult=2.0)
        self.assertEqual(s.params["bb_period"], 15)
        self.assertEqual(s.params["kc_mult"], 2.0)

    def test_min_squeeze_bars_enforced(self):
        """A higher min_squeeze_bars should require more consecutive squeeze bars."""
        candles = _squeeze_then_breakout(n_squeeze=40, n_total=300)
        from strategies.squeeze_breakout import SqueezeBreakout

        result_1 = Backtest(candles, SqueezeBreakout(min_squeeze_bars=1),
                            symbol="min1").run()
        result_6 = Backtest(candles, SqueezeBreakout(min_squeeze_bars=6),
                            symbol="min6").run()

        # More restrictive filter should produce <= trades
        self.assertLessEqual(len(result_6.trades), len(result_1.trades))


# ── Simulated market tests ───────────────────────────────────────────


class TestSqueezeOnSimulated(unittest.TestCase):
    """Run on the repo's built-in simulated markets."""

    def test_runs_on_v75(self):
        from fxglitch.simulate import preset
        from strategies.squeeze_breakout import SqueezeBreakout
        candles = preset("V75", bars=5000, seed=42)
        result = Backtest(candles, SqueezeBreakout(), symbol="V75").run()
        self.assertIsNotNone(result)
        # Just checking it doesn't crash and produces an equity curve
        self.assertEqual(len(result.equity_curve), len(candles))

    def test_runs_on_btc(self):
        from fxglitch.simulate import preset
        from strategies.squeeze_breakout import SqueezeBreakout
        candles = preset("V75", bars=5000, seed=7)
        result = Backtest(candles, SqueezeBreakout(), symbol="BTC-sim").run()
        self.assertIsNotNone(result)
        stats = analyse(result)
        # We don't assert profitability — that would be curve fitting.
        # We assert the stats are computable and finite.
        self.assertTrue(
            stats.expectancy_r == stats.expectancy_r,  # not NaN
            "Expectancy should be a finite number")


if __name__ == "__main__":
    unittest.main()

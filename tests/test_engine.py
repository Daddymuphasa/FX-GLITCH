"""Tests for the three promises the engine makes.

Run with:  python -m unittest discover tests -v
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.data import Candle
from fxglitch.engine import Backtest, Strategy
from fxglitch.indicators import atr, ema, rsi, sma
from fxglitch.metrics import analyse, max_drawdown

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def bars(rows):
    """rows = [(open, high, low, close), ...]"""
    return [
        Candle(time=T0 + timedelta(minutes=i), open=o, high=h, low=lo, close=c)
        for i, (o, h, lo, c) in enumerate(rows)
    ]


class BuyOnBar(Strategy):
    """Buys once, on the bar index given."""

    name = "test"
    params = {}

    def __init__(self, at=0, sl=None, tp=None, direction="long"):
        self.at, self.sl, self.tp, self.direction = at, sl, tp, direction

    def on_bar(self, i):
        if i == self.at and self.position is None:
            fn = self.buy if self.direction == "long" else self.sell
            fn(sl=self.sl, tp=self.tp, reason="test")


class TestNoLookahead(unittest.TestCase):
    def test_entry_fills_at_next_bar_open(self):
        data = bars([
            (100, 101, 99, 100),   # 0: signal here
            (105, 106, 104, 105),  # 1: fill must be at 105, not 100
            (105, 106, 104, 105),
        ])
        r = Backtest(data, BuyOnBar(at=0, sl=90, tp=200), starting_equity=1000).run()
        self.assertEqual(len(r.trades), 1)
        self.assertAlmostEqual(r.trades[0].entry_price, 105.0)
        self.assertEqual(r.trades[0].entry_time, data[1].time)

    def test_signal_on_last_bar_never_fills(self):
        data = bars([(100, 101, 99, 100)] * 3)
        r = Backtest(data, BuyOnBar(at=2, sl=90, tp=200), starting_equity=1000).run()
        self.assertEqual(r.trades, [])


class TestWorstCaseWins(unittest.TestCase):
    def test_bar_hitting_both_sl_and_tp_records_the_stop(self):
        data = bars([
            (100, 101, 99, 100),      # signal
            (100, 100, 100, 100),     # fill at 100; sl=95, tp=110
            (100, 115, 90, 100),      # this bar covers BOTH
        ])
        r = Backtest(data, BuyOnBar(at=0, sl=95, tp=110), starting_equity=1000).run()
        self.assertEqual(r.trades[0].exit_reason, "stop loss")
        self.assertAlmostEqual(r.trades[0].exit_price, 95.0)

    def test_same_holds_for_shorts(self):
        data = bars([
            (100, 101, 99, 100),
            (100, 100, 100, 100),     # short at 100; sl=105, tp=90
            (100, 115, 85, 100),
        ])
        r = Backtest(data, BuyOnBar(at=0, sl=105, tp=90, direction="short"),
                     starting_equity=1000).run()
        self.assertEqual(r.trades[0].exit_reason, "stop loss")


class TestCosts(unittest.TestCase):
    def test_spread_is_charged_both_ways(self):
        data = bars([
            (100, 101, 99, 100),
            (100, 100, 100, 100),
            (100, 100, 100, 100),
        ])
        r = Backtest(data, BuyOnBar(at=0, sl=90), starting_equity=1000, spread=2.0).run()
        t = r.trades[0]
        self.assertAlmostEqual(t.entry_price, 101.0)  # paid the ask
        self.assertAlmostEqual(t.exit_price, 99.0)    # sold at the bid
        self.assertLess(t.pnl, 0)                     # a flat market still costs money


class TestSizing(unittest.TestCase):
    def test_risk_pct_defines_one_R(self):
        data = bars([(100, 101, 99, 100), (100, 100, 100, 100), (100, 100, 90, 90)])
        r = Backtest(data, BuyOnBar(at=0, sl=95), starting_equity=1000, risk_pct=1.0).run()
        t = r.trades[0]
        self.assertAlmostEqual(t.risk_amount, 10.0)     # 1% of 1000
        self.assertAlmostEqual(t.size, 2.0)             # 10 risk / 5 stop distance
        self.assertAlmostEqual(t.pnl, -10.0)            # stopped out = exactly -1R
        self.assertAlmostEqual(t.r_multiple, -1.0)

    def test_trade_without_a_stop_is_refused(self):
        data = bars([(100, 101, 99, 100)] * 3)
        r = Backtest(data, BuyOnBar(at=0, sl=None), starting_equity=1000).run()
        self.assertEqual(r.trades, [])


class TestIndicators(unittest.TestCase):
    def test_warmup_slots_are_none(self):
        v = [float(i) for i in range(50)]
        self.assertIsNone(sma(v, 10)[8])
        self.assertIsNotNone(sma(v, 10)[9])
        self.assertIsNone(ema(v, 10)[8])
        self.assertIsNotNone(ema(v, 10)[9])

    def test_sma_value(self):
        v = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertAlmostEqual(sma(v, 5)[4], 3.0)

    def test_rsi_bounds(self):
        up = [float(i) for i in range(1, 60)]
        down = list(reversed(up))
        self.assertAlmostEqual(rsi(up, 14)[-1], 100.0)
        self.assertAlmostEqual(rsi(down, 14)[-1], 0.0)

    def test_atr_on_constant_range(self):
        data = bars([(100, 102, 98, 100)] * 40)
        self.assertAlmostEqual(atr(data, 14)[-1], 4.0)


class TestMetrics(unittest.TestCase):
    def test_max_drawdown(self):
        pct, abs_ = max_drawdown([100, 120, 60, 80])
        self.assertAlmostEqual(pct, 50.0)
        self.assertAlmostEqual(abs_, 60.0)

    def test_expectancy_of_a_known_set(self):
        # Two +1R wins and two -1R losses must average to 0.
        data = bars([(100, 100, 100, 100)] * 5)
        r = Backtest(data, BuyOnBar(at=99), starting_equity=1000).run()
        s = analyse(r)
        self.assertEqual(s.trades, 0)
        self.assertEqual(s.expectancy_r, 0.0)


class TestSimulation(unittest.TestCase):
    def test_seed_is_reproducible(self):
        from fxglitch.simulate import preset
        a = preset("V75", bars=200, seed=7)
        b = preset("V75", bars=200, seed=7)
        self.assertEqual([c.close for c in a], [c.close for c in b])

    def test_boom_spikes_up_not_down(self):
        from fxglitch.simulate import preset
        c = preset("BOOM1000", bars=20000, seed=1)
        rets = [(x.close / x.open - 1) for x in c]
        self.assertGreater(max(rets), 0.005)      # a real spike exists
        self.assertGreater(abs(min(rets)), 0)
        self.assertLess(abs(min(rets)), max(rets))  # downside is far smaller

    def test_ohlc_is_internally_consistent(self):
        from fxglitch.simulate import preset
        for name in ("V75", "BOOM1000", "CRASH1000", "XAUUSD-SIM"):
            for c in preset(name, bars=500, seed=3):
                self.assertGreaterEqual(c.high, max(c.open, c.close), name)
                self.assertLessEqual(c.low, min(c.open, c.close), name)



class TestCryptoCosts(unittest.TestCase):
    """Percentage fees, the crypto cost model."""

    def test_fee_pct_is_charged_on_both_sides(self):
        data = bars([
            (100, 101, 99, 100),
            (100, 100, 100, 100),
            (100, 100, 100, 100),
        ])
        # 1% per side, on purpose - big enough to check the arithmetic by eye.
        r = Backtest(data, BuyOnBar(at=0, sl=90), starting_equity=1000,
                     fee_pct=1.0).run()
        t = r.trades[0]
        self.assertAlmostEqual(t.entry_price, 101.0)
        self.assertAlmostEqual(t.exit_price, 99.0)

    def test_fee_scales_with_price_but_spread_does_not(self):
        """The whole point of having two cost models."""
        cheap = bars([(100, 100, 100, 100)] * 3)
        dear = bars([(50_000, 50_000, 50_000, 50_000)] * 3)

        for series, price in ((cheap, 100), (dear, 50_000)):
            r = Backtest(series, BuyOnBar(at=0, sl=price * 0.9),
                         starting_equity=1000, fee_pct=0.1).run()
            # 0.1% of price, whatever the price is.
            self.assertAlmostEqual(r.trades[0].entry_price, price * 1.001, places=4)

        r = Backtest(dear, BuyOnBar(at=0, sl=45_000), starting_equity=1000,
                     spread=2.0).run()
        self.assertAlmostEqual(r.trades[0].entry_price, 50_001.0)  # flat, not scaled

    def test_costs_stack(self):
        data = bars([(1000, 1000, 1000, 1000)] * 3)
        r = Backtest(data, BuyOnBar(at=0, sl=900), starting_equity=1000,
                     spread=2.0, fee_pct=0.1, slippage_pct=0.05).run()
        # half spread (1.0) + 0.15% of 1000 (1.5) = 2.5
        self.assertAlmostEqual(r.trades[0].entry_price, 1002.5)

    def test_higher_fees_never_improve_a_result(self):
        from fxglitch.simulate import random_walk
        from strategies.ema_cross import EmaCross
        series = random_walk(bars=3000, start_price=50_000, annual_vol=0.5, seed=11)
        last = None
        for fee in (0.0, 0.05, 0.1, 0.25):
            s = analyse(Backtest(series, EmaCross(), starting_equity=1000,
                                 fee_pct=fee).run())
            if last is not None:
                self.assertLessEqual(s.expectancy_r, last + 1e-9,
                                     f"fee {fee} improved expectancy")
            last = s.expectancy_r


class TestBenchmark(unittest.TestCase):
    def test_buy_and_hold_return(self):
        from fxglitch.report import buy_and_hold
        data = bars([(100, 100, 100, 100), (150, 150, 150, 150), (200, 200, 200, 200)])
        b = buy_and_hold(data, starting_equity=1000)
        self.assertAlmostEqual(b["return_pct"], 100.0)
        self.assertAlmostEqual(b["final"], 2000.0)
        self.assertAlmostEqual(b["max_dd_pct"], 0.0)

    def test_buy_and_hold_drawdown(self):
        from fxglitch.report import buy_and_hold
        data = bars([(100, 100, 100, 100), (50, 50, 50, 50), (100, 100, 100, 100)])
        self.assertAlmostEqual(buy_and_hold(data, 1000)["max_dd_pct"], 50.0)


class TestMarkets(unittest.TestCase):
    def test_every_preset_is_sane(self):
        from fxglitch import markets
        for key, m in markets.MARKETS.items():
            self.assertGreaterEqual(m.fee_pct, 0, key)
            self.assertGreaterEqual(m.slippage_pct, 0, key)
            self.assertLess(m.round_trip_pct, 5.0, f"{key} looks mistyped")

    def test_lookup_is_case_insensitive(self):
        from fxglitch import markets
        self.assertEqual(markets.get("BINANCE-SPOT").fee_pct, 0.10)
        with self.assertRaises(KeyError):
            markets.get("nonsense-exchange")


class TestDataLoading(unittest.TestCase):
    def test_roundtrip_csv(self):
        import tempfile
        from fxglitch.data import load_csv
        from tools.fetch_crypto import write_csv
        rows = [{"time": T0 + timedelta(hours=i), "open": 1.0 + i, "high": 2.0 + i,
                 "low": 0.5 + i, "close": 1.5 + i, "volume": 10.0} for i in range(5)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.csv")
            write_csv(rows, path)
            loaded = load_csv(path)
        self.assertEqual(len(loaded), 5)
        self.assertAlmostEqual(loaded[0].close, 1.5)
        self.assertAlmostEqual(loaded[-1].high, 6.0)

    def test_validate_catches_broken_ohlc(self):
        from tools.fetch_crypto import validate
        rows = [{"time": T0, "open": 10, "high": 5, "low": 1, "close": 8, "volume": 0}]
        self.assertTrue(any("high/low" in p for p in validate(rows)))

if __name__ == "__main__":
    unittest.main()

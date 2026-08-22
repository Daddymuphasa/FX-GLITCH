"""Tests for the screener, the BTC gate, and correlated exposure.

The exposure tests are the ones that matter. Every other limit in this repo
counts tickets; these count the bet. Twenty alt longs pass a twenty-position
limit and a 1%-per-trade limit while being one leveraged BTC bet entered twenty
times, and that arithmetic is how accounts die in a drawdown.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

from fxglitch.data import Candle
from fxglitch.engine import LONG, SHORT
from fxglitch.live import portfolio, regime as regime_mod
from fxglitch.live.portfolio import ExposureLimits, check_exposure, measure
from fxglitch.live.regime import DOWN, NEUTRAL, UP, btc_regime, gate
from fxglitch.live.screener import (
    ScreenRules, Ticker, parse_tickers, screen, summarise,
)
from fxglitch.venues.base import Instrument, Position

NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def instrument(symbol, tradeable=True):
    return Instrument(symbol, symbol[:-4], "USDT", 4, 2, Decimal("0.001"),
                      tradeable=tradeable)


def universe(*symbols):
    return {s: instrument(s) for s in symbols}


def trend(n=120, start=50000.0, step=200.0, end=NOW):
    return [Candle(time=end - DAY * (n - 1 - i), open=start + step * i,
                   high=start + step * i, low=start + step * i,
                   close=start + step * i) for i in range(n)]


class TestTickerParsing(unittest.TestCase):
    def test_reads_the_documented_field_names(self):
        [t] = parse_tickers([{"symbol": "BTCUSDT", "lastPrice": "78000",
                              "priceChangePercent": "2.5", "baseVol": "1000",
                              "quoteVol": "78000000"}])
        self.assertEqual(t.last, 78000.0)
        self.assertEqual(t.change_pct, 2.5)

    def test_survives_a_renamed_field_instead_of_crashing(self):
        # The doc page is behind the same bot check that blocks the client, so
        # the names are inferred. A rename must degrade, not explode.
        [t] = parse_tickers([{"symbol": "BTCUSDT", "close": "78000"}])
        self.assertEqual(t.last, 78000.0)
        self.assertIsNone(t.change_pct)
        self.assertTrue(t.usable)

    def test_a_row_with_no_price_is_not_usable(self):
        [t] = parse_tickers([{"symbol": "WEIRDUSDT", "someOtherField": "1"}])
        self.assertFalse(t.usable)

    def test_rows_without_a_symbol_are_dropped(self):
        self.assertEqual(parse_tickers([{"lastPrice": "1"}]), [])

    def test_turnover_falls_back_to_price_times_base_volume(self):
        [t] = parse_tickers([{"symbol": "X USDT".replace(" ", ""), "lastPrice": "100",
                              "baseVol": "500"}])
        self.assertEqual(t.turnover, 50000.0)


class TestScreen(unittest.TestCase):
    def rows(self):
        return [
            Ticker("BTCUSDT", 78000, 2.0, quote_volume=5_000_000_000),
            Ticker("ETHUSDT", 2400, 3.0, quote_volume=1_000_000_000),
            Ticker("SOLUSDT", 100, 9.0, quote_volume=200_000_000),
            Ticker("DUSTUSDT", 0.01, 40.0, quote_volume=5_000),
            Ticker("HALTUSDT", 1.0, 1.0, quote_volume=900_000_000),
        ]

    def instruments(self):
        u = universe("BTCUSDT", "ETHUSDT", "SOLUSDT", "DUSTUSDT")
        u["HALTUSDT"] = instrument("HALTUSDT", tradeable=False)
        return u

    def test_thin_symbols_are_cut(self):
        got = screen(self.rows(), self.instruments(), ScreenRules(top=10))
        self.assertNotIn("DUSTUSDT", [t.symbol for t in got])

    def test_untradeable_symbols_are_cut_however_liquid(self):
        got = screen(self.rows(), self.instruments(), ScreenRules(top=10))
        self.assertNotIn("HALTUSDT", [t.symbol for t in got])

    def test_btc_is_always_included_and_comes_first(self):
        # The gate needs BTC's bar every cycle even when nothing would trade it.
        rows = [Ticker("BTCUSDT", 78000, 0.0, quote_volume=1.0)]  # below the floor
        rows += [Ticker("ETHUSDT", 2400, 0.0, quote_volume=9_000_000_000)]
        got = screen(rows, universe("BTCUSDT", "ETHUSDT"), ScreenRules(top=5))
        self.assertEqual(got[0].symbol, "BTCUSDT")

    def test_top_n_is_respected_including_the_forced_symbol(self):
        got = screen(self.rows(), self.instruments(), ScreenRules(top=2))
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0].symbol, "BTCUSDT")

    def test_ranking_by_turnover_is_the_default(self):
        got = screen(self.rows(), self.instruments(), ScreenRules(top=3))
        self.assertEqual([t.symbol for t in got], ["BTCUSDT", "ETHUSDT", "SOLUSDT"])

    def test_ranking_by_absolute_change_reorders(self):
        got = screen(self.rows(), self.instruments(),
                     ScreenRules(top=3, rank_by="abs_change"))
        self.assertEqual(got[1].symbol, "SOLUSDT")

    def test_an_unknown_ranking_is_refused(self):
        with self.assertRaises(ValueError):
            screen(self.rows(), self.instruments(), ScreenRules(rank_by="vibes"))

    def test_excluded_symbols_never_appear(self):
        got = screen(self.rows(), self.instruments(),
                     ScreenRules(top=10, exclude=("ETHUSDT",)))
        self.assertNotIn("ETHUSDT", [t.symbol for t in got])

    def test_mass_parse_failure_is_reported_loudly(self):
        # A field rename would otherwise rank every symbol on zeros, which
        # looks like a decision and is a bug.
        broken = [Ticker("AUSDT"), Ticker("BUSDT")]
        self.assertIn("WARNING", summarise(broken, [], ScreenRules()))


class TestRegime(unittest.TestCase):
    def test_a_rising_market_reads_up(self):
        self.assertEqual(btc_regime(trend()).state, UP)

    def test_a_falling_market_reads_down(self):
        self.assertEqual(btc_regime(trend(step=-200.0)).state, DOWN)

    def test_a_flat_market_inside_the_band_reads_neutral(self):
        # The band exists so the gate does not flip on and off as price grazes
        # its own average, changing what the whole portfolio may do.
        flat = [Candle(time=NOW - DAY * (99 - i), open=50000, high=50000,
                       low=50000, close=50000) for i in range(100)]
        self.assertEqual(btc_regime(flat).state, NEUTRAL)

    def test_too_little_history_is_neutral_not_a_guess(self):
        self.assertEqual(btc_regime(trend(n=10)).state, NEUTRAL)

    def test_the_gate_blocks_alt_longs_in_a_downtrend(self):
        down = btc_regime(trend(step=-200.0))
        allowed, why = gate(down, "SOLUSDT", LONG)
        self.assertFalse(allowed)
        self.assertIn("DOWN", why)

    def test_the_gate_allows_alt_shorts_in_a_downtrend(self):
        down = btc_regime(trend(step=-200.0))
        self.assertTrue(gate(down, "SOLUSDT", SHORT)[0])

    def test_btc_is_never_gated_by_its_own_regime(self):
        # Otherwise this module quietly replaces the strategy's entry rule,
        # and the strategy is the part that was measured.
        down = btc_regime(trend(step=-200.0))
        self.assertTrue(gate(down, "BTCUSDT", LONG)[0])

    def test_neutral_permits_both_directions(self):
        neutral = regime_mod.Regime(NEUTRAL)
        self.assertTrue(gate(neutral, "SOLUSDT", LONG)[0])
        self.assertTrue(gate(neutral, "SOLUSDT", SHORT)[0])


class TestExposure(unittest.TestCase):
    def test_alt_positions_count_as_btc_exposure(self):
        e = measure([Position("SOLUSDT", LONG, 100, 100.0),
                     Position("AVAXUSDT", LONG, 200, 50.0)])
        self.assertEqual(e.long_count, 2)
        self.assertEqual(e.long_notional, 20000.0)

    def test_eth_carries_a_higher_beta_than_one(self):
        e = measure([Position("ETHUSDT", LONG, 1, 1000.0)])
        self.assertEqual(e.long_notional, 1100.0)

    def test_the_twenty_alt_longs_case_is_refused(self):
        # The whole reason this module exists. Three tickets, each individually
        # modest, together a single leveraged BTC bet.
        held = [Position(f"ALT{i}USDT", LONG, 100, 50.0) for i in range(3)]
        v = check_exposure(measure(held), "SOLUSDT", LONG, 5000.0, 10000.0,
                           ExposureLimits())
        self.assertFalse(v.ok)
        self.assertIn("one BTC bet", v.reason)

    def test_directional_exposure_is_capped_as_a_percent_of_equity(self):
        held = [Position("ETHUSDT", LONG, 1, 5000.0)]
        v = check_exposure(measure(held), "SOLUSDT", LONG, 3000.0, 10000.0,
                           ExposureLimits(max_same_direction=9))
        self.assertFalse(v.ok)
        self.assertIn("over the 60% limit", v.reason)

    def test_a_modest_addition_is_allowed(self):
        held = [Position("ETHUSDT", LONG, 1, 1000.0)]
        self.assertTrue(check_exposure(measure(held), "SOLUSDT", LONG, 2000.0,
                                       10000.0, ExposureLimits()))

    def test_opposing_positions_are_not_netted_to_zero(self):
        # A long and a short are not flat - they are a bet on the spread, which
        # is a different strategy that nothing here has measured. Netting would
        # report zero risk for a position that can certainly lose.
        e = measure([Position("ETHUSDT", LONG, 1, 5000.0),
                     Position("SOLUSDT", SHORT, 100, 50.0)])
        self.assertEqual(e.net, 500.0)
        self.assertEqual(e.gross, 10500.0)

    def test_gross_exposure_has_its_own_ceiling(self):
        held = [Position("ETHUSDT", LONG, 1, 4000.0),
                Position("SOLUSDT", SHORT, 100, 50.0)]
        v = check_exposure(measure(held), "AVAXUSDT", SHORT, 4000.0, 10000.0,
                           ExposureLimits(max_directional_pct=200.0))
        self.assertFalse(v.ok)
        self.assertIn("gross", v.reason)

    def test_shorts_and_longs_are_counted_separately(self):
        # Three shorts at 1,000 each: 30% gross on 10,000 equity, so nothing
        # else is binding and the direction count is what is being tested.
        held = [Position(f"A{i}USDT", SHORT, 100, 10.0) for i in range(3)]
        self.assertTrue(check_exposure(measure(held), "SOLUSDT", LONG, 1000.0,
                                       10000.0, ExposureLimits()))
        self.assertFalse(check_exposure(measure(held), "SOLUSDT", SHORT, 1000.0,
                                        10000.0, ExposureLimits()))

    def test_describe_reads_as_a_percentage_of_equity(self):
        e = measure([Position("ETHUSDT", LONG, 1, 1000.0)])
        self.assertIn("11%", portfolio.describe(e, 10000.0))
        self.assertEqual(portfolio.describe(measure([]), 1000.0), "exposure: flat")


if __name__ == "__main__":
    unittest.main()

"""Tests for the live layer.

These cover the failure modes that only exist once a process is running, and
that a backtest can never surface:

  - restarting and acting on the same bar twice
  - a stale feed that keeps serving the last bar it saw
  - a stop that moves against the position
  - a guard tripping and then being cleared by the restart it caused
  - dry-run sending something

The strategy-parity test is the one that matters most: it asserts the runner
drives the same class the backtester drives, because that equivalence is the
only reason any backtest result says anything about the live process.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.data import Candle
from fxglitch.engine import LONG, SHORT, Strategy, Trade
from fxglitch.live import guards
from fxglitch.live.guards import Limits
from fxglitch.live.runner import Runner, position_as_trade
from fxglitch.live.state import State, client_id
from fxglitch.venues.base import (
    Balance, Instrument, OrderResult, Position, Venue, VenueError,
)

NOW = datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)

BTC = Instrument("BTCUSDT", "BTC", "USDT", qty_precision=4, price_precision=1,
                 min_qty=Decimal("0.0001"), max_qty=Decimal("50000"),
                 max_leverage=125)


def candles(n=300, end=NOW, start_price=60000.0, rate=0.003, bar_range=0.025):
    """A clean trend with a REALISTIC range-to-price ratio.

    The ratio is the part that matters, and getting it wrong was quietly
    breaking these tests. An earlier fixture moved 100 points a bar on a
    60,000 price - a 0.1% daily range, roughly a twentieth of what crypto
    actually does. ATR came out tiny, so an ATR-based stop sat a hair from the
    entry, so risk-based sizing bought an enormous position to put 1% at risk,
    and the exposure limits refused it. The limits were right; the fixture was
    fantasy.

    Each close prints a new extreme so breakout rules trigger, while the bar's
    range stays near 2.5% of price, which is ordinary for daily BTC.
    """
    out, price = [], start_price
    for i in range(n):
        rising = rate >= 0
        out.append(Candle(
            time=end - DAY * (n - 1 - i),
            open=price / (1 + rate),
            high=price if rising else price * (1 + bar_range),
            low=price * (1 - bar_range) if rising else price,
            close=price, volume=100.0))
        price *= (1 + rate)
    return out


class FakeVenue(Venue):
    name = "fake"

    def __init__(self, *, bars=None, positions=None, balance=None, stops=None):
        self.bars = bars if bars is not None else candles()
        self._positions = positions or []
        self._balance = balance or Balance("USDT", available=10000.0, used=0.0)
        self._stops = stops or {}
        self.placed = []
        self.closed = []
        self.stop_moves = []

    def instruments(self):
        alts = {s: Instrument(s, s[:-4], "USDT", 3, 2, Decimal("0.001"),
                              max_leverage=100)
                for s in ("ETHUSDT", "SOLUSDT", "A0USDT", "A1USDT", "A2USDT")}
        return {"BTCUSDT": BTC, **alts}

    def candles(self, symbol, interval="1d", limit=200):
        return self.bars[-limit:]

    def balance(self):
        return self._balance

    def positions(self, symbol=None):
        return [p for p in self._positions if symbol in (None, p.symbol)]

    def stops(self):
        return dict(self._stops)

    def place(self, order):
        self.placed.append(order)
        return OrderResult(True, venue_order_id="oid1", client_id=order.client_id)

    def close(self, position, *, reason=""):
        self.closed.append((position, reason))
        return OrderResult(True, venue_order_id="oid2")

    def set_stop(self, position, stop_price):
        self.stop_moves.append((position.symbol, float(stop_price)))
        return OrderResult(True)


class AlwaysBuy(Strategy):
    """Long with a stop `gap` below, trailed. The gap is a PERCENT of price.

    Percent rather than points, for the same reason the candle fixture is
    proportional: a fixed-point stop means something completely different at
    60,000 than at 600, and a test whose meaning depends on the price level is
    a test that will mislead somebody later.
    """
    name = "always buy"

    def __init__(self, gap: float = 0.05):
        self.params = {"gap": gap}
        self.gap = gap

    def prepare(self):
        self.prepared = True

    def stop_for(self, price):
        return price * (1 - self.gap)

    def on_bar(self, i):
        price = self.candles[i].close
        if self.position is not None:
            trailed = self.stop_for(price)
            if self.position.sl is None or trailed > self.position.sl:
                self.position.sl = trailed
            return
        self.buy(sl=self.stop_for(price), reason="test entry")


class AlwaysClose(Strategy):
    name = "always close"

    def __init__(self):
        self.params = {}

    def on_bar(self, i):
        if self.position is not None:
            self.close(reason="test exit")


class Quiet(Strategy):
    name = "quiet"

    def __init__(self):
        self.params = {}

    def on_bar(self, i):
        pass


def runner(venue, strategy=AlwaysBuy, live=False, limits=None, state=None, **kw):
    return Runner(venue, strategy, ["BTCUSDT"], interval="1d",
                  limits=limits or Limits(), state=state or State(),
                  live=live, **kw)


class TestClientIds(unittest.TestCase):
    def test_same_decision_yields_the_same_id(self):
        # The crash-safety property: a retry after a crash must compute the id
        # that may already have been filled, so the venue can reject it.
        a = client_id("BTCUSDT", NOW, "enter")
        b = client_id("BTCUSDT", NOW, "enter")
        self.assertEqual(a, b)

    def test_different_bars_and_intents_differ(self):
        self.assertNotEqual(client_id("BTCUSDT", NOW, "enter"),
                            client_id("BTCUSDT", NOW + DAY, "enter"))
        self.assertNotEqual(client_id("BTCUSDT", NOW, "enter"),
                            client_id("BTCUSDT", NOW, "exit"))
        self.assertNotEqual(client_id("BTCUSDT", NOW, "enter"),
                            client_id("ETHUSDT", NOW, "enter"))

    def test_enter_and_exit_do_not_collide(self):
        # Both start with 'e'. Truncating to one character makes a close carry
        # its own entry's id, so the venue rejects it as a duplicate and the
        # position stays open while the log claims otherwise.
        self.assertNotEqual(client_id("BTCUSDT", NOW, "enter"),
                            client_id("BTCUSDT", NOW, "exit"))

    def test_an_unregistered_action_is_refused(self):
        with self.assertRaises(ValueError):
            client_id("BTCUSDT", NOW, "scale-in")

    def test_the_id_carries_no_wall_clock(self):
        # If it did, a retry would look like a new order and fill twice.
        import time
        a = client_id("BTCUSDT", NOW, "enter")
        time.sleep(0.01)
        self.assertEqual(a, client_id("BTCUSDT", NOW, "enter"))


class TestState(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "state.json")

    def test_a_decided_bar_survives_a_restart(self):
        s = State.load(self.path)
        s.mark_decided("BTCUSDT", NOW)
        s.save()
        self.assertTrue(State.load(self.path).already_decided("BTCUSDT", NOW))

    def test_an_older_bar_still_counts_as_decided(self):
        s = State()
        s.mark_decided("BTCUSDT", NOW)
        self.assertTrue(s.already_decided("BTCUSDT", NOW - DAY))
        self.assertFalse(s.already_decided("BTCUSDT", NOW + DAY))

    def test_a_halt_survives_a_restart(self):
        # A guard that is cleared by the restart it caused is not a guard.
        s = State.load(self.path)
        s.halt("daily loss: -7%")
        self.assertTrue(State.load(self.path).halted)

    def test_a_corrupt_state_file_halts_rather_than_resetting(self):
        # Resetting would re-enable trading on bars already acted on.
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        loaded = State.load(self.path)
        self.assertTrue(loaded.halted)
        self.assertIn("unreadable", loaded.halt_reason)

    def test_a_new_day_clears_only_the_daily_loss_halt(self):
        s = State()
        s.roll_day(1000.0, NOW)
        s.halt("daily loss: -7%")
        s.roll_day(900.0, NOW + DAY)
        self.assertFalse(s.halted)

        s2 = State()
        s2.roll_day(1000.0, NOW)
        s2.halt("state file is unreadable")
        s2.roll_day(900.0, NOW + DAY)
        self.assertTrue(s2.halted, "a non-daily halt must not expire overnight")

    def test_the_day_anchor_is_set_once_per_day(self):
        s = State()
        self.assertTrue(s.roll_day(1000.0, NOW))
        self.assertFalse(s.roll_day(500.0, NOW + timedelta(hours=6)))
        self.assertEqual(s.day_open_equity, 1000.0)

    def test_saving_never_leaves_a_partial_file(self):
        s = State.load(self.path)
        s.mark_decided("BTCUSDT", NOW)
        s.save()
        s.mark_decided("ETHUSDT", NOW)
        s.save()
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["decided"]), 2)


class TestGuards(unittest.TestCase):
    def test_stale_data_is_refused(self):
        old = NOW - timedelta(days=5)
        self.assertFalse(guards.check_data_fresh(old, 86400, Limits(), NOW))
        self.assertTrue(guards.check_data_fresh(NOW - timedelta(hours=1),
                                                86400, Limits(), NOW))

    def test_daily_loss_is_fatal_not_a_skip(self):
        v = guards.check_daily_loss(Balance("USDT", 930.0, 0.0), 1000.0, Limits())
        self.assertFalse(v.ok)
        self.assertTrue(v.fatal)

    def test_an_inverted_stop_is_caught(self):
        # A long stop above entry is an instant loss, and is exactly what a
        # flipped sign produces.
        self.assertFalse(guards.check_stop(61000, 60000, LONG, Limits()))
        self.assertFalse(guards.check_stop(59000, 60000, SHORT, Limits()))
        self.assertTrue(guards.check_stop(59000, 60000, LONG, Limits()))

    def test_a_missing_stop_is_refused_by_default(self):
        self.assertFalse(guards.check_stop(None, 60000, LONG, Limits()))
        self.assertTrue(guards.check_stop(None, 60000, LONG,
                                          Limits(require_stop=False)))

    def test_position_limit_and_duplicate_symbol(self):
        held = [Position("BTCUSDT", LONG, 1, 60000)]
        self.assertFalse(guards.check_capacity(held, "BTCUSDT", Limits()))
        self.assertTrue(guards.check_capacity(held, "ETHUSDT", Limits()))
        full = [Position(f"S{i}USDT", LONG, 1, 1) for i in range(3)]
        self.assertFalse(guards.check_capacity(full, "ETHUSDT", Limits()))

    def test_the_free_balance_reserve_is_enforced(self):
        b = Balance("USDT", available=1000.0, used=0.0)
        # 4000 notional at 5x = 800 margin, leaving 200 = exactly the 20% floor
        self.assertTrue(guards.check_balance(b, 4000.0, 5, Limits()))
        self.assertFalse(guards.check_balance(b, 4600.0, 5, Limits()))

    def test_oversized_risk_is_refused(self):
        b = Balance("USDT", available=1000.0, used=0.0)
        self.assertFalse(guards.check_risk(50.0, b, Limits()))
        self.assertTrue(guards.check_risk(10.0, b, Limits()))

    def test_leverage_ceiling(self):
        b = Balance("USDT", available=100000.0, used=0.0)
        self.assertFalse(guards.check_balance(b, 1000.0, 50, Limits()))


class TestStrategyParity(unittest.TestCase):
    def test_the_runner_drives_the_real_strategy_class(self):
        # Not a reimplementation, not a subclass. If this ever stops being
        # true, no backtest in the repo says anything about the live process.
        from strategies.donchian_breakout import DonchianBreakout
        v = FakeVenue()
        r = Runner(v, DonchianBreakout, ["BTCUSDT"], interval="1d",
                   state=State(), params={"entry": 20})
        strategy = r._drive("BTCUSDT", v.bars, None, None)
        self.assertIsInstance(strategy, DonchianBreakout)
        self.assertTrue(hasattr(strategy, "hi_entry"), "prepare() did not run")

    def test_a_venue_position_reaches_the_strategy_as_an_engine_trade(self):
        p = Position("BTCUSDT", SHORT, 0.5, 61000.0, venue_id="p1")
        t = position_as_trade(p, 62000.0)
        self.assertIsInstance(t, Trade)
        self.assertEqual(t.direction, SHORT)
        self.assertEqual(t.sl, 62000.0)
        self.assertEqual(t.size, 0.5)

    def test_a_donchian_breakout_produces_an_order_live(self):
        from strategies.donchian_breakout import DonchianBreakout
        v = FakeVenue()
        r = Runner(v, DonchianBreakout, ["BTCUSDT"], interval="1d",
                   state=State(), params={"entry": 20, "trend": 50}, live=True)
        [d] = r.cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "enter", d.detail)
        self.assertEqual(len(v.placed), 1)


class TestEntries(unittest.TestCase):
    def test_dry_run_computes_everything_and_sends_nothing(self):
        v = FakeVenue()
        [d] = runner(v, live=False).cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "enter")
        self.assertIn("[dry-run]", d.detail)
        self.assertFalse(d.sent)
        self.assertEqual(v.placed, [])

    def test_live_sends_the_order_with_its_stop_attached(self):
        v = FakeVenue()
        [d] = runner(v, live=True).cycle(now=NOW + timedelta(hours=1))
        self.assertTrue(d.sent)
        order = v.placed[0]
        self.assertEqual(order.direction, LONG)
        self.assertIsNotNone(order.stop_price)
        self.assertTrue(order.client_id)

    def test_size_comes_from_risk_divided_by_stop_distance(self):
        # 10,000 equity at 1% = 100 risked. A 5% stop on the last close means
        # size = 100 / (price * 0.05), rounded down to the venue's step.
        v = FakeVenue()
        runner(v, live=True).cycle(now=NOW + timedelta(hours=1))
        price = v.bars[-1].close
        expected = BTC.round_qty(100.0 / (price * 0.05))
        self.assertEqual(v.placed[0].qty, expected)

    def test_a_bar_is_never_acted_on_twice(self):
        v = FakeVenue()
        state = State()
        r = runner(v, live=True, state=state)
        r.cycle(now=NOW + timedelta(hours=1))
        r.cycle(now=NOW + timedelta(hours=2))
        self.assertEqual(len(v.placed), 1)

    def test_a_restart_does_not_reopen_the_same_bar(self):
        # The crash case: state persisted, process died, new Runner, same bar.
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "s.json")
        v = FakeVenue()
        runner(v, live=True, state=State.load(path)).cycle(now=NOW + timedelta(hours=1))
        runner(v, live=True, state=State.load(path)).cycle(now=NOW + timedelta(hours=2))
        self.assertEqual(len(v.placed), 1)

    def test_no_entry_while_already_holding_the_symbol(self):
        v = FakeVenue(positions=[Position("BTCUSDT", LONG, 0.1, 60000, venue_id="p1")])
        [d] = runner(v, live=True).cycle(now=NOW + timedelta(hours=1))
        self.assertNotEqual(d.action, "enter")
        self.assertEqual(v.placed, [])

    def test_a_refusal_does_not_burn_the_bar(self):
        # Out of margin now is a fact about now; the next cycle may differ.
        v = FakeVenue(balance=Balance("USDT", available=1.0, used=0.0))
        state = State()
        [d] = runner(v, live=True, state=state).cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "blocked")
        self.assertFalse(state.already_decided("BTCUSDT", v.bars[-1].time))

    def test_dust_sized_orders_are_refused_rather_than_sent(self):
        v = FakeVenue(balance=Balance("USDT", available=0.05, used=0.0))
        [d] = runner(v, live=True).cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "blocked")
        self.assertEqual(v.placed, [])


class TestExitsAndTrailing(unittest.TestCase):
    def test_a_close_request_closes_the_position(self):
        v = FakeVenue(positions=[Position("BTCUSDT", LONG, 0.1, 60000, venue_id="p1")])
        [d] = runner(v, AlwaysClose, live=True).cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "exit")
        self.assertEqual(len(v.closed), 1)

    def test_a_trailed_stop_is_pushed_to_the_exchange(self):
        held = Position("BTCUSDT", LONG, 0.1, 60000, venue_id="p1")
        last = candles()[-1].close
        v = FakeVenue(positions=[held], stops={"p1": last * 0.90})
        [d] = runner(v, AlwaysBuy, live=True).cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "trail")
        self.assertEqual(v.stop_moves, [("BTCUSDT", last * 0.95)])

    def test_an_unchanged_stop_is_not_resent_every_cycle(self):
        held = Position("BTCUSDT", LONG, 0.1, 60000, venue_id="p1")
        last = candles()[-1].close
        v = FakeVenue(positions=[held], stops={"p1": last * 0.95})
        [d] = runner(v, AlwaysBuy, live=True).cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "none")
        self.assertEqual(v.stop_moves, [])

    def test_a_stop_moving_against_the_position_is_refused(self):
        # Converts a bounded loss into an unbounded one. No other guard
        # would catch it.
        class LoosensStop(Strategy):
            name = "loosens"
            def __init__(self): self.params = {}
            def on_bar(self, i):
                if self.position is not None:
                    self.position.sl = self.candles[i].close * 0.5

        held = Position("BTCUSDT", LONG, 0.1, 60000, venue_id="p1")
        v = FakeVenue(positions=[held],
                      stops={"p1": candles()[-1].close * 0.95})
        [d] = runner(v, LoosensStop, live=True).cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "blocked")
        self.assertIn("against the position", d.detail)
        self.assertEqual(v.stop_moves, [])


class TestSessionGuards(unittest.TestCase):
    def test_a_stale_feed_blocks_trading(self):
        v = FakeVenue(bars=candles(end=NOW - timedelta(days=10)))
        [d] = runner(v, live=True).cycle(now=NOW)
        self.assertEqual(d.action, "blocked")
        self.assertIn("stale", d.detail)
        self.assertEqual(v.placed, [])

    def test_breaching_the_daily_loss_halts_the_session(self):
        state = State()
        state.roll_day(10000.0, NOW)
        v = FakeVenue(balance=Balance("USDT", available=9000.0, used=0.0))
        r = runner(v, live=True, state=state)
        [d] = r.cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "blocked")
        self.assertTrue(r.state.halted)
        self.assertEqual(v.placed, [])

    def test_a_halted_session_does_nothing_at_all(self):
        state = State()
        state.halted = True
        state.halt_reason = "manual"
        v = FakeVenue()
        [d] = runner(v, live=True, state=state).cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "blocked")
        self.assertEqual(v.placed, [])

    def test_one_bad_symbol_does_not_stop_the_scan(self):
        class Flaky(FakeVenue):
            def candles(self, symbol, interval="1d", limit=200):
                if symbol == "ETHUSDT":
                    raise VenueError("fake", "delisted", "gone")
                return self.bars[-limit:]

        v = Flaky()
        r = Runner(v, AlwaysBuy, ["ETHUSDT", "BTCUSDT"], interval="1d",
                   state=State(), live=True)
        results = r.cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(len(results), 2)
        self.assertEqual(len(v.placed), 1, "the healthy symbol was skipped")

    def test_quiet_strategies_report_no_signal(self):
        v = FakeVenue()
        [d] = runner(v, Quiet, live=True).cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "none")
        self.assertEqual(v.placed, [])


if __name__ == "__main__":
    unittest.main()


class TestScreenAndGateInTheRunner(unittest.TestCase):
    """The three pieces wired together: screen wide, gate on BTC, cap the bet."""

    def test_screening_replaces_the_symbol_list(self):
        from fxglitch.live.screener import ScreenRules, Ticker

        class Screenable(FakeVenue):
            def tickers(self, symbols=None):
                return [Ticker("BTCUSDT", 78000, 1.0, quote_volume=5e9),
                        Ticker("ETHUSDT", 2400, 1.0, quote_volume=1e9),
                        Ticker("DUSTUSDT", 0.01, 1.0, quote_volume=5_000)]

        v = Screenable()
        r = Runner(v, Quiet, ["IGNORED"], interval="1d", state=State(),
                   screen_rules=ScreenRules(top=5), use_gate=False)
        got = r.cycle(now=NOW + timedelta(hours=1))
        symbols = [d.symbol for d in got]
        self.assertIn("BTCUSDT", symbols)
        self.assertNotIn("DUSTUSDT", symbols)
        self.assertNotIn("IGNORED", symbols)

    def test_a_failed_screen_falls_back_rather_than_trading_everything(self):
        from fxglitch.live.screener import ScreenRules

        class Broken(FakeVenue):
            def tickers(self, symbols=None):
                raise VenueError("fake", "cloudflare", "blocked")

        v = Broken()
        r = Runner(v, Quiet, ["BTCUSDT"], interval="1d", state=State(),
                   screen_rules=ScreenRules(), use_gate=False)
        self.assertEqual([d.symbol for d in r.cycle(now=NOW + timedelta(hours=1))],
                         ["BTCUSDT"])

    def test_the_gate_blocks_an_alt_long_while_btc_falls(self):
        class PerSymbol(FakeVenue):
            def candles(self, symbol, interval="1d", limit=200):
                # BTC falling, the alt rising - exactly the trap the gate is for.
                bars = (candles(n=300, start_price=90000.0, rate=-0.003)
                        if symbol == "BTCUSDT" else candles())
                return bars[-limit:]

        v = PerSymbol()
        r = Runner(v, AlwaysBuy, ["SOLUSDT"], interval="1d", state=State(),
                   live=True, use_gate=True)
        [d] = r.cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "blocked")
        self.assertIn("BTC regime is DOWN", d.detail)
        self.assertEqual(v.placed, [])

    def test_the_same_alt_long_is_allowed_when_btc_rises(self):
        v = FakeVenue()          # everything rising, BTC included
        r = Runner(v, AlwaysBuy, ["SOLUSDT"], interval="1d", state=State(),
                   live=True, use_gate=True)
        [d] = r.cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "enter", d.detail)

    def test_btc_itself_is_not_gated_by_its_own_downtrend(self):
        v = FakeVenue(bars=candles(n=300, start_price=90000.0, rate=-0.003))
        r = Runner(v, AlwaysBuy, ["BTCUSDT"], interval="1d", state=State(),
                   live=True, use_gate=True)
        [d] = r.cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "enter", d.detail)

    def test_an_unreadable_driver_fails_closed_not_open(self):
        # NEUTRAL allows both directions, so a failure to read BTC must not
        # quietly disable the gate exactly when it matters.
        class NoBtc(FakeVenue):
            def candles(self, symbol, interval="1d", limit=200):
                if symbol == "BTCUSDT":
                    raise VenueError("fake", "network", "down")
                return self.bars[-limit:]

        v = NoBtc()
        r = Runner(v, AlwaysBuy, ["SOLUSDT"], interval="1d", state=State(),
                   live=True, use_gate=True)
        [d] = r.cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "blocked")
        self.assertEqual(v.placed, [])

    def test_correlated_exposure_stops_the_fourth_alt_long(self):
        from fxglitch.live.portfolio import ExposureLimits
        held = [Position(f"A{i}USDT", LONG, 1.0, 100.0, venue_id=f"p{i}")
                for i in range(3)]
        v = FakeVenue(positions=held)
        r = Runner(v, AlwaysBuy, ["SOLUSDT"], interval="1d", state=State(),
                   live=True, use_gate=False,
                   limits=Limits(max_positions=99),
                   exposure_limits=ExposureLimits(max_same_direction=3))
        [d] = r.cycle(now=NOW + timedelta(hours=1))
        self.assertEqual(d.action, "blocked")
        self.assertIn("one BTC bet", d.detail)
        self.assertEqual(v.placed, [])

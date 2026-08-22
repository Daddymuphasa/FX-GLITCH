"""Tests for walk-forward validation.

The mechanics being tested here are the ones that, if wrong, would leak the
future into the result and make every walk-forward number in the repo a lie:

  - no test window may overlap another, and none may start before its own
    training data ends
  - warm-up trades must be discarded, not counted
  - discarding them must not leave the survivors sized off an equity path that
    includes the discarded results
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.engine import BacktestResult, LONG, SHORT, Trade
from fxglitch.metrics import analyse
from fxglitch.walkforward import combine, drift, make_folds, trim_result

T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def trade(day: int, r: float, risk: float = 10.0, direction: int = LONG) -> Trade:
    """A closed trade that lands exactly `r` R on an account risking `risk`."""
    entry = 100.0
    size = risk / 1.0                      # 1.0 of price movement = 1R
    return Trade(
        direction=direction,
        entry_time=T0 + day * DAY,
        entry_price=entry,
        size=size,
        sl=entry - direction,
        tp=None,
        exit_time=T0 + (day + 1) * DAY,
        exit_price=entry + r * direction,
        exit_reason="test",
        risk_amount=risk,
    )


def result(trades) -> BacktestResult:
    return BacktestResult(
        strategy="t", symbol="t", trades=list(trades),
        equity_curve=[1000.0], times=[T0], starting_equity=1000.0,
        params={"entry": 55},
    )


class TestFolds(unittest.TestCase):
    def test_windows_do_not_overlap_and_cover_the_tail(self):
        folds = make_folds(1000, folds=5, min_train_frac=0.4)
        self.assertEqual(len(folds), 5)
        self.assertEqual(folds[0].test_start, 400)
        self.assertEqual(folds[-1].test_end, 1000)
        for a, b in zip(folds, folds[1:]):
            self.assertEqual(a.test_end, b.test_start)

    def test_no_fold_tests_on_its_own_training_data(self):
        for rolling in (False, True):
            for f in make_folds(1000, folds=4, rolling=rolling):
                self.assertLessEqual(f.train_end, f.test_start)
                self.assertLess(f.train_start, f.train_end)

    def test_anchored_training_grows_rolling_does_not(self):
        anchored = make_folds(1000, folds=4, min_train_frac=0.4)
        rolling = make_folds(1000, folds=4, min_train_frac=0.4, rolling=True)
        self.assertEqual([f.train_start for f in anchored], [0, 0, 0, 0])
        self.assertEqual(
            [f.train_bars for f in anchored],
            sorted(f.train_bars for f in anchored),
        )
        # A rolling window is a fixed length once it is past the start.
        self.assertEqual({f.train_bars for f in rolling[1:]}, {400})

    def test_warmup_reaches_back_before_the_window_and_clamps_at_zero(self):
        f = make_folds(1000, folds=2, min_train_frac=0.4, warmup=250)[0]
        self.assertEqual(f.warmup_start, 150)
        self.assertLess(f.warmup_start, f.test_start)
        early = make_folds(1000, folds=2, min_train_frac=0.1, warmup=250)[0]
        self.assertEqual(early.warmup_start, 0)

    def test_refuses_a_split_that_does_not_fit(self):
        with self.assertRaises(ValueError):
            make_folds(50, folds=40)
        with self.assertRaises(ValueError):
            make_folds(1000, folds=0)
        with self.assertRaises(ValueError):
            make_folds(1000, min_train_frac=1.0)


class TestTrim(unittest.TestCase):
    def test_warmup_trades_are_dropped(self):
        r = trim_result(result([trade(1, 3.0), trade(9, -1.0), trade(11, 2.0)]),
                        T0 + 10 * DAY, starting_equity=1000.0, risk_pct=1.0)
        self.assertEqual(len(r.trades), 1)
        self.assertEqual(r.trades[0].entry_time, T0 + 11 * DAY)

    def test_a_trade_exactly_on_the_boundary_is_kept(self):
        r = trim_result(result([trade(10, 1.0)]), T0 + 10 * DAY,
                        starting_equity=1000.0, risk_pct=1.0)
        self.assertEqual(len(r.trades), 1)

    def test_survivors_are_resized_off_the_folds_own_equity(self):
        # Same three trades, but the first two are warm-up. The kept trade must
        # risk 1% of 1000 - not 1% of whatever the warm-up left behind.
        r = trim_result(result([trade(1, 5.0), trade(2, 5.0), trade(20, 2.0)]),
                        T0 + 10 * DAY, starting_equity=1000.0, risk_pct=1.0)
        self.assertAlmostEqual(r.trades[0].risk_amount, 10.0)
        self.assertAlmostEqual(r.final_equity, 1020.0)

    def test_resizing_does_not_touch_the_r_multiple(self):
        # Cash changes with account size; R does not. If this ever fails, the
        # rescale is distorting results rather than just restating them.
        original = [trade(20, 1.5), trade(21, -1.0), trade(22, 3.0)]
        r = trim_result(result(original), T0, starting_equity=250.0, risk_pct=2.0)
        self.assertEqual([round(t.r_multiple, 9) for t in r.trades],
                         [1.5, -1.0, 3.0])
        self.assertAlmostEqual(analyse(r).expectancy_r, (1.5 - 1.0 + 3.0) / 3)

    def test_compounding_is_applied_in_order(self):
        r = trim_result(result([trade(1, 2.0), trade(2, 2.0)]), T0,
                        starting_equity=1000.0, risk_pct=10.0)
        # +2R at 10% risk is +20% of the balance at the time, twice over.
        self.assertAlmostEqual(r.final_equity, 1000.0 * 1.2 * 1.2)

    def test_a_wipeout_stops_the_replay(self):
        r = trim_result(result([trade(1, -1.0), trade(2, 5.0)]), T0,
                        starting_equity=1000.0, risk_pct=100.0)
        self.assertEqual(len(r.trades), 1)
        self.assertLessEqual(r.final_equity, 0.0)

    def test_empty_window_reports_the_starting_balance_not_a_crash(self):
        r = trim_result(result([trade(1, 3.0)]), T0 + 99 * DAY,
                        starting_equity=1000.0, risk_pct=1.0)
        self.assertEqual(r.trades, [])
        self.assertEqual(r.final_equity, 1000.0)
        self.assertEqual(analyse(r).trades, 0)

    def test_open_trades_are_not_counted(self):
        still_open = trade(20, 1.0)
        still_open.exit_price = None
        r = trim_result(result([still_open]), T0,
                        starting_equity=1000.0, risk_pct=1.0)
        self.assertEqual(r.trades, [])


class TestCombine(unittest.TestCase):
    def test_folds_chain_into_one_compounding_account(self):
        a = trim_result(result([trade(1, 1.0)]), T0, starting_equity=1000.0,
                        risk_pct=10.0)
        b = trim_result(result([trade(5, 1.0)]), T0, starting_equity=1000.0,
                        risk_pct=10.0)
        both = combine([a, b], starting_equity=1000.0, risk_pct=10.0)
        self.assertEqual(len(both.trades), 2)
        # Each fold alone ends at 1100; chained, the second compounds on 1100.
        self.assertAlmostEqual(a.final_equity, 1100.0)
        self.assertAlmostEqual(both.final_equity, 1000.0 * 1.1 * 1.1)

    def test_shorts_survive_the_rescale(self):
        r = trim_result(result([trade(1, 2.0, direction=SHORT)]), T0,
                        starting_equity=1000.0, risk_pct=1.0)
        self.assertAlmostEqual(r.trades[0].r_multiple, 2.0)
        self.assertAlmostEqual(r.final_equity, 1020.0)


class TestDrift(unittest.TestCase):
    def test_stable_pick_is_zero_drift(self):
        self.assertEqual(drift([{"entry": 55}] * 4), 0.0)

    def test_a_new_pick_every_fold_is_total_drift(self):
        self.assertEqual(drift([{"e": 1}, {"e": 2}, {"e": 3}]), 1.0)

    def test_one_change_in_four_handovers(self):
        picks = [{"e": 1}, {"e": 1}, {"e": 2}, {"e": 2}, {"e": 2}]
        self.assertAlmostEqual(drift(picks), 0.25)

    def test_a_single_fold_cannot_drift(self):
        self.assertEqual(drift([{"e": 1}]), 0.0)
        self.assertEqual(drift([]), 0.0)


if __name__ == "__main__":
    unittest.main()

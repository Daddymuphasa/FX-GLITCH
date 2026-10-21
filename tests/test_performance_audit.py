import unittest
from datetime import datetime, timedelta, timezone

from fxglitch.engine import BacktestResult
from tools.performance_audit import weekly_returns


class TestWeeklyReturns(unittest.TestCase):
    def test_partial_edges_excluded_and_weekly_equity_compounds(self):
        monday = datetime(2026, 8, 3, tzinfo=timezone.utc)
        result = BacktestResult("test", "test", [], [100, 140, 126, 300],
                                [monday + timedelta(weeks=i) for i in range(4)], 100)
        returns = weekly_returns(result)
        self.assertEqual(len(returns), 2)
        self.assertAlmostEqual(returns[0], 40)
        self.assertAlmostEqual(returns[1], -10)

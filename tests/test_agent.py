"""Tests for the Agent OS path: positioning, policy, MCP, no TA."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.agent.mcp_server import handle
from fxglitch.agent.policy import ProposedTrade, evaluate
from fxglitch.agent.positioning import Briefing, fetch_briefing, funding_label, oi_quadrant
from fxglitch.agent.session import judge_demo, paper_balance, run_cycle
from fxglitch.derivs import FUNDING_EXTREME
from fxglitch.engine import LONG
from fxglitch.live.guards import Limits
from fxglitch.venues.base import Position


def transport_for(funding="0.0001", change="0.5", oi="1000", oi_hist=None, lsr="1.1"):
    hist = oi_hist or [
        {"sumOpenInterest": "1000"},
        {"sumOpenInterest": "1100"},
    ]

    def transport(method, url):
        if "premiumIndex" in url:
            return {"markPrice": "110000", "lastFundingRate": funding}
        if "ticker/24hr" in url:
            return {"priceChangePercent": change}
        if "openInterestHist" in url:
            return hist
        if "openInterest" in url:
            return {"openInterest": oi}
        if "globalLongShortAccountRatio" in url:
            return [{"longShortRatio": lsr}]
        raise AssertionError(url)

    return transport


class TestPositioningLabels(unittest.TestCase):
    def test_funding_labels_match_derivs_thresholds(self):
        self.assertEqual(funding_label(0.0001), "baseline")
        self.assertEqual(funding_label(FUNDING_EXTREME), "extreme_long")
        self.assertEqual(funding_label(-FUNDING_EXTREME), "extreme_short")

    def test_quadrants_are_the_documented_four(self):
        self.assertEqual(oi_quadrant(2.0, 3.0), "new_longs")
        self.assertEqual(oi_quadrant(2.0, -3.0), "short_squeeze")
        self.assertEqual(oi_quadrant(-2.0, 3.0), "new_shorts")
        self.assertEqual(oi_quadrant(-2.0, -3.0), "long_liquidation")

    def test_fetch_briefing_uses_injected_transport(self):
        b = fetch_briefing("btcusdt", transport=transport_for(funding="0.0012", change="4",
                                                             oi_hist=[{"sumOpenInterest": "100"},
                                                                      {"sumOpenInterest": "90"}]))
        self.assertEqual(b.symbol, "BTCUSDT")
        self.assertEqual(b.funding_label, "extreme_long")
        self.assertTrue(b.veto_long)
        self.assertEqual(b.oi_quadrant, "short_squeeze")
        self.assertFalse(b.veto_short)


class TestPolicy(unittest.TestCase):
    def test_stand_aside_is_always_allowed(self):
        d = evaluate(ProposedTrade("BTCUSDT", "stand_aside", reason="no edge"),
                     balance=paper_balance(), positions=[])
        self.assertTrue(d.allowed)

    def test_open_without_stop_is_refused(self):
        d = evaluate(
            ProposedTrade("DOGEUSDT", "open", "LONG", leverage=20, stop_pct=0,
                          entry=0.12, reason="no stop"),
            balance=paper_balance(), positions=[], limits=Limits(require_stop=True, max_leverage=5),
        )
        self.assertFalse(d.allowed)
        self.assertIn("stop", d.reason.lower())

    def test_crowded_long_vetoes_new_long(self):
        briefing = Briefing(
            "BTCUSDT", "2026-09-06T00:00:00Z", 110000, 0.0012, "extreme_long",
            1, 0, 0, "short_squeeze", 1.8, "euphoric longs",
            veto_long=True, veto_short=False,
        )
        d = evaluate(
            ProposedTrade("BTCUSDT", "open", "LONG", leverage=5, stop_pct=2, entry=110000),
            balance=paper_balance(), positions=[], briefing=briefing,
        )
        self.assertFalse(d.allowed)
        self.assertIn("veto", d.reason.lower())

    def test_clean_open_is_sized_and_allowed_in_dry_run(self):
        d = evaluate(
            ProposedTrade("BTCUSDT", "open", "LONG", leverage=5, risk_pct=1, stop_pct=2,
                          entry=100000, reason="baseline"),
            balance=paper_balance(1000), positions=[],
        )
        self.assertTrue(d.allowed)
        self.assertGreater(d.sized_qty, 0)
        # 1% of 1000 = 10 cash risk, 2% stop = 2000 price, qty = 10/2000 = 0.005
        self.assertAlmostEqual(d.sized_qty, 0.005, places=6)

    def test_fourth_alt_long_is_one_btc_bet(self):
        held = [
            Position("ETHUSDT", LONG, 1, 4000, venue_id="ETHUSDT:LONG"),
            Position("SOLUSDT", LONG, 20, 180, venue_id="SOLUSDT:LONG"),
            Position("DOGEUSDT", LONG, 10000, 0.12, venue_id="DOGEUSDT:LONG"),
        ]
        d = evaluate(
            ProposedTrade("WIFUSDT", "open", "LONG", leverage=5, risk_pct=1, stop_pct=2, entry=2),
            balance=paper_balance(1000), positions=held, limits=Limits(max_positions=10),
        )
        self.assertFalse(d.allowed)
        self.assertIn("btc bet", d.reason.lower())


class TestJudgeDemo(unittest.TestCase):
    def test_four_cases_offline(self):
        cases = judge_demo()
        self.assertEqual(len(cases), 4)
        marks = [c["policy"]["allowed"] for c in cases]
        self.assertEqual(marks, [False, False, False, True])
        self.assertTrue(cases[-1]["dry_run"])


class TestMcp(unittest.TestCase):
    def test_initialize_and_list_tools(self):
        init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "fx-glitch")
        listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in listed["result"]["tools"]}
        self.assertIn("get_positioning", names)
        self.assertIn("check_policy", names)
        self.assertIn("judge_demo", names)

    def test_judge_demo_tool(self):
        reply = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "judge_demo", "arguments": {}}})
        payload = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(len(payload), 4)

    def test_notification_has_no_reply(self):
        self.assertIsNone(handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


class TestCycleUsesInjectedBriefing(unittest.TestCase):
    def test_cycle_does_not_invent_an_entry(self):
        briefing = fetch_briefing("BTCUSDT", transport=transport_for())
        result = run_cycle("BTCUSDT", briefing=briefing, live=False)
        self.assertEqual(result.proposal["action"], "stand_aside")
        self.assertTrue(result.policy["allowed"])
        self.assertTrue(result.dry_run)
        self.assertFalse(result.sent)


if __name__ == "__main__":
    unittest.main()

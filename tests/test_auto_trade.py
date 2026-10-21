import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["FXGLITCH_AUTO_TRADE"] = "1"
os.environ["FXGLITCH_AUTO_SLOTS"] = "2"

from fxglitch.agent import auto_trade


class TestAutoTrade(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old_fills, self.old_log = auto_trade.FILLS, auto_trade.LOG
        auto_trade.FILLS = self.tmp / "fills.json"
        auto_trade.LOG = self.tmp / "log.txt"

    def tearDown(self):
        auto_trade.FILLS, auto_trade.LOG = self.old_fills, self.old_log

    def test_skips_old_posts(self):
        out = auto_trade.maybe_execute(
            "BTCUSDT\nShort\nEntry CMP\nTp 1\nSl 2\nLeverage 5x",
            telegram_id="old-1",
            posted_at="2020-01-01T00:00:00Z",
        )
        self.assertIsNone(out)

    def test_skips_second_time(self):
        auto_trade._save_fills({"abc": {"ok": True}})
        out = auto_trade.maybe_execute("x", telegram_id="abc", posted_at=None)
        self.assertEqual(out["ok"], True)

    def test_duplicate_slot_config_does_not_send_twice(self):
        with patch.dict(os.environ, {"FXGLITCH_AUTO_SLOTS": "2, 2, 1, 2"}):
            self.assertEqual(auto_trade.slots(), [2, 1])

    def test_slots_include_account_four(self):
        with patch.dict(os.environ, {"FXGLITCH_AUTO_SLOTS": "2,3,4"}):
            self.assertEqual(auto_trade.slots(), [2, 3, 4])

    def test_retries_preflight_failure_but_not_unknown_order_result(self):
        signal = "BTCUSDT\nShort\nEntry CMP\nTp 1\nSl 2\nLeverage 5x"
        with patch.object(auto_trade, "_send", side_effect=[
            {"ok": False, "retryable_slots": [2], "sent": [], "errors": [], "blocked_slots": []},
            {"ok": True, "retryable_slots": [], "sent": [{"slot": 2}], "errors": [], "blocked_slots": []},
        ]) as send:
            self.assertFalse(auto_trade.maybe_execute(signal, telegram_id="retry")["ok"])
            self.assertTrue(auto_trade.maybe_execute(signal, telegram_id="retry")["ok"])
            self.assertTrue(auto_trade.maybe_execute(signal, telegram_id="retry")["ok"])
            self.assertEqual(send.call_count, 2)

        auto_trade._save_fills({"unknown": {"ok": False, "retryable": False}})
        with patch.object(auto_trade, "_send") as send:
            self.assertFalse(auto_trade.maybe_execute(signal, telegram_id="unknown")["ok"])
            send.assert_not_called()

    def test_no_live_mark_is_retryable(self):
        from types import SimpleNamespace
        rec = SimpleNamespace(bitunix_symbol="BTCUSDT", binance_symbol="BTCUSDT",
                              mark=None, entry=None, direction="SHORT", stop=2,
                              take_profits=[1])
        with patch.object(auto_trade, "recommend", return_value=rec), \
             patch.object(auto_trade, "fetch_mark", return_value=None):
            result = auto_trade._send("signal", "no-mark", None)
        self.assertFalse(result["ok"])
        self.assertTrue(result["retryable"])

    def test_pending_preflight_survives_restart_and_retries(self):
        signal = "BTCUSDT Short Entry CMP TP 1 SL 2"
        auto_trade._save_fills({
            "pending": {"ok": False, "retryable": True, "raw": signal, "posted_at": None},
            "old": {"ok": False, "retryable": True, "raw": signal,
                    "posted_at": "2020-01-01T00:00:00Z"},
        })
        with patch.object(auto_trade, "_send", return_value={"ok": True, "retryable_slots": [],
                                                              "sent": [{"slot": 2}], "errors": [],
                                                              "blocked_slots": []}) as send:
            auto_trade.retry_pending()
            send.assert_called_once_with(signal, "pending", None, [2])
        self.assertTrue(auto_trade._load_fills()["pending"]["ok"])

    def test_auto_uses_daredevil_risk_plan(self):
        from types import SimpleNamespace
        rec = SimpleNamespace(bitunix_symbol="BTCUSDT", binance_symbol="BTCUSDT",
                              mark=100, entry=100, direction="LONG", stop=90,
                              take_profits=[120])
        plan = {"id": "daredevil", "policy_ok": True, "risk_pct": 8.0,
                "loss_if_sl": 7.95}
        venue = SimpleNamespace(authenticated=True,
                                instruments=lambda: {"BTCUSDT": SimpleNamespace(tradeable=True)},
                                balance=lambda: object(), set_margin_mode=lambda *args: None)
        placed = {"order_id": "order-1", "qty": "0.5", "leverage": 15}
        with patch.object(auto_trade, "recommend", return_value=rec), \
             patch.object(auto_trade, "from_slot", return_value=venue), \
             patch.object(auto_trade, "plan_by_id", return_value=plan) as choose, \
             patch.object(auto_trade, "place_plan", return_value=placed) as submit:
            result = auto_trade._send("BTCUSDT Long entry 100 sl 90 tp 120", "signal-1", None)
        choose.assert_called_once_with(rec, "daredevil")
        submit.assert_called_once_with(venue, rec, plan, request_id="signal-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["sent"][0]["risk_pct"], 8.0)
        self.assertEqual(result["sent"][0]["estimated_stop_loss"], 7.95)

    def test_retries_only_failed_account_after_other_account_fills(self):
        from types import SimpleNamespace
        signal = "BTCUSDT Long entry 100 sl 90 tp 120"
        rec = SimpleNamespace(bitunix_symbol="BTCUSDT", binance_symbol="BTCUSDT",
                              mark=100, entry=100, direction="LONG", stop=90,
                              take_profits=[120])
        plan = {"id": "daredevil", "policy_ok": True, "risk_pct": 8.0,
                "loss_if_sl": 8.0}
        venue = SimpleNamespace(authenticated=True,
                                instruments=lambda: {"BTCUSDT": SimpleNamespace(tradeable=True)},
                                balance=lambda: object(), set_margin_mode=lambda *args: None)
        with patch.dict(os.environ, {"FXGLITCH_AUTO_SLOTS": "2,3"}), \
             patch.object(auto_trade, "recommend", return_value=rec), \
             patch.object(auto_trade, "from_slot", side_effect=lambda slot: venue if slot == 2 or ready[0] else SimpleNamespace(authenticated=False)), \
             patch.object(auto_trade, "plan_by_id", return_value=plan), \
             patch.object(auto_trade, "place_plan", return_value={"order_id": "order", "qty": "1", "leverage": 5}) as submit:
            ready = [False]
            first = auto_trade.maybe_execute(signal, telegram_id="multi")
            self.assertEqual([row["slot"] for row in first["sent"]], [2])
            self.assertEqual(first["retryable_slots"], [3])
            self.assertEqual(submit.call_count, 1)
            ready[0] = True
            second = auto_trade.maybe_execute(signal, telegram_id="multi")
            self.assertEqual([row["slot"] for row in second["sent"]], [2, 3])
            self.assertEqual(submit.call_count, 2)
            self.assertFalse(second["retryable"])
            auto_trade.maybe_execute(signal, telegram_id="multi")
            self.assertEqual(submit.call_count, 2)

    def test_existing_success_is_not_backfilled_to_new_account(self):
        auto_trade._save_fills({"legacy": {"ok": True, "sent": [{"slot": 2}]}})
        with patch.dict(os.environ, {"FXGLITCH_AUTO_SLOTS": "2,3"}), \
             patch.object(auto_trade, "_send") as send:
            result = auto_trade.maybe_execute("BTCUSDT Long entry 100 sl 90 tp 120", telegram_id="legacy")
        self.assertEqual(result["sent"], [{"slot": 2}])
        send.assert_not_called()

    def test_uncertain_order_is_not_resubmitted(self):
        from types import SimpleNamespace
        from fxglitch.venues.base import VenueError
        signal = "BTCUSDT Long entry 100 sl 90 tp 120"
        rec = SimpleNamespace(bitunix_symbol="BTCUSDT", binance_symbol="BTCUSDT",
                              mark=100, entry=100, direction="LONG", stop=90,
                              take_profits=[120])
        venue = SimpleNamespace(authenticated=True,
                                instruments=lambda: {"BTCUSDT": SimpleNamespace(tradeable=True)},
                                balance=lambda: object(), set_margin_mode=lambda *args: None)
        with patch.object(auto_trade, "recommend", return_value=rec), \
             patch.object(auto_trade, "from_slot", return_value=venue), \
             patch.object(auto_trade, "plan_by_id", return_value={"policy_ok": True}), \
             patch.object(auto_trade, "place_plan", side_effect=VenueError("bitunix", "timeout", "timeout")) as submit:
            first = auto_trade.maybe_execute(signal, telegram_id="uncertain")
            second = auto_trade.maybe_execute(signal, telegram_id="uncertain")
        self.assertEqual(first["blocked_slots"], [2])
        self.assertFalse(first["retryable"])
        self.assertEqual(second, first)
        self.assertEqual(submit.call_count, 1)


if __name__ == "__main__":
    unittest.main()

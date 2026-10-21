import os
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from fxglitch.agent import wa_dialog
from fxglitch.venues.base import Balance, Instrument, VenueError


class TestWaDialog(unittest.TestCase):
    def test_plain_language_menu_commands(self):
        session = {"account": 1, "name": "Test", "step": "home"}
        with patch.object(wa_dialog, "format_signals", return_value="AVAILABLE") as signals:
            self.assertEqual(wa_dialog.handle(session, "available trades"), "AVAILABLE")
            signals.assert_called_once()
        with patch.object(wa_dialog, "format_positions", return_value="RUNNING"):
            self.assertEqual(wa_dialog.handle(session, "running trades"), "RUNNING")
        with patch.object(wa_dialog, "format_balance", return_value="BALANCE"):
            self.assertEqual(wa_dialog.handle(session, "account balance"), "BALANCE")

    def setUp(self):
        os.environ["BITUNIX_ACCESS"] = "alpha"
        os.environ["BITUNIX_ACCESS_2"] = "bravo"
        os.environ["BITUNIX_NAME_2"] = "second"

    def test_login_then_list(self):
        session = {}
        rows = [{"id": "1", "direction": "LONG", "bitunix_symbol": "BTCUSDT", "still_good": True, "raw": "BTCUSDT long"}]
        with patch.object(wa_dialog, "_open_signals", return_value=rows):
            reply = wa_dialog.handle(session, "bravo")
        self.assertEqual(session["account"], 2)
        self.assertIn("second", reply)
        self.assertIn("BTCUSDT", reply)

    def test_wrong_code(self):
        session = {}
        reply = wa_dialog.handle(session, "nope")
        self.assertNotIn("account", session)
        self.assertIn("did not match", reply.lower())

    def test_pick_risk_flow(self):
        session = {"account": 2, "name": "second"}
        rows = [{
            "id": "1", "direction": "LONG", "bitunix_symbol": "ETHUSDT",
            "still_good": True, "raw": "ETHUSDT\nLong\nEntry 3000\nTp 3300\nSl 2900\nLeverage 5x",
            "entry": 3000, "stop": 2900,
        }]
        with patch.object(wa_dialog, "_open_signals", return_value=rows), \
             patch.object(wa_dialog, "_live_recommendation", return_value=wa_dialog.recommend(rows[0]["raw"])):
            wa_dialog.handle(session, "1")
            reply = wa_dialog.handle(session, "normal")
        self.assertEqual(session["plan"], "mid")
        self.assertIn("yes", reply.lower())

    def test_execute_uses_selected_row_prices_not_stale_raw(self):
        session = {"account": 2, "name": "second", "equity": 1000}
        rows = [{
            "id": "inj-1",
            "direction": "SHORT",
            "bitunix_symbol": "INJUSDT",
            "binance_symbol": "INJUSDT",
            "still_good": True,
            "raw": "INJUSDT\nShort position\nEntry 109.107\nTp 6.0\nSl 8.5\nLeverage 3x",
            "entry": 7.9,
            "stop": 8.5,
            "take_profit": 6.0,
            "take_profits": [6.0],
        }]
        venue = type("V", (), {})()
        venue.authenticated = True
        venue.account_name = "second"
        venue.balance = lambda: Balance("USDT", available=1000, used=0)
        venue.mark_price = lambda symbol: 7.9
        venue.instruments = lambda: {
            "INJUSDT": Instrument("INJUSDT", "INJ", "USDT", 1, 4, Decimal("0.8"), max_leverage=50)
        }
        placed = {}

        def fake_place_plan(_venue, _rec, plan, **kwargs):
            placed["qty"] = plan["qty"]
            return {"order_id": "ok-1", "symbol": "INJUSDT"}

        with patch.object(wa_dialog, "_open_signals", return_value=rows), \
             patch.object(wa_dialog, "from_slot", return_value=venue), \
             patch.object(wa_dialog, "place_plan", side_effect=fake_place_plan):
            wa_dialog.handle(session, "1")
            wa_dialog.handle(session, "low risk")
            reply = wa_dialog.handle(session, "yes")

        self.assertIn("Sent", reply)
        self.assertGreaterEqual(placed["qty"], 0.8)
        self.assertEqual(placed["qty"], 8.1)

    def test_close_all_uses_venue(self):
        session = {"account": 2, "name": "second"}
        pos = type("P", (), {
            "symbol": "BTCUSDT", "side": "LONG", "qty": 0.01,
            "venue_id": "p1", "unrealised_pnl": 1.2, "entry_price": 100,
        })()
        venue = type("V", (), {})()
        venue.authenticated = True
        venue.positions = lambda: [pos]
        closed = []
        def close(p, reason=""):
            closed.append(p.symbol)
            venue.positions = lambda: []
            return type("R", (), {"venue_order_id": "x", "message": ""})()
        venue.close = close
        with patch.object(wa_dialog, "from_slot", return_value=venue):
            reply = wa_dialog.handle(session, "close all")
        self.assertEqual(closed, ["BTCUSDT"])
        self.assertIn("flat", reply.lower())

    def test_auto_mate_enables_automatic_trading_with_plain_warning(self):
        session = {"account": 2, "name": "second"}
        reply = wa_dialog.handle(session, "auto mate low risk")
        self.assertTrue(session["auto_trade"])
        self.assertEqual(session["auto_plan"], "low")
        self.assertIn("Auto trade is ON", reply)
        self.assertIn("safety checks", reply)
        self.assertIn("auto off", reply)
        off = wa_dialog.handle(session, "auto off")
        self.assertFalse(session["auto_trade"])
        self.assertIn("OFF", off)

    def test_auto_take_signal_uses_guarded_place_plan_path(self):
        session = {"account": 2, "name": "second", "auto_trade": True, "auto_plan": "low"}
        row = {
            "id": "inj-auto",
            "direction": "SHORT",
            "bitunix_symbol": "INJUSDT",
            "binance_symbol": "INJUSDT",
            "raw": "INJUSDT short entry 7.9 sl 8.5 tp 6 lev 3x",
            "entry": 7.9,
            "stop": 8.5,
            "take_profit": 6.0,
            "take_profits": [6.0],
        }
        venue = type("V", (), {})()
        venue.authenticated = True
        venue.account_name = "second"
        venue.balance = lambda: Balance("USDT", available=1000, used=0)
        venue.mark_price = lambda symbol: 7.9
        venue.instruments = lambda: {
            "INJUSDT": Instrument("INJUSDT", "INJ", "USDT", 1, 4, Decimal("0.8"), max_leverage=50)
        }
        called = {}

        def fake_place_plan(_venue, _rec, plan, **kwargs):
            called["request_id"] = kwargs.get("request_id")
            called["qty"] = plan["qty"]
            return {"order_id": "auto-1", "symbol": "INJUSDT"}

        with patch.object(wa_dialog, "from_slot", return_value=venue), \
             patch.object(wa_dialog, "place_plan", side_effect=fake_place_plan):
            reply = wa_dialog.auto_take_signal(session, row)

        self.assertIn("Auto sent", reply)
        self.assertEqual(called["request_id"], "inj-auto")
        self.assertGreater(called["qty"], 0)
        self.assertNotIn("signal", session)


class TestMinimumSizeDialog(unittest.TestCase):
    def setUp(self):
        cost = 0.0008
        stop = (0.1 / 3.43351 + 0.5 * (1 - cost)) / (1 + cost)
        self.row = {
            "id": "synthetic-zeta", "bitunix_symbol": "ZETAUSDT", "direction": "SHORT",
            "raw": "ZETAUSDT short entry CMP sl 0.55 tp 0.2",
            "entry": 0.52, "stop": stop, "take_profits": [0.2],
        }
        self.session = {"account": 1, "signal": self.row, "step": "risk", "equity": 1000}
        self.venue = Mock(authenticated=True, account_name="test")
        self.venue.balance.return_value = Balance("USDT", available=20, used=0)
        self.venue.mark_price.return_value = 0.5
        self.venue.instruments.return_value = {
            "ZETAUSDT": Instrument("ZETAUSDT", "ZETA", "USDT", 0, 5, Decimal("10"), max_leverage=50)
        }
        for name, kwargs in (
            ("_open_signals", {"return_value": [self.row]}),
            ("from_slot", {"return_value": self.venue}),
            ("place_plan", {"return_value": {"order_id": "test-order", "symbol": "ZETAUSDT"}}),
        ):
            patcher = patch.object(wa_dialog, name, **kwargs)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            if name == "place_plan":
                self.place = mocked

    def test_rejection_shows_math_and_requires_new_tier_then_confirmation(self):
        reply = wa_dialog.handle(self.session, "low risk")
        self.assertIn("I did not send this trade", reply)
        self.assertIn("Bitunix will not accept a ZETAUSDT order smaller than 10 ZETA", reply)
        self.assertIn("Your selected level (Low risk) only allowed about 3 ZETA", reply)
        self.assertIn("1.46% of your account", reply)
        self.assertIn("bigger trade than you approved", reply)
        self.assertIn("Smallest valid choice right now: High risk (3% risk)", reply)
        self.assertIn("then *yes*", reply)
        self.assertIn("Nothing has been sent", reply)
        self.assertEqual(self.session["step"], "risk")
        self.assertNotIn("plan", self.session)
        self.assertNotIn("quote", self.session)
        self.assertIn("Nothing to send", wa_dialog.handle(self.session, "yes"))
        self.place.assert_not_called()
        reply = wa_dialog.handle(self.session, "high risk")
        self.assertIn("Reply *yes*", reply)
        self.assertEqual(self.session["quote"]["entry"], 0.5)
        self.assertEqual(self.session["quote"]["qty"], 20)
        self.place.assert_not_called()
        self.assertIn("Sent", wa_dialog.handle(self.session, "yes"))
        self.place.assert_called_once()

    def test_no_alternative_when_no_tier_qualifies(self):
        self.venue.balance.return_value = Balance("USDT", available=1, used=0)
        reply = wa_dialog.handle(self.session, "low risk")
        self.assertIn("No risk level is valid", reply)
        self.assertIn("current account balance and exchange minimum", reply)
        self.place.assert_not_called()

    def test_failed_repricing_clears_previous_higher_risk_approval(self):
        wa_dialog.handle(self.session, "high risk")
        self.assertEqual(self.session["plan"], "high")
        self.venue.mark_price.side_effect = VenueError("bitunix", "no-price", "unavailable")
        reply = wa_dialog.handle(self.session, "low risk")
        self.assertIn("Cannot price", reply)
        self.assertNotIn("plan", self.session)
        self.assertNotIn("quote", self.session)
        self.assertIn("Nothing to send", wa_dialog.handle(self.session, "yes"))
        self.place.assert_not_called()

    def test_size_is_rechecked_when_user_confirms(self):
        wa_dialog.handle(self.session, "high risk")
        self.venue.balance.return_value = Balance("USDT", available=1, used=0)
        reply = wa_dialog.handle(self.session, "yes")
        self.assertIn("Bitunix will not accept a ZETAUSDT order smaller than 10 ZETA", reply)
        self.assertNotIn("plan", self.session)
        self.place.assert_not_called()


if __name__ == "__main__":
    unittest.main()

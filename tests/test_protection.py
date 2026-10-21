import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fxglitch.agent import protection, wa_dialog
from fxglitch.agent.plans import recommend, place_plan
from fxglitch.agent.policy import ProposedTrade, evaluate
from fxglitch.agent.protection import AccountState, protect_account, trailing_stop
from fxglitch.venues.base import Balance, Instrument, OrderResult, Position, VenueError

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
SIGNAL = "INJUSDT short entry 7.9 sl 8.5 tp 6 lev 3x"


class FakeVenue:
    name = "bitunix"
    account_id = 1
    account_name = "test"
    authenticated = True

    def __init__(self):
        self.equity, self.mark = 1000.0, 7.9
        self.held, self.sent, self.moves = [], [], []
        self.stop_prices = {}
        self.accepted = True

    def balance(self):
        return Balance("USDT", self.equity, 0)

    def instruments(self):
        return {"INJUSDT": Instrument("INJUSDT", "INJ", "USDT", 1, 4, Decimal("0.8"), max_leverage=50)}

    def mark_price(self, symbol):
        return self.mark

    def positions(self):
        return self.held

    def stops(self):
        return self.stop_prices

    def set_leverage(self, symbol, leverage):
        pass

    def place(self, request):
        self.sent.append(request)
        return OrderResult(self.accepted, "o1", message="test rejection" if not self.accepted else "")

    def set_stop(self, pos, stop):
        self.moves.append(stop)
        self.stop_prices[pos.venue_id] = stop
        return OrderResult(True)


class TestProtection(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        p = patch.object(protection, "ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)
        self.venue = FakeVenue()

    def test_target_latches_across_drawback_and_restart_and_rolls_daily(self):
        path = str(self.root / "state.json")
        state = AccountState(path=path)
        state.observe(Balance("USDT", 1000, 0), NOW)
        state.observe(Balance("USDT", 1050, 0), NOW)
        state = AccountState.load(path)
        state.observe(Balance("USDT", 1040, 0), NOW)
        self.assertTrue(state.daily_target_hit)
        self.assertAlmostEqual(state.progress(1040)["weekly_gain_pct"], 4)
        state.observe(Balance("USDT", 1040, 0), NOW + timedelta(days=1))
        self.assertFalse(state.daily_target_hit)
        self.assertEqual(state.week_open_equity, 1040)

    def test_trailing_locks_half_profit_above_and_below_one_r_both_sides(self):
        for side in (1, -1):
            for profit in (5, 15, 35):
                with self.subTest(side=side, profit=profit):
                    pos = Position("INJUSDT", side, 1, 100, unrealised_pnl=profit)
                    current = 100 - side * 10
                    stop = trailing_stop(pos, current, {}, True)
                    self.assertEqual(stop, 100 + side * profit / 2)

    def test_stop_does_not_loosen_on_pullback(self):
        state = {}
        pos = Position("INJUSDT", 1, 1, 100, unrealised_pnl=30)
        target = trailing_stop(pos, 90, state, True)
        state["last_stop"] = target
        pullback = Position("INJUSDT", 1, 1, 100, unrealised_pnl=20)
        self.assertIsNone(trailing_stop(pullback, target, state, True))

    def test_profit_protection_still_runs_during_entry_halt(self):
        state = protection.observe_account(self.venue, self.venue.balance())
        state.daily_target_hit = True
        state.halt("manual")
        self.venue.held = [Position("INJUSDT", 1, 1, 100, venue_id="p1", unrealised_pnl=15)]
        self.venue.stop_prices = {"p1": 90}
        protect_account(self.venue)
        self.assertEqual(self.venue.moves, [107.5])
        stored = AccountState.load(state.path)
        self.assertEqual(stored.trailing["p1"]["initial_stop"], 90)
        self.assertTrue(stored.halted)

    def test_live_sizing_includes_costs_and_rounding(self):
        rec = recommend(SIGNAL, balance=self.venue.balance(), instruments=self.venue.instruments())
        low = rec.plans[-1]
        self.assertEqual(low["qty"], 8.1)
        self.assertLessEqual(low["loss_if_sl"], 5)
        self.assertLess(low["win_if_tp"], low["qty"] * 1.9)

    def test_send_resizes_down_for_changed_live_price(self):
        rec = recommend(SIGNAL, instruments=self.venue.instruments())
        plan = rec.plans[-1]
        self.venue.mark = 7.7
        result = place_plan(self.venue, rec, plan, request_id="signal-1")
        self.assertTrue(result["sent"])
        self.assertEqual(plan["entry"], 7.7)
        self.assertLess(plan["qty"], 8.1)
        self.assertLessEqual(plan["loss_if_sl"], 5)
        self.assertAlmostEqual(plan["notional"], plan["qty"] * 7.7)

    def test_replayed_signal_is_not_sent_twice_even_with_flat_book(self):
        for attempt in range(2):
            rec = recommend(SIGNAL, instruments=self.venue.instruments())
            if attempt == 0:
                place_plan(self.venue, rec, rec.plans[-1], request_id="signal-1")
            else:
                with self.assertRaisesRegex(VenueError, "already submitted"):
                    place_plan(self.venue, rec, rec.plans[-1], request_id="signal-1")
        self.assertEqual(len(self.venue.sent), 1)

    def test_rejects_existing_position_missing_price_and_daily_loss(self):
        for cause in ("holding", "price", "loss", "balance"):
            with self.subTest(cause=cause):
                venue = FakeVenue()
                rec = recommend(SIGNAL, instruments=venue.instruments())
                protection.observe_account(venue, venue.balance())
                if cause == "holding":
                    venue.held = [Position("INJUSDT", -1, 1, 7.9)]
                elif cause == "price":
                    venue.mark = None
                elif cause == "loss":
                    venue.equity = 930
                else:
                    venue.balance = lambda: (_ for _ in ()).throw(VenueError("bitunix", "network", "offline"))
                with self.assertRaises(VenueError):
                    place_plan(venue, rec, rec.plans[-1])
                self.assertFalse(venue.sent)

    def test_rejected_order_never_claims_success(self):
        self.venue.accepted = False
        rec = recommend(SIGNAL, instruments=self.venue.instruments())
        with self.assertRaisesRegex(VenueError, "test rejection"):
            place_plan(self.venue, rec, rec.plans[-1])

    def test_preview_uses_live_equity(self):
        self.venue.equity = 200
        session = {"account": 1, "signal": {"raw": SIGNAL, "bitunix_symbol": "INJUSDT"}}
        with patch.object(wa_dialog, "from_slot", return_value=self.venue):
            reply = wa_dialog.handle(session, "low risk")
        self.assertIn("lose ~0.98", reply)
        self.assertEqual(session["equity"], 200)

    def test_invalid_targets_and_stop_updates_do_not_produce_orders(self):
        for message in ("INJUSDT long entry 7.9 sl 7 tp 6", "Adjust your sl to 7 INJUSDT long entry 7.9 tp 9"):
            self.assertFalse(recommend(message).can_recommend)

    def test_explicit_quantity_and_nonfinite_prices_are_checked(self):
        for kwargs in ({"qty": 1000}, {"entry": float("nan")}, {"stop": -1}, {"risk_pct": -1}):
            args = dict(symbol="INJUSDT", action="open", direction="LONG", entry=7.9, stop=7)
            args.update(kwargs)
            self.assertFalse(evaluate(ProposedTrade(**args), balance=self.venue.balance(), positions=[]).allowed)

    def test_configured_account_is_protected_without_a_logged_in_chat(self):
        from fxglitch.agent import whatsapp_user

        with patch.object(whatsapp_user.WhatsAppBridge, "_load_users"):
            bridge = whatsapp_user.WhatsAppBridge()
        with patch.object(whatsapp_user, "listed_accounts", return_value=[{"id": 1, "ready": True}]), \
             patch.object(whatsapp_user, "from_slot", return_value=self.venue), \
             patch.object(whatsapp_user, "protect_account") as protect:
            bridge._trail_once()
        protect.assert_called_once_with(self.venue)

    def test_auto_enabled_whatsapp_user_attempts_new_signals(self):
        from fxglitch.agent import whatsapp_user

        with patch.object(whatsapp_user.WhatsAppBridge, "_load_users"):
            bridge = whatsapp_user.WhatsAppBridge()
        bridge._status = "linked"
        bridge._client = object()
        bridge._loop = object()
        bridge._users = {
            "auto": {"account": 1, "auto_trade": True},
            "manual": {"account": 1},
        }
        rows = [{"id": "s1", "raw": SIGNAL}]

        def done(coro, loop):
            coro.close()

        with patch.object(whatsapp_user, "auto_take_signal", return_value="AUTO SENT") as auto, \
             patch.object(whatsapp_user.asyncio, "run_coroutine_threadsafe", side_effect=done) as scheduled:
            bridge.notify_signals(rows)

        auto.assert_called_once_with(bridge._users["auto"], rows[0])
        self.assertEqual(scheduled.call_count, 2)

    def test_definite_exchange_refusal_does_not_burn_the_signal(self):
        rec = recommend(SIGNAL, instruments=self.venue.instruments())
        with patch.object(self.venue, "place", side_effect=VenueError("bitunix", 400, "refused")):
            with self.assertRaises(VenueError):
                place_plan(self.venue, rec, rec.plans[-1], request_id="retryable-signal")
        result = place_plan(self.venue, rec, rec.plans[-1], request_id="retryable-signal")
        self.assertTrue(result["sent"])


if __name__ == "__main__":
    unittest.main()

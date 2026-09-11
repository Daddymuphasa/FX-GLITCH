import os
import unittest
from unittest.mock import patch

from fxglitch.agent import wa_dialog


class TestWaDialog(unittest.TestCase):
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
        with patch.object(wa_dialog, "_open_signals", return_value=rows):
            wa_dialog.handle(session, "1")
            reply = wa_dialog.handle(session, "normal")
        self.assertEqual(session["plan"], "mid")
        self.assertIn("yes", reply.lower())


if __name__ == "__main__":
    unittest.main()

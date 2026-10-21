import json
import unittest
from unittest.mock import patch

from fxglitch.venues.base import Balance, Position, SHORT
from fxglitch.agent.http_api import dispatch


class FakeVenue:
    authenticated = True

    def balance(self):
        return Balance("USDT", available=22.5, used=5.0, unrealised_pnl=-1.2)

    def positions(self):
        return [Position("RAYUSDT", SHORT, 38.6, 1.9395, venue_id="p1",
                         leverage=15, unrealised_pnl=-1.2, liquidation_price=2.63)]

    def stops(self):
        return {"p1": 2.2}

    def targets(self):
        return {"p1": 1.0}

    def mark_price(self, symbol):
        return {"RAYUSDT": 2.01, "ETHUSDT": 4000}.get(symbol)


class TestPublicStatus(unittest.TestCase):
    def test_status_is_public(self):
        venue = FakeVenue()
        signals = [{
            "id": "tg:1",
            "source": "telegram",
            "bitunix_symbol": "ETHUSDT",
            "direction": "SHORT",
            "entry": 4100,
            "stop": 4300,
            "take_profits": [3500],
            "still_good": True,
            "raw": "ETHUSDT short",
            "posted_at": "2026-09-24T12:00:00Z",
        }]
        with patch("fxglitch.agent.http_api._bitunix", return_value=venue), \
             patch("fxglitch.agent.http_api.bridge.snapshot", return_value={"status": "watching", "watch": {"title": "Cosmas"}}), \
             patch("fxglitch.agent.http_api.list_signals", return_value=signals):
            status, ctype, body = dispatch("GET", "/api/status", {}, {}, {"Host": "fxglitch.xyz"})
        self.assertEqual(status, 200)
        self.assertIn("json", ctype)
        payload = json.loads(body)
        self.assertEqual(payload["watching"], "watching")
        self.assertAlmostEqual(payload["balance"]["equity"], 26.3, places=3)
        self.assertEqual(payload["positions"][0]["symbol"], "RAYUSDT")
        self.assertEqual(payload["positions"][0]["sl"], 2.2)
        self.assertEqual(payload["available"][0]["symbol"], "ETHUSDT")
        self.assertTrue(payload["available"][0]["still_good"])

    def test_open_symbol_is_not_listed_as_available(self):
        venue = FakeVenue()
        signals = [{
            "id": "tg:ray",
            "source": "telegram",
            "bitunix_symbol": "RAYUSDT",
            "direction": "SHORT",
            "entry": 1.94,
            "stop": 2.2,
            "take_profits": [1.0],
            "still_good": True,
            "raw": "RAY short",
        }]
        with patch("fxglitch.agent.http_api._bitunix", return_value=venue), \
             patch("fxglitch.agent.http_api.bridge.snapshot", return_value={"status": "watching"}), \
             patch("fxglitch.agent.http_api.list_signals", return_value=signals):
            _, _, body = dispatch("GET", "/api/status", {}, {}, {})
        payload = json.loads(body)
        self.assertEqual(payload["available"], [])

    def test_stale_setup_is_hidden(self):
        venue = FakeVenue()
        signals = [{
            "id": "tg:old",
            "source": "telegram",
            "bitunix_symbol": "ETHUSDT",
            "direction": "SHORT",
            "entry": 4100,
            "stop": 4300,
            "take_profits": [3500],
            "still_good": True,
            "raw": "ETHUSDT short",
            "posted_at": "2026-09-01T12:00:00Z",
        }]
        with patch("fxglitch.agent.http_api._bitunix", return_value=venue), \
             patch("fxglitch.agent.http_api.bridge.snapshot", return_value={"status": "watching"}), \
             patch("fxglitch.agent.http_api.list_signals", return_value=signals):
            _, _, body = dispatch("GET", "/api/status", {}, {}, {})
        self.assertEqual(json.loads(body)["available"], [])


if __name__ == "__main__":
    unittest.main()

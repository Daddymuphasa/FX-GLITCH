"""The live trailing worker moves stops at 200% ROI steps."""

import unittest
from unittest.mock import patch

from fxglitch.venues.base import LONG, OrderResult, Position
from tools import trail_200


class FakeVenue:
    def __init__(self):
        self.mark = 119.9
        self.stop = 90.0
        self.moves = []

    def mark_price(self, symbol):
        return self.mark

    def stops(self, symbol):
        return {"position-1": self.stop}

    def set_stop(self, position, stop):
        self.stop = stop
        self.moves.append(stop)
        return OrderResult(True, "stop-order")


class TestTrail200(unittest.TestCase):
    def test_first_step_moves_to_breakeven_then_locks_200_percent(self):
        venue = FakeVenue()
        position = Position("BTCUSDT", LONG, 1, 100, venue_id="position-1", leverage=10)
        state = {}
        with patch.dict(trail_200.os.environ, {"FXGLITCH_TRAIL_STEP": "200"}), \
             patch.object(trail_200, "_save_orig"), \
             patch.object(trail_200, "alert"):
            trail_200.trail_one(venue, position, state)
            self.assertEqual(venue.moves, [])
            venue.mark = 120.0
            trail_200.trail_one(venue, position, state)
            self.assertEqual(venue.moves, [100.0])
            venue.mark = 140.0
            trail_200.trail_one(venue, position, state)
            self.assertEqual(venue.moves, [100.0, 120.0])
            self.assertEqual(state["position-1"]["step_size"], 200.0)


if __name__ == "__main__":
    unittest.main()

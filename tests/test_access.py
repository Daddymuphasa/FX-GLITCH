import json
import os
import unittest

from fxglitch.agent import access
from fxglitch.venues.bitunix import listed_accounts


class TestAccessCodes(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in list(os.environ)}
        for key in list(os.environ):
            if key.startswith("BITUNIX_") or key == "FXGLITCH_OPERATOR_ACCESS":
                os.environ.pop(key, None)

    def tearDown(self):
        for key in list(os.environ):
            if key.startswith("BITUNIX_") or key == "FXGLITCH_OPERATOR_ACCESS":
                os.environ.pop(key, None)
        for key, value in self._old.items():
            if value is not None:
                os.environ[key] = value

    def test_code_maps_to_slot_two(self):
        os.environ["BITUNIX_ACCESS"] = "alpha"
        os.environ["BITUNIX_ACCESS_2"] = "bravo"
        os.environ["BITUNIX_NAME_2"] = "second"
        os.environ["BITUNIX_API_KEY_2"] = "k"
        os.environ["BITUNIX_SECRET_KEY_2"] = "s"
        user = access.unlock("bravo")
        self.assertEqual(user["account"], 2)
        self.assertEqual(user["name"], "second")
        self.assertFalse(user["admin"])
        self.assertIsNone(access.unlock("nope"))

    def test_cookie_survives_roundtrip(self):
        os.environ["BITUNIX_ACCESS_2"] = "bravo"
        os.environ["BITUNIX_NAME_2"] = "second"
        user = access.unlock("bravo")
        token = access.mint(user)
        back = access.read_token(token)
        self.assertEqual(back["account"], 2)
        self.assertEqual(back["name"], "second")

    def test_health_post_logs_in(self):
        os.environ["BITUNIX_ACCESS_2"] = "bravo"
        os.environ["BITUNIX_NAME_2"] = "second"
        from fxglitch.agent.http_api import dispatch
        status, ctype, body, extra = dispatch(
            "POST", "/api/health", {}, {"code": "bravo"}, {"Host": "localhost:8765"}
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["account"], 2)
        self.assertIn("Set-Cookie", extra)

    def test_account_three_is_listed(self):
        rows = listed_accounts()
        self.assertEqual([r["id"] for r in rows], [1, 2, 3])
        self.assertTrue(rows[2]["coming_soon"] or not rows[2]["ready"])


if __name__ == "__main__":
    unittest.main()

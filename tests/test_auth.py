import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["FXGLITCH_AUTH"] = str(Path(tempfile.gettempdir()) / "fxg-auth-test.json")
os.environ["FXGLITCH_BOOKS"] = str(Path(tempfile.gettempdir()) / "fxg-books-test")

from fxglitch.agent import auth


class TestPasskeyStore(unittest.TestCase):
    def setUp(self):
        path = auth._auth_path()
        if path.exists():
            path.unlink()
        folder = auth._book_dir()
        if folder.exists():
            for child in folder.glob("*"):
                child.unlink()

    def test_rp_id_maps_loopback_to_localhost(self):
        self.assertEqual(auth.rp_id_for("127.0.0.1:8765"), "localhost")
        self.assertEqual(auth.rp_id_for("localhost"), "localhost")
        self.assertEqual(auth.rp_id_for("fxglitch.xyz"), "fxglitch.xyz")

    def test_cookie_roundtrip(self):
        header = auth.cookie_header("abc")
        self.assertIn("fxg_sid=abc", header)
        self.assertIn("HttpOnly", header)
        parsed = auth.parse_cookie({"Cookie": "fxg_sid=abc; other=1"})
        self.assertEqual(parsed, "abc")
        self.assertIsNone(auth.current_user({"Cookie": "fxg_sid=abc"}))

    def test_book_is_per_user(self):
        auth.save_fill("u1", {"symbol": "BTCUSDT", "plan": "Normal"})
        auth.save_fill("u2", {"symbol": "ETHUSDT", "plan": "Easy"})
        self.assertEqual(auth.load_book("u1")[0]["symbol"], "BTCUSDT")
        self.assertEqual(auth.load_book("u2")[0]["symbol"], "ETHUSDT")

    def test_register_options_need_a_name(self):
        with self.assertRaises(ValueError):
            auth.begin_register("x", {}, "localhost")

    def test_register_begin_returns_webauthn_options(self):
        box = auth.begin_register("Ada", {"Host": "localhost:8765"}, "localhost:8765")
        self.assertTrue(box["flow_id"])
        self.assertIn("challenge", box["options"])
        self.assertEqual(box["options"]["rp"]["id"], "localhost")
        stored = json.loads(auth._auth_path().read_text(encoding="utf-8"))
        self.assertIn(box["flow_id"], stored["flows"])


if __name__ == "__main__":
    unittest.main()

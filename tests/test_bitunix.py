"""Tests for the Bitunix futures venue.

Every test here runs against a fake transport. No network, no API key, no
order ever leaves the building. That is deliberate: a client you can only
exercise by sending real orders to a real account is a client nobody tests.

The tests that matter most are the ones covering the four ways this venue
quietly does the wrong thing rather than failing loudly:

  - code != 0 arriving inside an HTTP 200, so an error looks like success
  - klines capped at 200, so a 200-period filter silently runs blind
  - the still-forming candle, which is lookahead handed over as data
  - size rounding, where rounding the wrong way risks more than asked
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.venues.base import (
    LONG, SHORT, Instrument, OrderRequest, Position, VenueError,
)
from fxglitch.venues.bitunix import Bitunix, sign_request

PAIRS = {"code": 0, "msg": "Success", "data": [
    {"symbol": "BTCUSDT", "base": "BTC", "quote": "USDT", "minTradeVolume": "0.0001",
     "maxMarketOrderVolume": "50000", "basePrecision": 4, "quotePrecision": 1,
     "minLeverage": 1, "maxLeverage": 125, "defaultLeverage": 20,
     "symbolStatus": "OPEN", "isApiSupported": True},
    {"symbol": "ETHUSDT", "base": "ETH", "quote": "USDT", "minTradeVolume": "0.01",
     "maxMarketOrderVolume": "10000", "basePrecision": 2, "quotePrecision": 2,
     "maxLeverage": 100, "symbolStatus": "OPEN", "isApiSupported": True},
    {"symbol": "HALTUSDT", "base": "HALT", "quote": "USDT", "minTradeVolume": "1",
     "basePrecision": 0, "quotePrecision": 4, "maxLeverage": 20,
     "symbolStatus": "CLOSED", "isApiSupported": True},
    {"symbol": "NOAPIUSDT", "base": "NOAPI", "quote": "USDT", "minTradeVolume": "1",
     "basePrecision": 0, "quotePrecision": 4, "maxLeverage": 20,
     "symbolStatus": "OPEN", "isApiSupported": False},
]}


class FakeTransport:
    """Records what the client sent and replays canned responses."""

    def __init__(self, responses: dict | None = None, default=None):
        self.responses = responses or {}
        self.default = default or {"code": 0, "msg": "Success", "data": []}
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url,
                           "headers": headers, "body": body})
        path = url.split("?")[0].replace("https://fapi.bitunix.com", "")
        hit = self.responses.get(path, self.default)
        return hit(self.calls[-1]) if callable(hit) else hit

    @property
    def last(self):
        return self.calls[-1]


def client(responses=None, **kw):
    responses = dict(responses or {})
    responses.setdefault("/api/v1/futures/market/trading_pairs", PAIRS)
    kw.setdefault("api_key", "k")
    kw.setdefault("secret_key", "s")
    return Bitunix(transport=FakeTransport(responses), **kw)


class TestSignature(unittest.TestCase):
    def test_matches_the_documented_worked_example(self):
        # Reproduces the example in Bitunix's own sign.html byte for byte. If
        # this fails, either they changed the scheme or we mistyped it - and
        # every private call is rejected until it is fixed.
        import hashlib
        nonce, ts, key, secret = "123456", "20241120123045", "yourApiKey", "yourSecretKey"
        query = {"id": "1", "uid": "200"}
        body = '{"uid":"2899","arr":[{"id":1,"name":"maple"}]}'
        expected_digest = hashlib.sha256(
            (nonce + ts + key + "id1uid200" + body).encode()).hexdigest()
        expected = hashlib.sha256((expected_digest + secret).encode()).hexdigest()
        self.assertEqual(sign_request(key, secret, nonce, ts, query, body), expected)

    def test_query_params_are_sorted_ascii_not_insertion_order(self):
        a = sign_request("k", "s", "n", "t", {"uid": "200", "id": "1"})
        b = sign_request("k", "s", "n", "t", {"id": "1", "uid": "200"})
        self.assertEqual(a, b)

    def test_the_secret_changes_the_signature(self):
        self.assertNotEqual(sign_request("k", "s1", "n", "t"),
                            sign_request("k", "s2", "n", "t"))

    def test_signature_is_not_hmac(self):
        # Muscle memory from every other exchange says HMAC. It is not, and a
        # silent switch to HMAC would produce a plausible-looking hex string
        # that the venue rejects on every call.
        import hmac, hashlib
        theirs = hmac.new(b"s", b"nt", hashlib.sha256).hexdigest()
        self.assertNotEqual(sign_request("k", "s", "n", "t"), theirs)


class TestCredentials(unittest.TestCase):
    def test_public_data_works_with_no_keys(self):
        c = Bitunix(api_key="", secret_key="",
                    transport=FakeTransport({"/api/v1/futures/market/trading_pairs": PAIRS}))
        self.assertIn("BTCUSDT", c.instruments())
        self.assertFalse(c.authenticated)

    def test_trading_without_keys_fails_before_anything_is_sent(self):
        c = Bitunix(api_key="", secret_key="",
                    transport=FakeTransport({"/api/v1/futures/market/trading_pairs": PAIRS}))
        with self.assertRaises(VenueError) as caught:
            c.positions()
        self.assertIn("BITUNIX_API_KEY", caught.exception.message)

    def test_credentials_go_in_headers_never_the_url(self):
        # A secret in a query string ends up in proxy logs, browser history and
        # every error report that quotes the URL.
        c = client(api_key="KEY-abc123", secret_key="SECRET-xyz789")
        c.positions(symbol="BTCUSDT")
        url = c._transport.last["url"]
        self.assertNotIn("KEY-abc123", url)
        self.assertNotIn("SECRET-xyz789", url)
        self.assertEqual(c._transport.last["headers"]["api-key"], "KEY-abc123")

    def test_the_secret_itself_is_never_transmitted(self):
        # The signature is derived from it; the secret must never be sent.
        c = client(api_key="KEY-abc123", secret_key="SECRET-xyz789")
        c.positions()
        call = c._transport.last
        self.assertNotIn("SECRET-xyz789", str(call["headers"]) + str(call["url"]))


class TestErrorHandling(unittest.TestCase):
    def test_business_error_inside_an_http_200_is_raised(self):
        # The single most dangerous response shape this venue produces.
        c = client({"/api/v1/futures/trade/place_order":
                    {"code": 20007, "msg": "Insufficient balance", "data": None},
                    "/api/v1/futures/account": {"code": 0, "data": [
                        {"marginCoin": "USDT", "available": "0", "margin": "0",
                         "positionMode": "ONE_WAY"}]}})
        with self.assertRaises(VenueError) as caught:
            c.place(OrderRequest("BTCUSDT", LONG, Decimal("0.001")))
        self.assertEqual(caught.exception.code, 20007)
        self.assertIn("Insufficient balance", caught.exception.message)

    def test_error_marks_itself_retryable_or_not(self):
        c = client({"/api/v1/futures/account": {"code": 10002, "msg": "busy"}})
        with self.assertRaises(VenueError) as caught:
            c.balance()
        self.assertTrue(caught.exception.retryable)

        c = client({"/api/v1/futures/account": {"code": 20007, "msg": "no funds"}})
        with self.assertRaises(VenueError) as caught:
            c.balance()
        self.assertFalse(caught.exception.retryable)


class TestInstruments(unittest.TestCase):
    def test_parses_precision_and_limits(self):
        btc = client().instruments()["BTCUSDT"]
        self.assertEqual(btc.qty_precision, 4)
        self.assertEqual(btc.min_qty, Decimal("0.0001"))
        self.assertEqual(btc.max_leverage, 125)
        self.assertTrue(btc.tradeable)

    def test_halted_and_api_disabled_pairs_are_not_tradeable(self):
        got = client().instruments()
        self.assertFalse(got["HALTUSDT"].tradeable)
        self.assertFalse(got["NOAPIUSDT"].tradeable)

    def test_universe_excludes_untradeable_and_leads_with_btc(self):
        got = client().universe()
        self.assertEqual(got[0], "BTCUSDT")
        self.assertEqual(got, ["BTCUSDT", "ETHUSDT"])

    def test_instruments_are_cached(self):
        c = client()
        c.instruments()
        c.instruments()
        pair_calls = [x for x in c._transport.calls if "trading_pairs" in x["url"]]
        self.assertEqual(len(pair_calls), 1)


class TestSizing(unittest.TestCase):
    def setUp(self):
        self.btc = client().instruments()["BTCUSDT"]

    def test_size_rounds_down_never_up(self):
        # 0.00019 BTC must become 0.0001, not 0.0002. Rounding up risks more
        # than the caller asked for.
        self.assertEqual(self.btc.round_qty(0.00019), Decimal("0.0001"))
        self.assertEqual(self.btc.round_qty(0.99999), Decimal("0.9999"))

    def test_dust_is_refused_rather_than_sent_as_zero(self):
        ok, why = self.btc.fits(self.btc.round_qty(0.00001))
        self.assertFalse(ok)
        self.assertIn("precision", why)

    def test_below_minimum_is_refused(self):
        eth = client().instruments()["ETHUSDT"]
        ok, why = eth.fits(Decimal("0.005"))
        self.assertFalse(ok)
        self.assertIn("minimum", why)

    def test_untradeable_symbol_is_refused_even_at_a_valid_size(self):
        ok, why = client().instruments()["HALTUSDT"].fits(Decimal("5"))
        self.assertFalse(ok)
        self.assertIn("not tradeable", why)


class TestCandles(unittest.TestCase):
    DAY_MS = 86_400_000

    def klines(self, n, end_ms, step=DAY_MS):
        """n daily bars ending at end_ms, newest last."""
        return {"code": 0, "data": [
            {"time": end_ms - step * i, "open": 100 + i, "high": 110 + i,
             "low": 90 + i, "close": 105 + i, "baseVol": "7", "quoteVol": "700"}
            for i in range(n)
        ]}

    def test_pages_past_the_200_bar_cap(self):
        # A 200-period trend filter needs more than 200 bars to be warm on the
        # bar it decides on. One request cannot supply that.
        now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        pages = iter([self.klines(200, now - self.DAY_MS * 2),
                      self.klines(200, now - self.DAY_MS * 205)])

        c = client({"/api/v1/futures/market/kline": lambda call: next(pages)})
        got = c.candles("BTCUSDT", "1d", limit=400)
        kline_calls = [x for x in c._transport.calls if "kline" in x["url"]]
        self.assertEqual(len(kline_calls), 2)
        self.assertEqual(len(got), 400)

    def test_candles_come_back_oldest_first(self):
        now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        c = client({"/api/v1/futures/market/kline": self.klines(10, now - self.DAY_MS * 2)})
        got = c.candles("BTCUSDT", "1d", limit=10)
        self.assertEqual(got, sorted(got, key=lambda x: x.time))

    def test_the_unclosed_bar_is_dropped(self):
        # Today's daily bar is still forming; its high is not yet its high.
        # Handing it to a strategy is lookahead, so it must not appear.
        now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        forming = now - 3600_000          # opened an hour ago, closes tomorrow
        c = client({"/api/v1/futures/market/kline": self.klines(5, forming)})
        got = c.candles("BTCUSDT", "1d", limit=5)
        self.assertTrue(all(
            int(x.time.timestamp() * 1000) + self.DAY_MS <= now for x in got
        ), "an unfinished candle was handed to the caller")

    def test_a_closed_bar_is_kept(self):
        now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        closed = now - self.DAY_MS * 2
        c = client({"/api/v1/futures/market/kline": self.klines(5, closed)})
        self.assertEqual(len(c.candles("BTCUSDT", "1d", limit=5)), 5)

    def test_stops_instead_of_spinning_when_the_venue_repeats_itself(self):
        now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        c = client({"/api/v1/futures/market/kline": self.klines(50, now - self.DAY_MS * 2)})
        got = c.candles("BTCUSDT", "1d", limit=1000)
        self.assertLessEqual(len(c._transport.calls), 5)
        self.assertLessEqual(len(got), 50)

    def test_volume_is_base_units_despite_their_inverted_field_names(self):
        # Real BTCUSDT daily row, 2026-08-22. Bitunix labels the BTC figure
        # "quoteVol" and the USDT figure "baseVol" - backwards both times.
        # Trusting the names makes every volume factor wrong by ~76,000x
        # without ever raising an error.
        now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        real = {"code": 0, "data": [
            {"open": "72998.8", "high": "79530.7", "low": "72992.4",
             "close": "78309.1", "quoteVol": "79674.9934",
             "baseVol": "6107090225.25062", "time": now - self.DAY_MS * 2}]}
        got = client({"/api/v1/futures/market/kline": real}).candles("BTCUSDT", "1d", limit=1)
        self.assertAlmostEqual(got[0].volume, 79674.9934)

    def test_unknown_interval_is_refused_locally(self):
        with self.assertRaises(ValueError):
            client().candles("BTCUSDT", "7m")


class TestOrders(unittest.TestCase):
    ACCEPTED = {"code": 0, "data": {"orderId": "111", "clientId": "abc"}}
    ONE_WAY = {"code": 0, "data": [{"marginCoin": "USDT", "available": "1000",
                                   "margin": "0", "positionMode": "ONE_WAY"}]}
    HEDGE = {"code": 0, "data": [{"marginCoin": "USDT", "available": "1000",
                                 "margin": "0", "positionMode": "HEDGE"}]}

    def order_client(self, mode=ONE_WAY):
        return client({"/api/v1/futures/trade/place_order": self.ACCEPTED,
                       "/api/v1/futures/account": mode})

    def sent_body(self, c):
        import json
        return json.loads([x for x in c._transport.calls
                           if "place_order" in x["url"]][-1]["body"])

    def test_the_stop_rides_along_with_the_entry(self):
        # Not a follow-up call. Two calls means a window where the position
        # exists and the stop does not.
        c = self.order_client()
        c.place(OrderRequest("BTCUSDT", LONG, Decimal("0.01"),
                             stop_price=Decimal("58000")))
        body = self.sent_body(c)
        self.assertEqual(body["slPrice"], "58000.0")
        self.assertEqual(body["slOrderType"], "MARKET")

    def test_long_and_short_map_to_buy_and_sell(self):
        c = self.order_client()
        c.place(OrderRequest("BTCUSDT", LONG, Decimal("0.01")))
        self.assertEqual(self.sent_body(c)["side"], "BUY")
        c.place(OrderRequest("BTCUSDT", SHORT, Decimal("0.01")))
        self.assertEqual(self.sent_body(c)["side"], "SELL")

    def test_market_order_carries_no_price(self):
        c = self.order_client()
        c.place(OrderRequest("BTCUSDT", LONG, Decimal("0.01")))
        body = self.sent_body(c)
        self.assertEqual(body["orderType"], "MARKET")
        self.assertNotIn("price", body)

    def test_hedge_mode_adds_tradeside_and_one_way_does_not(self):
        hedged = self.order_client(self.HEDGE)
        hedged.place(OrderRequest("BTCUSDT", LONG, Decimal("0.01")))
        self.assertEqual(self.sent_body(hedged)["tradeSide"], "OPEN")

        one_way = self.order_client(self.ONE_WAY)
        one_way.place(OrderRequest("BTCUSDT", LONG, Decimal("0.01")))
        self.assertNotIn("tradeSide", self.sent_body(one_way))

    def test_client_id_is_forwarded_so_a_retry_cannot_double_fill(self):
        c = self.order_client()
        c.place(OrderRequest("BTCUSDT", LONG, Decimal("0.01"), client_id="fxg-1"))
        self.assertEqual(self.sent_body(c)["clientId"], "fxg-1")

    def test_bad_size_is_caught_before_the_request_is_sent(self):
        c = self.order_client()
        with self.assertRaises(VenueError) as caught:
            c.place(OrderRequest("BTCUSDT", LONG, Decimal("0.00001")))
        self.assertEqual(caught.exception.code, "size-rejected")
        self.assertFalse([x for x in c._transport.calls if "place_order" in x["url"]])

    def test_unknown_symbol_is_caught_locally(self):
        with self.assertRaises(VenueError) as caught:
            self.order_client().place(OrderRequest("FAKEUSDT", LONG, Decimal("1")))
        self.assertEqual(caught.exception.code, "unknown-symbol")

    def test_close_sends_a_reduce_only_order_the_other_way(self):
        c = self.order_client(self.HEDGE)
        c.close(Position("BTCUSDT", LONG, 0.05, 60000.0, venue_id="p1"))
        body = self.sent_body(c)
        self.assertEqual(body["side"], "SELL")
        self.assertTrue(body["reduceOnly"])
        self.assertEqual(body["tradeSide"], "CLOSE")
        self.assertEqual(body["qty"], "0.0500")


class TestPositions(unittest.TestCase):
    def test_parses_a_live_position(self):
        c = client({"/api/v1/futures/position/get_pending_positions": {"code": 0, "data": [
            {"positionId": "p1", "symbol": "BTCUSDT", "side": "SHORT", "qty": "0.5",
             "avgOpenPrice": "61000", "leverage": 10, "unrealizedPNL": "-12.5",
             "liqPrice": "67000", "ctime": "1700000000000"}]}})
        p = c.positions()[0]
        self.assertEqual(p.direction, SHORT)
        self.assertEqual(p.side, "SHORT")
        self.assertEqual(p.venue_id, "p1")
        self.assertEqual(p.liquidation_price, 67000.0)
        self.assertAlmostEqual(p.notional, 30500.0)

    def test_zero_size_rows_are_not_positions(self):
        c = client({"/api/v1/futures/position/get_pending_positions": {"code": 0, "data": [
            {"positionId": "p1", "symbol": "BTCUSDT", "side": "LONG", "qty": "0"}]}})
        self.assertEqual(c.positions(), [])

    def test_no_liquidation_price_when_the_venue_reports_zero(self):
        c = client({"/api/v1/futures/position/get_pending_positions": {"code": 0, "data": [
            {"positionId": "p1", "symbol": "BTCUSDT", "side": "LONG", "qty": "1",
             "avgOpenPrice": "60000", "liqPrice": "0"}]}})
        self.assertIsNone(c.positions()[0].liquidation_price)


class TestBalance(unittest.TestCase):
    def test_equity_counts_margin_and_open_pnl(self):
        c = client({"/api/v1/futures/account": {"code": 0, "data": [
            {"marginCoin": "USDT", "available": "800", "frozen": "0", "margin": "200",
             "crossUnrealizedPNL": "15", "isolationUnrealizedPNL": "5",
             "positionMode": "HEDGE"}]}})
        b = c.balance()
        self.assertEqual(b.available, 800.0)
        self.assertEqual(b.used, 200.0)
        self.assertEqual(b.equity, 1020.0)

    def test_position_mode_is_learned_from_the_account_not_assumed(self):
        c = client({"/api/v1/futures/account": {"code": 0, "data": [
            {"marginCoin": "USDT", "available": "1", "margin": "0",
             "positionMode": "HEDGE"}]}})
        self.assertEqual(c.position_mode(), "HEDGE")


if __name__ == "__main__":
    unittest.main()

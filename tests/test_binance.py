"""Offline contract tests for the Binance futures venue."""

import os
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.venues.base import LONG, SHORT, OrderRequest, Position, VenueError
from fxglitch.venues.binance import Binance, sign_request


PAIRS = {"symbols": [
    {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
     "status": "TRADING", "contractType": "PERPETUAL", "quantityPrecision": 3,
     "pricePrecision": 2, "filters": [
         {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "1000", "stepSize": "0.001"},
         {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
     ]},
    {"symbol": "HALTUSDT", "baseAsset": "HALT", "quoteAsset": "USDT",
     "status": "BREAK", "contractType": "PERPETUAL", "filters": [
         {"filterType": "LOT_SIZE", "minQty": "1", "stepSize": "1"},
         {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
     ]},
]}


class FakeTransport:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def __call__(self, method, url, headers, body):
        from urllib.parse import parse_qs, urlparse
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body,
                           "query": parse_qs(urlparse(url).query)})
        path = urlparse(url).path
        response = self.responses.get(path, {})
        return response(self.calls[-1]) if callable(response) else response


def client(responses=None, **kwargs):
    answers = {"/fapi/v1/exchangeInfo": PAIRS,
               "/fapi/v1/positionSide/dual": {"dualSidePosition": False},
               "/fapi/v1/order": {"orderId": 1, "clientOrderId": "fxg-1"}}
    answers["/fapi/v1/algoOrder"] = {"algoId": 2, "clientAlgoId": "fxg-1-sl"}
    answers.update(responses or {})
    kwargs.setdefault("api_key", "key")
    kwargs.setdefault("secret_key", "secret")
    transport = FakeTransport(answers)
    return Binance(transport=transport, **kwargs), transport


class TestBinanceSigning(unittest.TestCase):
    def test_signature_is_hmac_sha256_over_encoded_parameters(self):
        import hashlib
        import hmac
        params = {"symbol": "BTCUSDT", "timestamp": 123}
        expected = hmac.new(b"secret", b"symbol=BTCUSDT&timestamp=123", hashlib.sha256).hexdigest()
        self.assertEqual(sign_request("secret", params), expected)

    def test_private_credentials_are_not_in_url(self):
        c, transport = client({"/fapi/v2/balance": [{"asset": "USDT", "balance": "10",
                                                       "availableBalance": "10"}]})
        c.balance()
        self.assertNotIn("secret", transport.calls[-1]["url"])
        self.assertEqual(transport.calls[-1]["headers"]["X-MBX-APIKEY"], "key")


class TestBinanceMarketData(unittest.TestCase):
    def test_instruments_and_universe(self):
        c, _ = client()
        self.assertEqual(c.instruments()["BTCUSDT"].min_qty, Decimal("0.001"))
        self.assertEqual(c.instruments()["BTCUSDT"].qty_precision, 3)
        self.assertEqual(c.universe(), ["BTCUSDT"])

    def test_unclosed_candle_is_dropped(self):
        now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        row = [now - 600_000, "100", "110", "90", "105", "7", now + 3_000_000]
        c, _ = client({"/fapi/v1/klines": [row]})
        self.assertEqual(c.candles("BTCUSDT", "1h", limit=1), [])

    def test_unknown_interval_is_refused_locally(self):
        c, _ = client()
        with self.assertRaises(ValueError):
            c.candles("BTCUSDT", "7m")


class TestBinanceTrading(unittest.TestCase):
    def test_set_leverage_is_signed(self):
        c, transport = client({"/fapi/v1/leverage": {"leverage": 8, "symbol": "BTCUSDT"}})
        c.set_leverage("BTCUSDT", 8)
        call = transport.calls[-1]
        self.assertIn("/fapi/v1/leverage", call["url"])
        self.assertEqual(call["query"]["leverage"], ["8"])
        self.assertEqual(call["headers"]["X-MBX-APIKEY"], "key")

    def test_market_order_and_protective_stop(self):
        c, transport = client()
        result = c.place(OrderRequest("BTCUSDT", LONG, Decimal("0.001"),
                                     stop_price=Decimal("58000"), client_id="fxg-1"))
        orders = [call for call in transport.calls if any(path in call["url"] for path in ("/fapi/v1/order?", "/fapi/v1/algoOrder?"))]
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0]["query"]["type"], ["MARKET"])
        self.assertEqual(orders[1]["query"]["type"], ["STOP_MARKET"])
        self.assertEqual(orders[1]["query"]["closePosition"], ["true"])
        self.assertEqual(Decimal(orders[1]["query"]["triggerPrice"][0]), Decimal("58000"))
        self.assertEqual(result.venue_order_id, "1")

    def test_reduced_close_uses_the_opposite_side(self):
        c, transport = client()
        c.close(Position("BTCUSDT", SHORT, 0.002, 60000, venue_id="BTCUSDT:SHORT"))
        order = [call for call in transport.calls if "/fapi/v1/order?" in call["url"]][-1]
        self.assertEqual(order["query"]["side"], ["BUY"])
        self.assertEqual(order["query"]["reduceOnly"], ["true"])

    def test_hedge_close_addresses_the_held_side(self):
        c, transport = client({"/fapi/v1/positionSide/dual": {"dualSidePosition": True}})
        c.close(Position("BTCUSDT", SHORT, 0.002, 60000, venue_id="BTCUSDT:SHORT"))
        order = transport.calls[-1]["query"]
        self.assertEqual(order["side"], ["BUY"])
        self.assertEqual(order["positionSide"], ["SHORT"])
        self.assertNotIn("reduceOnly", order)

    def test_failed_replacement_keeps_the_old_stop(self):
        c, transport = client({"/fapi/v1/openAlgoOrders": [
            {"algoId": 7, "orderType": "STOP_MARKET", "positionSide": "BOTH", "side": "SELL"}],
            "/fapi/v1/algoOrder": {"code": -2021, "msg": "would trigger"}})
        with self.assertRaises(VenueError):
            c.set_stop(Position("BTCUSDT", LONG, 0.01, 60000), 59000)
        self.assertFalse(any(call["method"] == "DELETE" for call in transport.calls))

    def test_failed_entry_protection_submits_reduce_only_close(self):
        c, transport = client({"/fapi/v1/algoOrder": {"code": -2021, "msg": "would trigger"}})
        with self.assertRaisesRegex(VenueError, "emergency close submitted"):
            c.place(OrderRequest("BTCUSDT", LONG, Decimal("0.001"), stop_price=Decimal("59000")))
        self.assertEqual(transport.calls[-1]["query"]["reduceOnly"], ["true"])
        self.assertEqual(transport.calls[-1]["query"]["side"], ["SELL"])

    def test_new_stop_is_accepted_before_cancelling_old_one(self):
        c, transport = client({"/fapi/v1/openAlgoOrders": [
            {"algoId": 7, "orderType": "STOP_MARKET", "positionSide": "BOTH", "side": "SELL"}]})
        c.set_stop(Position("BTCUSDT", LONG, 0.01, 60000), 59000)
        actions = [call["method"] for call in transport.calls if "/fapi/v1/algoOrder?" in call["url"]]
        self.assertEqual(actions, ["POST", "DELETE"])

    def test_bad_size_is_rejected_before_order(self):
        c, transport = client()
        with self.assertRaises(VenueError) as caught:
            c.place(OrderRequest("BTCUSDT", LONG, Decimal("0.0001")))
        self.assertEqual(caught.exception.code, "size-rejected")
        self.assertFalse([call for call in transport.calls if "/fapi/v1/order?" in call["url"]])

    def test_positions_use_signed_one_way_amounts(self):
        c, _ = client({"/fapi/v2/positionRisk": [{
            "symbol": "BTCUSDT", "positionAmt": "-0.002", "entryPrice": "60000",
            "leverage": "5", "unRealizedProfit": "-2", "liquidationPrice": "70000",
        }]})
        [position] = c.positions()
        self.assertEqual(position.direction, SHORT)
        self.assertEqual(position.qty, 0.002)
        self.assertEqual(position.liquidation_price, 70000.0)


if __name__ == "__main__":
    unittest.main()

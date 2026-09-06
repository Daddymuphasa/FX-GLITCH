"""Binance USDⓈ-M perpetual futures venue.

The rest of FX-GLITCH speaks in venue-neutral orders. This module keeps the
Binance details here: HMAC signing, exchange filters, signed position sizes,
and Binance's separate protective-stop order model.

Credentials are read from BINANCE_API_KEY and BINANCE_SECRET_KEY. The base URL
is configurable so the same client can be pointed at Binance's futures demo
environment without changing strategy or runner code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

from ..data import Candle
from .base import (
    LONG, SHORT, Balance, Instrument, OrderRequest, OrderResult, Position,
    Venue, VenueError,
)

BASE_URL = "https://fapi.binance.com"
DEMO_BASE_URL = "https://demo-fapi.binance.com"
USER_AGENT = "FX-GLITCH/binance-venue"
MAX_KLINES_PER_CALL = 1500

INTERVALS = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h",
    "12h": "12h", "1d": "1d", "3d": "3d", "1w": "1w", "1M": "1M",
}

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000,
    "1w": 604_800_000, "1M": 2_592_000_000,
}


def sign_request(secret_key: str, query: dict) -> str:
    """Return Binance's HMAC-SHA256 signature for a query string."""
    encoded = urllib.parse.urlencode(query)
    return hmac.new(secret_key.encode(), encoded.encode(), hashlib.sha256).hexdigest()


def _parse(raw: str) -> dict | list:
    try:
        return json.loads(raw)
    except ValueError:
        raise VenueError("binance", "malformed", "expected JSON from Binance") from None


def _urlopen_transport(method: str, url: str, headers: dict, body: str | None):
    data = body.encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return _parse(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return _parse(raw)
        except VenueError:
            raise
        except ValueError:
            raise VenueError("binance", exc.code, raw[:160], retryable=exc.code >= 500) from None
    except urllib.error.URLError as exc:
        raise VenueError("binance", "network", str(exc.reason), retryable=True) from None


class Binance(Venue):
    """Binance USDⓈ-M futures client with injectable transport for tests."""

    name = "binance"
    quote_currency = "USDT"

    def __init__(self, *, api_key: str | None = None, secret_key: str | None = None,
                 base_url: str | None = None, transport=_urlopen_transport) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("BINANCE_API_KEY", "")
        self._secret = secret_key if secret_key is not None else os.environ.get("BINANCE_SECRET_KEY", "")
        self.base_url = (base_url or os.environ.get("BINANCE_FUTURES_BASE_URL", BASE_URL)).rstrip("/")
        self._transport = transport
        self._instruments: dict[str, Instrument] | None = None
        self._hedge_mode: bool | None = None

    @property
    def authenticated(self) -> bool:
        return bool(self.api_key and self._secret)

    def _require_keys(self) -> None:
        if not self.authenticated:
            raise VenueError(
                "binance", "no-credentials",
                "BINANCE_API_KEY and BINANCE_SECRET_KEY are not set. "
                "Public market data works without them; trading does not.",
            )

    def _request(self, method: str, path: str, *, query: dict | None = None,
                 private: bool = False):
        params = {k: v for k, v in (query or {}).items() if v is not None}
        if private:
            self._require_keys()
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            params["signature"] = sign_request(self._secret, params)

        encoded = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}" + (f"?{encoded}" if encoded else "")
        headers = {"Content-Type": "application/x-www-form-urlencoded",
                   "User-Agent": USER_AGENT}
        if private:
            headers["X-MBX-APIKEY"] = self.api_key
        response = self._transport(method, url, headers, None)
        if isinstance(response, dict) and response.get("code", 0) not in (0, "0"):
            code = response.get("code")
            raise VenueError("binance", code, str(response.get("msg", "")),
                             retryable=code in (-1001, -1003, -1007))
        return response

    @staticmethod
    def _precision(raw: str | int | None, fallback: int) -> int:
        value = Decimal(str(raw or "0"))
        if value <= 0:
            return fallback
        return max(0, -value.as_tuple().exponent)

    def instruments(self, refresh: bool = False) -> dict[str, Instrument]:
        if self._instruments is not None and not refresh:
            return self._instruments
        rows = self._request("GET", "/fapi/v1/exchangeInfo").get("symbols", [])
        out: dict[str, Instrument] = {}
        for row in rows:
            filters = {item["filterType"]: item for item in row.get("filters", [])}
            lot = filters.get("LOT_SIZE", {})
            price = filters.get("PRICE_FILTER", {})
            step = lot.get("stepSize", "0")
            tick = price.get("tickSize", "0")
            out[row["symbol"]] = Instrument(
                symbol=row["symbol"], base=row.get("baseAsset", ""),
                quote=row.get("quoteAsset", ""),
                qty_precision=self._precision(step, int(row.get("quantityPrecision", 0))),
                price_precision=self._precision(tick, int(row.get("pricePrecision", 0))),
                min_qty=Decimal(str(lot.get("minQty", "0"))),
                max_qty=(Decimal(str(lot["maxQty"])) if lot.get("maxQty") else None),
                max_leverage=int(row.get("maxLeverage", 125) or 125),
                tradeable=row.get("status") == "TRADING" and row.get("contractType") == "PERPETUAL",
            )
        self._instruments = out
        return out

    def universe(self, quote: str = "USDT") -> list[str]:
        symbols = [s for s, i in self.instruments().items()
                   if i.tradeable and i.quote.upper() == quote.upper()]
        lead = f"BTC{quote.upper()}"
        symbols.sort(key=lambda s: (s != lead, s))
        return symbols

    def candles(self, symbol: str, interval: str = "1d", limit: int = 200) -> list[Candle]:
        if interval not in INTERVALS:
            raise ValueError(f"Unknown interval {interval!r}. Use one of: {', '.join(INTERVALS)}")
        step = INTERVAL_MS[interval]
        now_ms = int(time.time() * 1000)
        collected: dict[int, Candle] = {}
        end = now_ms
        while len(collected) < limit:
            want = min(MAX_KLINES_PER_CALL, limit - len(collected))
            rows = self._request("GET", "/fapi/v1/klines", query={
                "symbol": symbol, "interval": INTERVALS[interval], "limit": want,
                "startTime": end - step * want, "endTime": end,
            })
            if not rows:
                break
            before = len(collected)
            for row in rows:
                candle = self._to_candle(row)
                collected[int(candle.time.timestamp() * 1000)] = candle
            if len(collected) == before:
                break
            end = min(collected) - 1
        ordered = [collected[key] for key in sorted(collected)]
        if ordered:
            last = int(ordered[-1].time.timestamp() * 1000)
            if now_ms < last + step:
                ordered.pop()
        return ordered[-limit:]

    @staticmethod
    def _to_candle(row: list) -> Candle:
        return Candle(
            time=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
            open=float(row[1]), high=float(row[2]), low=float(row[3]),
            close=float(row[4]), volume=float(row[5]),
        )

    def balance(self, asset: str = "USDT") -> Balance:
        rows = self._request("GET", "/fapi/v2/balance", private=True)
        row = next((item for item in rows if item.get("asset") == asset), None)
        if row is None:
            raise VenueError("binance", "no-account", f"the account has no {asset} futures wallet")
        wallet = float(row.get("balance", 0) or 0)
        available = float(row.get("availableBalance", 0) or 0)
        return Balance(currency=asset, available=available,
                       used=wallet - available,
                       unrealised_pnl=float(row.get("crossUnPnl", 0) or 0))

    def position_mode(self) -> str:
        if self._hedge_mode is None:
            data = self._request("GET", "/fapi/v1/positionSide/dual", private=True)
            self._hedge_mode = bool(data.get("dualSidePosition", False))
        return "HEDGE" if self._hedge_mode else "ONE_WAY"

    def positions(self, symbol: str | None = None) -> list[Position]:
        rows = self._request("GET", "/fapi/v2/positionRisk",
                             query={"symbol": symbol}, private=True)
        out = []
        for row in rows or []:
            amount = float(row.get("positionAmt", 0) or 0)
            if amount == 0:
                continue
            side = str(row.get("positionSide", "")).upper()
            direction = LONG if side == "LONG" or (side not in ("LONG", "SHORT") and amount > 0) else SHORT
            qty = abs(amount)
            liq = float(row.get("liquidationPrice", 0) or 0)
            opened = int(row.get("updateTime", 0) or 0)
            out.append(Position(
                symbol=row["symbol"], direction=direction, qty=qty,
                entry_price=float(row.get("entryPrice", 0) or 0),
                venue_id=f"{row['symbol']}:{side or ('LONG' if direction == LONG else 'SHORT')}",
                leverage=int(row.get("leverage", 1) or 1),
                unrealised_pnl=float(row.get("unRealizedProfit", 0) or 0),
                liquidation_price=liq if liq > 0 else None,
                opened_at=(datetime.fromtimestamp(opened / 1000, tz=timezone.utc) if opened else None),
            ))
        return out

    def _order(self, params: dict) -> dict:
        encoded = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        # Binance order endpoints are POST, but signed parameters are accepted
        # in the query string and this keeps the injected transport simple.
        return self._request("POST", "/fapi/v1/order", query=dict(
            urllib.parse.parse_qsl(encoded)), private=True)

    def _protective_order(self, order: OrderRequest, instrument: Instrument,
                          kind: str, price: Decimal) -> dict:
        """Place a close-position stop or target for an existing position."""
        params = {
            "symbol": order.symbol,
            "side": "SELL" if order.direction == LONG else "BUY",
            "type": kind,
            "stopPrice": str(instrument.round_price(price)),
            "closePosition": "true", "workingType": "MARK_PRICE",
            "newClientOrderId": f"{order.client_id}-{'sl' if kind == 'STOP_MARKET' else 'tp'}"
                               if order.client_id else None,
        }
        if self.position_mode() == "HEDGE":
            params["positionSide"] = "LONG" if order.direction == LONG else "SHORT"
        return self._order(params)

    def stops(self, symbol: str | None = None) -> dict[str, float]:
        """Return resting Binance stop prices keyed like ``Position.venue_id``."""
        rows = self._request("GET", "/fapi/v1/openOrders",
                             query={"symbol": symbol}, private=True)
        out: dict[str, float] = {}
        for row in rows or []:
            if row.get("type") not in ("STOP", "STOP_MARKET"):
                continue
            pair = row.get("symbol", symbol)
            side = str(row.get("positionSide", "")).upper()
            if side not in ("LONG", "SHORT"):
                side = "LONG" if row.get("side") == "SELL" else "SHORT"
            raw = row.get("stopPrice")
            if pair and raw not in (None, "", "0"):
                out[f"{pair}:{side}"] = float(raw)
        return out

    def set_stop(self, position: Position, stop_price: Decimal | float) -> OrderResult:
        """Replace the resting stop for a position before the next bar."""
        instrument = self.instruments().get(position.symbol)
        if instrument is None:
            raise VenueError("binance", "unknown-symbol", f"{position.symbol} is not a listed perpetual")
        side = "LONG" if position.direction == LONG else "SHORT"
        for row in self._request("GET", "/fapi/v1/openOrders",
                                 query={"symbol": position.symbol}, private=True) or []:
            row_side = str(row.get("positionSide", "")).upper()
            if row.get("type") in ("STOP", "STOP_MARKET") and \
                    (row_side == side or (not row_side and
                     ((position.direction == LONG and row.get("side") == "SELL") or
                      (position.direction == SHORT and row.get("side") == "BUY")))):
                self._request("DELETE", "/fapi/v1/order",
                              query={"symbol": position.symbol,
                                     "orderId": row.get("orderId")}, private=True)
        request = OrderRequest(position.symbol, position.direction,
                               Decimal(str(position.qty)), client_id=f"trail-{position.venue_id}")
        data = self._protective_order(request, instrument, "STOP_MARKET", Decimal(str(stop_price)))
        return OrderResult(accepted=True, venue_order_id=str(data.get("orderId", "")), raw=data)

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """POST /fapi/v1/leverage — required before a futures market order."""
        return self._request("POST", "/fapi/v1/leverage", query={
            "symbol": symbol, "leverage": int(leverage),
        }, private=True)

    def place(self, order: OrderRequest) -> OrderResult:
        instrument = self.instruments().get(order.symbol)
        if instrument is None:
            raise VenueError("binance", "unknown-symbol", f"{order.symbol} is not a listed perpetual")
        qty = instrument.round_qty(order.qty)
        ok, why = instrument.fits(qty)
        if not ok:
            raise VenueError("binance", "size-rejected", why)
        params = {
            "symbol": order.symbol, "side": "BUY" if order.direction == LONG else "SELL",
            "type": "MARKET" if order.is_market else "LIMIT", "quantity": str(qty),
            "newClientOrderId": order.client_id or None,
        }
        if not order.is_market:
            params.update(price=str(instrument.round_price(order.price)), timeInForce="GTC")
        if self.position_mode() == "HEDGE":
            params["positionSide"] = "LONG" if order.direction == LONG else "SHORT"
        elif order.reduce_only:
            params["reduceOnly"] = "true"
        data = self._order(params)
        protection = {}
        if order.stop_price is not None and not order.reduce_only:
            protection["stop"] = self._protective_order(
                order, instrument, "STOP_MARKET", order.stop_price)
        if order.take_profit is not None and not order.reduce_only:
            protection["takeProfit"] = self._protective_order(
                order, instrument, "TAKE_PROFIT_MARKET", order.take_profit)
        raw = data if isinstance(data, dict) else {}
        if protection:
            raw["protection"] = protection
        return OrderResult(accepted=True, venue_order_id=str(data.get("orderId", "")),
                           client_id=str(data.get("clientOrderId", order.client_id)), raw=raw)

    def close(self, position: Position, *, reason: str = "") -> OrderResult:
        instrument = self.instruments().get(position.symbol)
        qty = instrument.round_qty(position.qty) if instrument else Decimal(str(position.qty))
        return self.place(OrderRequest(
            symbol=position.symbol,
            direction=SHORT if position.direction == LONG else LONG,
            qty=qty, reduce_only=True,
            client_id=f"close-{position.venue_id}" if position.venue_id else "",
            reason=reason,
        ))

"""Bitunix USDT-M perpetual futures - venue one.

Docs: https://www.bitunix.com/api-docs/futures/common/introduction.html
Base: https://fapi.bitunix.com

WHAT IS DIFFERENT ABOUT THIS VENUE, AND WHY EACH THING MATTERS
--------------------------------------------------------------
1. THE STOP CAN LIVE ON THE EXCHANGE. `place_order` accepts `slPrice` and
   `tpPrice` in the same call that opens the position. Use it, always. A stop
   held in this process is a stop that evaporates when the process does, and
   the whole point of a leveraged position is that it is the one thing you
   cannot leave unattended. Sending the stop with the entry means the worst
   case for a crashed bot is an unmanaged position with a floor, not a naked one.

2. KLINES CAP AT 200 BARS PER CALL. A 200-period trend filter therefore cannot
   be fed by one request, so `candles()` pages backwards. Get this wrong and
   the strategy runs on a shorter history than it was tested on - the indicator
   is warm in the backtest and blind live, and nothing in the output tells you.

3. HEDGE MODE vs ONE-WAY. In hedge mode `tradeSide` must be OPEN or CLOSE and
   closing needs the `positionId`. In one-way it must be omitted. The account
   tells you which it is, so we read it rather than assume, and we cache it.

4. SIZE IS IN BASE COIN, NOT CONTRACTS. `qty` for BTCUSDT is in BTC. That makes
   risk sizing pleasantly direct - qty = risk_in_usdt / stop_distance_in_usdt -
   but it also means precision rules bite: `basePrecision` 4 on BTC means the
   smallest step is 0.0001 BTC, and anything below `minTradeVolume` is rejected.

5. THE SIGNATURE IS TWO SHA256 PASSES, NOT HMAC. Nearly every other exchange
   uses HMAC-SHA256, so muscle memory is wrong here. See `sign_request`.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
No retries, no rate limiting, no position sizing, no strategy. It is a typed
wrapper over the wire and nothing else. Retry policy needs to know whether a
duplicate fill is survivable; sizing needs to know the portfolio. Neither is
knowable from here, and guessing at this layer is how a "helpful" client ends
up opening two positions because it retried a request that had actually landed.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

from ..data import Candle
from .base import (
    LONG, SHORT, Balance, Instrument, OrderRequest, OrderResult, Position,
    Venue, VenueError,
)

BASE_URL = "https://fapi.bitunix.com"
# Cloudflare fronts this API and profiles the client. A bare or obviously
# scripted agent gets challenged; a burst of requests gets challenged harder.
# This is not evasion of a rate limit - their documented limit is 10/sec/ip and
# we stay far under it - it is looking like an ordinary HTTP client rather than
# something worth interrogating.
# An honest, identifying agent. Counter-intuitively this passes where a full
# Chrome string does not: claiming to be Chrome without any of the headers a
# real Chrome sends is a mismatch worth challenging, whereas a plainly-labelled
# API client is not pretending to be anything.
USER_AGENT = "Mozilla/5.0 (FX-GLITCH research client)"

# Seconds to leave between requests. Their documented limit is 10/sec/ip and
# this is nowhere near it, because the constraint that actually bites is
# Cloudflare's bot scoring rather than the quota.
#
# MEASURED, the hard way: a scan of 3 symbols at 2 kline pages each - about 8
# requests in 2 seconds - was enough to earn a challenge that then lasted well
# over two minutes and survived a change of user agent. It is an IP-level
# cooldown, and further requests extend it.
#
# The consequence for the platform is architectural, not cosmetic: per-symbol
# scanning does not scale to a 625-symbol universe. Screening has to come from
# the single-call endpoints (get_tickers returns every symbol at once), with
# klines fetched only for the handful that pass. See the note in universe().
MIN_REQUEST_INTERVAL = 0.5

# Their interval strings happen to match ours; mapped explicitly anyway so a
# change on their side becomes a KeyError here rather than an empty result.
INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
    "1d": "1d", "3d": "3d", "1w": "1w", "1M": "1M",
}

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
    "3d": 259_200_000, "1w": 604_800_000, "1M": 2_592_000_000,
}

MAX_KLINES_PER_CALL = 200

# Codes we know are worth trying again. Anything not listed is treated as a
# refusal to be reported, not papered over.
RETRYABLE_CODES = {10002, 10003, 20001}


def sign_request(api_key: str, secret_key: str, nonce: str, timestamp: str,
                 query: dict | None = None, body: str = "") -> str:
    """Bitunix's two-pass SHA256 signature.

        digest = sha256(nonce + timestamp + api_key + sortedQuery + body)
        sign   = sha256(digest + secret_key)

    `sortedQuery` is every query parameter sorted by key in ascending ASCII
    order, then key and value concatenated with NO separators at all - id=1 and
    uid=200 become the string "id1uid200". `body` is the compact JSON with
    spaces stripped, or empty for GET.

    Note this is NOT HMAC. The secret is concatenated into the second hash
    rather than keying it. That is their design; we implement it as specified.
    """
    parts = ""
    if query:
        parts = "".join(f"{k}{query[k]}" for k in sorted(query))
    digest = hashlib.sha256((nonce + timestamp + api_key + parts + body).encode()).hexdigest()
    return hashlib.sha256((digest + secret_key).encode()).hexdigest()


def _short(raw: str) -> str:
    """One readable line out of whatever came back.

    An HTML error page pasted into a log entry buries the actual problem under
    a screenful of markup, and every symbol in the scan repeats it.
    """
    text = " ".join(raw.split())
    return text[:160] + ("..." if len(text) > 160 else "")


def _parse(raw: str) -> dict:
    """JSON, or a named error for the things that are not JSON.

    Cloudflare answers with an HTML challenge page rather than an API error,
    and reported raw it looks like a broken endpoint or a bad key. It is
    neither - it is a transient block, it clears on its own, and it is
    retryable. Naming it saves the hour spent debugging the wrong thing.
    """
    try:
        return json.loads(raw)
    except ValueError:
        head = raw.lstrip()[:400].lower()
        if "just a moment" in head or "cf-browser-verification" in head or "<!doctype html" in head:
            raise VenueError(
                "bitunix", "cloudflare",
                "blocked by Cloudflare's bot check. The API and your key are "
                "fine - this is a cooldown on the IP, it can last several "
                "minutes, and retrying hard makes it longer. Wait it out.",
                retryable=True,
            ) from None
        raise VenueError("bitunix", "malformed",
                         f"expected JSON, got: {_short(raw)}") from None


def _compact(payload: dict) -> str:
    """JSON with every space removed - what the signature is computed over."""
    return json.dumps(payload, separators=(",", ":"))


def _urlopen_transport(method: str, url: str, headers: dict, body: str | None) -> dict:
    data = body.encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            return _parse(raw)
    except urllib.error.HTTPError as exc:
        # Their errors arrive as a JSON body under a non-200 status; the body is
        # far more useful than the status, so surface it rather than the code.
        raw = exc.read().decode(errors="replace")
        try:
            return _parse(raw)
        except VenueError:
            raise
        except ValueError:
            raise VenueError("bitunix", exc.code, _short(raw),
                             retryable=exc.code >= 500) from None
    except urllib.error.URLError as exc:
        raise VenueError("bitunix", "network", str(exc.reason), retryable=True) from None


class Bitunix(Venue):
    """Bitunix futures. Credentials come from the environment, never arguments.

    BITUNIX_API_KEY and BITUNIX_SECRET_KEY. They are read here, used to sign,
    and never logged, echoed or written anywhere. Public endpoints work without
    them, so market data and the symbol universe are available before you have
    created a key at all - build and watch first, authenticate later.
    """

    name = "bitunix"
    quote_currency = "USDT"

    def __init__(self, *, api_key: str | None = None, secret_key: str | None = None,
                 base_url: str = BASE_URL, transport=_urlopen_transport) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("BITUNIX_API_KEY", "")
        self._secret = secret_key if secret_key is not None else os.environ.get("BITUNIX_SECRET_KEY", "")
        self.base_url = base_url.rstrip("/")
        # Injected so the whole client is testable without a network or a key.
        self._transport = transport
        self._instruments: dict[str, Instrument] | None = None
        self._position_mode: str | None = None
        self._last_request_at = 0.0

    # --- plumbing ------------------------------------------------------

    @property
    def authenticated(self) -> bool:
        return bool(self.api_key and self._secret)

    def _require_keys(self) -> None:
        if not self.authenticated:
            raise VenueError(
                "bitunix", "no-credentials",
                "BITUNIX_API_KEY and BITUNIX_SECRET_KEY are not set in the "
                "environment. Public market data works without them; trading "
                "does not.",
            )

    def _request(self, method: str, path: str, *, query: dict | None = None,
                 payload: dict | None = None, private: bool = False) -> dict:
        query = {k: v for k, v in (query or {}).items() if v is not None}
        query = {k: str(v) for k, v in query.items()}
        body = _compact(payload) if payload is not None else ""

        url = f"{self.base_url}{path}"
        if query:
            url += "?" + "&".join(f"{k}={query[k]}" for k in sorted(query))

        # Cloudflare sits in front of fapi.bitunix.com and answers the default
        # Python-urllib agent with a 403 "error code: 1010" before the API ever
        # sees the request. That failure looks exactly like a rejected key, so
        # it is worth knowing it is not one.
        headers = {"Content-Type": "application/json", "language": "en-US",
                   "User-Agent": USER_AGENT}
        if private:
            self._require_keys()
            nonce = secrets.token_hex(16)
            timestamp = str(int(time.time() * 1000))
            headers.update({
                "api-key": self.api_key,
                "nonce": nonce,
                "timestamp": timestamp,
                "sign": sign_request(self.api_key, self._secret, nonce, timestamp,
                                     query, body),
            })

        self._pace()
        response = self._transport(method, url, headers, body or None)
        return self._unwrap(response)

    def _pace(self) -> None:
        """Leave a gap between requests.

        A scan across many symbols is a burst, and a burst is what gets
        challenged. Sleeping a quarter second costs nothing on a daily
        strategy and keeps the scan from being interrupted halfway through.
        """
        gap = time.monotonic() - self._last_request_at
        if gap < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - gap)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _unwrap(response: dict) -> dict:
        """Bitunix returns HTTP 200 with code != 0 for business errors.

        Which means a naive client that only checks the status code will treat
        'insufficient margin' as a successful order. Every response goes
        through here.
        """
        if not isinstance(response, dict):
            raise VenueError("bitunix", "malformed", f"expected an object, got {type(response).__name__}")
        code = response.get("code")
        if code not in (0, "0", None):
            raise VenueError("bitunix", code, str(response.get("msg", "")),
                             retryable=code in RETRYABLE_CODES)
        return response.get("data", response)

    # --- market data (public, no key needed) ---------------------------

    def instruments(self, refresh: bool = False) -> dict[str, Instrument]:
        """Every listed perpetual and its trading rules.

        Cached, because it changes when Bitunix lists something and not
        otherwise, and every sizing decision needs it.
        """
        if self._instruments is not None and not refresh:
            return self._instruments

        rows = self._request("GET", "/api/v1/futures/market/trading_pairs")
        out: dict[str, Instrument] = {}
        for row in rows or []:
            symbol = row["symbol"]
            out[symbol] = Instrument(
                symbol=symbol,
                base=row.get("base", ""),
                quote=row.get("quote", ""),
                qty_precision=int(row.get("basePrecision", 4)),
                price_precision=int(row.get("quotePrecision", 2)),
                min_qty=Decimal(str(row.get("minTradeVolume", "0"))),
                max_qty=(Decimal(str(row["maxMarketOrderVolume"]))
                         if row.get("maxMarketOrderVolume") else None),
                max_leverage=int(row.get("maxLeverage", 1)),
                # Both flags matter: a pair can be listed and open while still
                # refusing API orders, and finding that out from a rejection is
                # a wasted signal.
                tradeable=(row.get("symbolStatus") == "OPEN"
                           and bool(row.get("isApiSupported", True))),
            )
        self._instruments = out
        return out

    def universe(self, quote: str = "USDT") -> list[str]:
        """Tradeable symbols quoted in `quote`, BTC first.

        BTC leads the list on purpose. It is the market's driver, so anything
        that scans symbols in order gets BTC's read before it judges an alt.
        """
        syms = [s for s, i in self.instruments().items()
                if i.tradeable and i.quote.upper() == quote.upper()]
        lead = f"BTC{quote.upper()}"
        syms.sort(key=lambda s: (s != lead, s))
        return syms

    def tickers(self, symbols: list[str] | None = None):
        """Every symbol's price and 24h stats in ONE request.

        This is the endpoint that makes a 625-symbol universe tractable. The
        alternative - a kline call per symbol - is 625 requests, and roughly
        eight in two seconds is enough to earn a Cloudflare cooldown lasting
        minutes. One call here, then klines for the handful that survive.

        Field names are resolved defensively in screener.py, because the doc
        page for this endpoint sits behind the same bot check that blocks the
        client and could not be read end to end.
        """
        from ..live.screener import parse_tickers
        query = {"symbols": ",".join(symbols)} if symbols else None
        return parse_tickers(self._request(
            "GET", "/api/v1/futures/market/tickers", query=query))

    def candles(self, symbol: str, interval: str = "1d", limit: int = 200) -> list[Candle]:
        """Closed candles, oldest first.

        Pages backwards when `limit` exceeds the 200-bar cap, because a
        strategy with a 200-period filter needs more than 200 bars to have a
        warm indicator on the bar it is actually deciding on.

        The most recent bar is dropped when it is still forming. That bar's
        high and low are not final, and handing it to a strategy is lookahead:
        the backtest only ever saw finished bars.
        """
        if interval not in INTERVALS:
            raise ValueError(f"Unknown interval {interval!r}. "
                             f"Use one of: {', '.join(INTERVALS)}")

        step = INTERVAL_MS[interval]
        now_ms = int(time.time() * 1000)
        collected: dict[int, Candle] = {}
        end = now_ms

        while len(collected) < limit:
            want = min(MAX_KLINES_PER_CALL, limit - len(collected))
            rows = self._request("GET", "/api/v1/futures/market/kline", query={
                "symbol": symbol,
                "interval": INTERVALS[interval],
                "limit": want,
                "startTime": end - step * want,
                "endTime": end,
            })
            if not rows:
                break
            batch = [self._to_candle(r) for r in rows]
            before = len(collected)
            for c in batch:
                collected[int(c.time.timestamp() * 1000)] = c
            if len(collected) == before:
                break  # the venue is repeating itself; stop rather than spin
            end = min(collected) - 1

        ordered = [collected[k] for k in sorted(collected)]

        # Drop the bar that has not closed yet.
        if ordered:
            last_open_ms = int(ordered[-1].time.timestamp() * 1000)
            if now_ms < last_open_ms + step:
                ordered.pop()

        return ordered[-limit:]

    @staticmethod
    def _to_candle(row: dict) -> Candle:
        """Parse one kline row.

        THEIR VOLUME FIELD NAMES ARE INVERTED. Verified against live BTCUSDT
        daily data on 2026-08-22:

            quoteVol   79,674            <- this is BTC. The BASE volume.
            baseVol    6,107,090,225     <- this is USDT. The QUOTE volume.

        79,674 BTC at roughly 76,000 is 6.05bn, which settles it. Trusting the
        names costs you a factor of about 76,000 - and it would not crash, it
        would just quietly make every volume comparison in factors.py wrong
        while still producing plausible-looking ratios.

        We store BASE volume, matching the Binance CSVs the backtests use, so
        that a strategy sees the same units live as it did in testing.
        """
        ms = int(row["time"])
        return Candle(
            time=datetime.fromtimestamp(ms / 1000, tz=timezone.utc),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("quoteVol") or 0.0),
        )

    # --- account (private) ---------------------------------------------

    def balance(self, margin_coin: str = "USDT") -> Balance:
        data = self._request("GET", "/api/v1/futures/account",
                             query={"marginCoin": margin_coin}, private=True)
        if isinstance(data, list):
            # An empty list means the account holds no such margin coin. That
            # is a real answer, not an error - but it must not become an
            # IndexError three frames deep in an order path.
            if not data:
                raise VenueError("bitunix", "no-account",
                                 f"the account has no {margin_coin} futures "
                                 f"wallet - fund it or check the margin coin")
            data = data[0]
        row = data
        if row.get("positionMode"):
            self._position_mode = str(row["positionMode"]).upper()
        return Balance(
            currency=row.get("marginCoin", margin_coin),
            available=float(row.get("available", 0) or 0),
            used=float(row.get("margin", 0) or 0),
            unrealised_pnl=(float(row.get("crossUnrealizedPNL", 0) or 0)
                            + float(row.get("isolationUnrealizedPNL", 0) or 0)),
        )

    def position_mode(self) -> str:
        """HEDGE or ONE_WAY. Read once, then cached.

        It changes how every order must be addressed, so we ask the account
        rather than assuming - an assumption here is wrong silently until the
        first close fails to close anything.
        """
        if self._position_mode is None:
            self.balance()
        return self._position_mode or "ONE_WAY"

    def positions(self, symbol: str | None = None) -> list[Position]:
        rows = self._request("GET", "/api/v1/futures/position/get_pending_positions",
                             query={"symbol": symbol}, private=True)
        out = []
        for row in rows or []:
            qty = float(row.get("qty", 0) or 0)
            if qty == 0:
                continue
            out.append(Position(
                symbol=row["symbol"],
                direction=LONG if str(row.get("side", "")).upper() == "LONG" else SHORT,
                qty=qty,
                entry_price=float(row.get("avgOpenPrice", 0) or 0),
                venue_id=str(row.get("positionId", "")),
                leverage=int(row.get("leverage", 1) or 1),
                unrealised_pnl=float(row.get("unrealizedPNL", 0) or 0),
                liquidation_price=(float(row["liqPrice"])
                                   if float(row.get("liqPrice", 0) or 0) > 0 else None),
                opened_at=(datetime.fromtimestamp(int(row["ctime"]) / 1000, tz=timezone.utc)
                           if row.get("ctime") else None),
            ))
        return out

    # --- trading (private) ---------------------------------------------

    def place(self, order: OrderRequest) -> OrderResult:
        """Send an order, with its stop attached.

        The stop goes in this same call rather than a follow-up, and that is
        not a convenience. Two calls means a window - however short - where the
        position exists and the stop does not. Every unattended blow-up starts
        in a window like that.
        """
        instrument = self.instruments().get(order.symbol)
        if instrument is None:
            raise VenueError("bitunix", "unknown-symbol",
                             f"{order.symbol} is not a listed perpetual")
        ok, why = instrument.fits(order.qty)
        if not ok:
            raise VenueError("bitunix", "size-rejected", why)

        payload = {
            "symbol": order.symbol,
            "qty": str(order.qty),
            "side": "BUY" if order.direction == LONG else "SELL",
            "orderType": "MARKET" if order.is_market else "LIMIT",
        }
        if not order.is_market:
            payload["price"] = str(instrument.round_price(order.price))
            payload["effect"] = "GTC"
        if order.client_id:
            payload["clientId"] = order.client_id
        if order.reduce_only:
            payload["reduceOnly"] = True
        if self.position_mode() == "HEDGE":
            payload["tradeSide"] = "CLOSE" if order.reduce_only else "OPEN"
        if order.stop_price is not None:
            payload["slPrice"] = str(instrument.round_price(order.stop_price))
            payload["slStopType"] = "MARK_PRICE"
            payload["slOrderType"] = "MARKET"
        if order.take_profit is not None:
            payload["tpPrice"] = str(instrument.round_price(order.take_profit))
            payload["tpStopType"] = "MARK_PRICE"
            payload["tpOrderType"] = "MARKET"

        data = self._request("POST", "/api/v1/futures/trade/place_order",
                             payload=payload, private=True)
        return OrderResult(
            accepted=True,
            venue_order_id=str(data.get("orderId", "")),
            client_id=str(data.get("clientId", order.client_id)),
            raw=data if isinstance(data, dict) else {},
        )

    def stops(self, symbol: str | None = None) -> dict[str, float]:
        """Current stop price per positionId, read from the exchange.

        Needed because `positions()` does not report the stop, and a trailing
        stop has to be compared against what is actually resting there. Our own
        memory of it is a belief; this is the record.

        DOC CAVEAT: Cloudflare blocks the tp_sl doc pages to non-browser
        clients, so the field names below come from search results rather than
        a page I could read end to end. Parsing is therefore defensive - an
        unexpected shape yields "no stop known" instead of a crash, and the
        caller treats that as a reason to be careful rather than as zero.
        """
        rows = self._request("GET", "/api/v1/futures/tpsl/get_pending_orders",
                             query={"symbol": symbol}, private=True)
        out: dict[str, float] = {}
        for row in rows or []:
            pid = str(row.get("positionId", "") or "")
            raw = row.get("slPrice") or row.get("stopLossPrice")
            if not pid or raw in (None, "", "0"):
                continue
            try:
                out[pid] = float(raw)
            except (TypeError, ValueError):
                continue
        return out

    def set_stop(self, position: Position, stop_price: Decimal | float) -> OrderResult:
        """Move the stop on an open position.

        Tries modify first, then place. A position opened with `slPrice`
        already has a resting stop to modify, but one opened by hand in the app
        may not, and the difference is not visible from here. Trying both in
        order costs one wasted call in the uncommon case and avoids silently
        failing to protect a position in the other.
        """
        if not position.venue_id:
            raise VenueError("bitunix", "no-position-id",
                             f"cannot move the stop on {position.symbol} without "
                             f"a positionId")
        instrument = self.instruments().get(position.symbol)
        price = (instrument.round_price(stop_price) if instrument
                 else Decimal(str(stop_price)))
        payload = {
            "symbol": position.symbol,
            "positionId": position.venue_id,
            "slPrice": str(price),
            "slStopType": "MARK_PRICE",
        }
        try:
            data = self._request("POST", "/api/v1/futures/tpsl/position/modify_order",
                                 payload=payload, private=True)
        except VenueError:
            data = self._request("POST", "/api/v1/futures/tpsl/position/place_order",
                                 payload=payload, private=True)
        return OrderResult(accepted=True,
                           venue_order_id=str((data or {}).get("orderId", "")),
                           raw=data if isinstance(data, dict) else {})

    def close(self, position: Position, *, reason: str = "") -> OrderResult:
        """Flatten a position with a reduce-only market order in the other direction."""
        instrument = self.instruments().get(position.symbol)
        qty = instrument.round_qty(position.qty) if instrument else Decimal(str(position.qty))
        return self.place(OrderRequest(
            symbol=position.symbol,
            direction=SHORT if position.direction == LONG else LONG,
            qty=qty,
            reduce_only=True,
            client_id=f"close-{position.venue_id}" if position.venue_id else "",
            reason=reason,
        ))

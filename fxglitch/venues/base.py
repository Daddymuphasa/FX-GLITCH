"""The contract every venue implements.

THE POINT OF THIS FILE
----------------------
Three venues are planned - Bitunix perps, Deriv forex, DEX Screener tokens -
and they agree on almost nothing. Bitunix has leverage, liquidation prices and
a position id. Deriv has contracts with durations. A DEX has no account at all,
just a wallet and a swap that either lands in a block or does not.

If the strategy layer ever learns which of those it is talking to, the platform
stops being a platform and becomes three bots in a trench coat. So the contract
below is deliberately the *intersection*: what you can ask of any venue that
takes money and gives you exposure.

Everything venue-specific - hedge mode, gas, contract expiry - stays inside the
venue module, behind these types.

WHY THE TYPES LOOK LIKE THIS
----------------------------
`Instrument` exists because every venue silently rejects orders for its own
reasons: too small, too many decimals, wrong tick. Getting that wrong shows up
as a rejected order if you are lucky and a wrongly-sized position if you are
not, so rounding is the venue's job and it is enforced by `Instrument.fits`.

`Position` carries `venue_id` because reconciliation - "does the exchange agree
with what I think I hold" - is the check that stops a restarted process from
opening a second position on top of the first one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

LONG, SHORT = 1, -1


class VenueError(RuntimeError):
    """The venue said no.

    Carries the venue's own code and message rather than flattening them,
    because the difference between 'insufficient balance' and 'symbol halted'
    decides whether the caller should retry, skip the symbol, or stop trading
    entirely.
    """

    def __init__(self, venue: str, code, message: str, *, retryable: bool = False):
        super().__init__(f"[{venue}] {code}: {message}")
        self.venue = venue
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class Instrument:
    """What a venue will and will not accept for one tradeable thing."""

    symbol: str
    base: str
    quote: str
    qty_precision: int          # decimal places allowed on size
    price_precision: int        # decimal places allowed on price
    min_qty: Decimal
    max_qty: Decimal | None = None
    max_leverage: int = 1
    tradeable: bool = True

    def round_qty(self, qty: float | Decimal) -> Decimal:
        """Round size DOWN to what the venue accepts.

        Down, never nearest. Rounding up means risking more than the caller
        asked for, and a risk limit that can be exceeded by a rounding rule is
        not a risk limit.
        """
        step = Decimal(1).scaleb(-self.qty_precision)
        return Decimal(str(qty)).quantize(step, rounding=ROUND_DOWN)

    def round_price(self, price: float | Decimal) -> Decimal:
        step = Decimal(1).scaleb(-self.price_precision)
        return Decimal(str(price)).quantize(step, rounding=ROUND_DOWN)

    def fits(self, qty: Decimal) -> tuple[bool, str]:
        """Would the venue accept this size? Returns (ok, why not)."""
        if not self.tradeable:
            return False, f"{self.symbol} is not tradeable right now"
        if qty <= 0:
            return False, f"size rounded to {qty} - below {self.symbol} precision"
        if qty < self.min_qty:
            return False, f"size {qty} is below the {self.symbol} minimum {self.min_qty}"
        if self.max_qty is not None and qty > self.max_qty:
            return False, f"size {qty} is above the {self.symbol} maximum {self.max_qty}"
        return True, ""


@dataclass(frozen=True)
class Balance:
    currency: str
    available: float        # free to commit to a new position
    used: float             # already committed as margin
    unrealised_pnl: float = 0.0

    @property
    def equity(self) -> float:
        return self.available + self.used + self.unrealised_pnl


@dataclass(frozen=True)
class Position:
    """An open position as the VENUE sees it, not as we remember it.

    The distinction matters. Anything built from local memory is a belief;
    only this is evidence.
    """

    symbol: str
    direction: int              # LONG or SHORT
    qty: float
    entry_price: float
    venue_id: str = ""          # the venue's own handle for it
    leverage: int = 1
    unrealised_pnl: float = 0.0
    liquidation_price: float | None = None
    stop_price: float | None = None
    take_profit: float | None = None
    opened_at: datetime | None = None

    @property
    def side(self) -> str:
        return "LONG" if self.direction == LONG else "SHORT"

    @property
    def notional(self) -> float:
        return self.qty * self.entry_price


@dataclass(frozen=True)
class OrderRequest:
    """An intent to trade, in venue-neutral terms.

    `client_id` is not optional in spirit even though it is in type. It is what
    makes a retry safe: send the same client_id twice and a sane venue gives
    you one position, not two. A process that can crash between 'sent' and
    'confirmed' - which is every process - needs that.
    """

    symbol: str
    direction: int
    qty: Decimal
    reduce_only: bool = False
    price: Decimal | None = None        # None means market
    stop_price: Decimal | None = None   # venue-side stop, survives our process dying
    take_profit: Decimal | None = None
    client_id: str = ""
    reason: str = ""                    # why the strategy wanted this; for the log

    @property
    def is_market(self) -> bool:
        return self.price is None


@dataclass(frozen=True)
class OrderResult:
    accepted: bool
    venue_order_id: str = ""
    client_id: str = ""
    message: str = ""
    raw: dict = field(default_factory=dict)


class Venue:
    """Implement these six methods and the rest of the platform can trade you.

    Everything here is synchronous and may raise VenueError. Nothing here
    retries on its own - retry policy belongs to the caller, which is the only
    layer that knows whether a duplicate fill would be a disaster.
    """

    name = "unnamed"
    quote_currency = "USDT"

    def instruments(self) -> dict[str, Instrument]:
        """Every tradeable symbol, keyed by symbol. The universe."""
        raise NotImplementedError

    def candles(self, symbol: str, interval: str, limit: int = 200) -> list:
        """Closed candles, oldest first, as fxglitch.data.Candle.

        Must return CLOSED candles only. A live in-progress bar handed to a
        strategy is lookahead with extra steps: the rules fire on a high that
        has not finished being a high.
        """
        raise NotImplementedError

    def balance(self) -> Balance:
        raise NotImplementedError

    def positions(self, symbol: str | None = None) -> list[Position]:
        raise NotImplementedError

    def place(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    def close(self, position: Position, *, reason: str = "") -> OrderResult:
        raise NotImplementedError

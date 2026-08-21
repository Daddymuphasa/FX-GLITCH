"""The backtest engine.

Three rules are baked in here, because breaking them is how people end up with
a beautiful equity curve and an empty account:

1. NO LOOKAHEAD. A signal decided on the close of bar `i` is filled at the
   OPEN of bar `i+1`. You cannot trade a candle you have not finished watching.
2. WORST CASE WINS. If a bar's high and low would have hit both the take
   profit and the stop loss, the engine records the STOP. Real life is not
   generous about which came first.
3. COSTS ARE REAL. Spread is charged on entry and on exit, every trade.

If a backtest still looks good after all three, it is worth a demo account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from typing import TYPE_CHECKING

from .data import Candle, Series

if TYPE_CHECKING:
    from .signals import SignalFeed

LONG, SHORT = 1, -1


@dataclass(slots=True)
class Trade:
    """A completed round trip."""

    direction: int
    entry_time: datetime
    entry_price: float
    size: float
    sl: float | None
    tp: float | None
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    entry_reason: str = ""
    risk_amount: float = 0.0  # cash we were prepared to lose = 1R

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.direction * self.size

    @property
    def r_multiple(self) -> float:
        """Profit in units of risk. +2R means you made twice what you risked."""
        if self.risk_amount <= 0:
            return 0.0
        return self.pnl / self.risk_amount

    @property
    def side(self) -> str:
        return "LONG" if self.direction == LONG else "SHORT"


@dataclass
class Order:
    """A market order queued for the next bar's open."""

    direction: int
    sl: float | None = None
    tp: float | None = None
    risk_pct: float | None = None
    reason: str = ""


class Strategy:
    """Subclass this. Two methods matter.

        prepare()  - runs once. Precompute indicators over self.candles.
        on_bar(i)  - runs on every bar. Call self.buy()/self.sell()/self.close().

    Inside on_bar you may look at self.candles[0..i] and nothing beyond.
    """

    name = "unnamed"
    params: dict = {}

    # Filled in by the engine before prepare() is called.
    candles: Series
    position: Trade | None
    feed: "SignalFeed | None" = None   # non-price evidence, point-in-time safe

    _pending: Order | None = None
    _close_request: str | None = None

    def prepare(self) -> None:  # optional hook
        pass

    def on_bar(self, i: int) -> None:
        raise NotImplementedError("Your strategy must implement on_bar(i).")

    # --- actions available inside on_bar -------------------------------

    def buy(self, sl: float | None = None, tp: float | None = None,
            risk_pct: float | None = None, reason: str = "") -> None:
        self._pending = Order(LONG, sl, tp, risk_pct, reason)

    def sell(self, sl: float | None = None, tp: float | None = None,
             risk_pct: float | None = None, reason: str = "") -> None:
        self._pending = Order(SHORT, sl, tp, risk_pct, reason)

    def close(self, reason: str = "signal") -> None:
        """Exit the open position at the next bar's open."""
        self._close_request = reason


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    trades: list[Trade]
    equity_curve: list[float]
    times: list[datetime]
    starting_equity: float
    params: dict = field(default_factory=dict)

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else self.starting_equity


class Backtest:
    def __init__(
        self,
        candles: Series,
        strategy: Strategy,
        *,
        symbol: str = "?",
        starting_equity: float = 1000.0,
        risk_pct: float = 1.0,
        spread: float = 0.0,
        fee_pct: float = 0.0,
        slippage_pct: float = 0.0,
        max_bars_in_trade: int | None = None,
        feed: "SignalFeed | None" = None,
    ) -> None:
        """
        risk_pct     - percent of CURRENT equity risked per trade (1.0 means 1%).
        spread       - ABSOLUTE, in price units. Charged on entry and exit.
                       This is the forex/CFD cost model: XAUUSD at 0.20-0.30.
        fee_pct      - PERCENT of notional, per side. This is the crypto cost
                       model: Binance spot taker is 0.10, futures taker 0.04.
                       A round trip therefore costs 2x this.
        slippage_pct - percent per side for the gap between the price you saw
                       and the price you got. On liquid BTC 0.01-0.02 is fair;
                       on a thin altcoin it can dwarf the fee.

        The two cost models stack, so you can use whichever fits the market -
        or both. Getting this wrong is the single most common reason a
        profitable backtest turns into a losing account: a strategy taking 300
        trades a year at 0.1% per side is paying 60% of capital in fees before
        it makes a cent.
        """
        if not candles:
            raise ValueError("No candles to backtest.")
        self.candles = candles
        self.strategy = strategy
        self.symbol = symbol
        self.starting_equity = starting_equity
        self.risk_pct = risk_pct
        self.spread = spread
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.max_bars_in_trade = max_bars_in_trade
        self.feed = feed

    def _cost(self, price: float) -> float:
        """Total one-way cost at `price`, expressed in price units."""
        return self.spread / 2.0 + price * (self.fee_pct + self.slippage_pct) / 100.0

    def run(self) -> BacktestResult:
        s = self.strategy
        s.candles = self.candles
        s.feed = self.feed
        s.position = None
        s._pending = None
        s._close_request = None
        s.prepare()

        equity = self.starting_equity
        trades: list[Trade] = []
        curve: list[float] = []
        times: list[datetime] = []
        bars_held = 0

        for i, bar in enumerate(self.candles):
            # 1. Fill anything queued on the previous bar, at THIS bar's open.
            if s._pending is not None and s.position is None:
                trade = self._open_trade(s._pending, bar, equity)
                if trade is not None:
                    s.position = trade
                    bars_held = 0
            s._pending = None

            # 2. Honour a close requested on the previous bar.
            if s._close_request and s.position is not None:
                self._close_trade(s.position, bar.open, bar.time, s._close_request)
                equity += s.position.pnl
                trades.append(s.position)
                s.position = None
            s._close_request = None

            # 3. Did this bar hit the stop or the target?
            if s.position is not None:
                bars_held += 1
                hit = self._check_exit(s.position, bar)
                if hit is None and self.max_bars_in_trade and bars_held >= self.max_bars_in_trade:
                    hit = (bar.close, "time stop")
                if hit is not None:
                    price, reason = hit
                    self._close_trade(s.position, price, bar.time, reason)
                    equity += s.position.pnl
                    trades.append(s.position)
                    s.position = None

            # 4. Let the strategy think, using data up to and including this close.
            s.on_bar(i)

            # 5. Mark to market.
            unrealised = 0.0
            if s.position is not None:
                unrealised = (
                    (bar.close - s.position.entry_price)
                    * s.position.direction
                    * s.position.size
                )
            curve.append(equity + unrealised)
            times.append(bar.time)

            if equity <= 0:
                break  # account is gone; stop pretending

        # Close anything still running, at the last close.
        if s.position is not None:
            last = self.candles[-1]
            self._close_trade(s.position, last.close, last.time, "end of data")
            equity += s.position.pnl
            trades.append(s.position)
            s.position = None

        return BacktestResult(
            strategy=s.name,
            symbol=self.symbol,
            trades=trades,
            equity_curve=curve,
            times=times,
            starting_equity=self.starting_equity,
            params=dict(s.params),
        )

    # --- internals -----------------------------------------------------

    def _open_trade(self, order: Order, bar: Candle, equity: float) -> Trade | None:
        entry = bar.open + self._cost(bar.open) * order.direction

        if order.sl is None:
            return None  # no stop, no trade - we do not size blind bets here

        stop_distance = abs(entry - order.sl)
        if stop_distance <= 0:
            return None

        risk_pct = order.risk_pct if order.risk_pct is not None else self.risk_pct
        risk_amount = equity * (risk_pct / 100.0)
        size = risk_amount / stop_distance

        return Trade(
            direction=order.direction,
            entry_time=bar.time,
            entry_price=entry,
            size=size,
            sl=order.sl,
            tp=order.tp,
            entry_reason=order.reason,
            risk_amount=risk_amount,
        )

    def _check_exit(self, t: Trade, bar: Candle) -> tuple[float, str] | None:
        """Stop loss is checked first, on purpose. See rule 2 at the top."""
        if t.direction == LONG:
            if t.sl is not None and bar.low <= t.sl:
                return t.sl, "stop loss"
            if t.tp is not None and bar.high >= t.tp:
                return t.tp, "take profit"
        else:
            if t.sl is not None and bar.high >= t.sl:
                return t.sl, "stop loss"
            if t.tp is not None and bar.low <= t.tp:
                return t.tp, "take profit"
        return None

    def _close_trade(self, t: Trade, price: float, when: datetime, reason: str) -> None:
        t.exit_price = price - self._cost(price) * t.direction
        t.exit_time = when
        t.exit_reason = reason

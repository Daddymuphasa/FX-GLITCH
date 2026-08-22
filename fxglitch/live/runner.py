"""The loop: closed bar in, order out.

THE ONE RULE
------------
The strategy classes in strategies/ are imported and driven exactly as the
backtester drives them. Same `prepare()`, same `on_bar(i)`, same `self.position`,
same `buy()`/`sell()`/`close()`. Nothing is reimplemented for live.

That is not tidiness. The backtest is the only evidence that any of this is
worth running, and it is evidence about a specific piece of code. A live
reimplementation - even a careful one - is a different piece of code, and the
evidence quietly stops applying to the thing holding your money.

HOW ONE CYCLE WORKS
-------------------
For each symbol, per closed bar:

    1. fetch candles, drop the bar still forming
    2. skip if we already decided on this bar (survives restarts)
    3. ask the VENUE what we hold - never our own memory
    4. hand the strategy that position as an engine.Trade, so it sees exactly
       the type it saw in testing
    5. prepare(), then on_bar(last)
    6. read what it wants: an entry, an exit, or a moved stop
    7. run the guards, size it, send it

WHERE LIVE AND BACKTEST GENUINELY DIFFER, AND WHY IT IS ACCEPTABLE
------------------------------------------------------------------
The backtest fills a signal at the NEXT bar's open. Live, we decide at the
close of a bar and send a market order within seconds - which is the next bar's
open, near enough, and is the closest honest analogue available. The difference
is real but small, and it is in the conservative direction: the backtest gets a
clean open price, live gets a fill with slippage.

The other difference is the stop. In the backtest the stop is checked against
each bar's high and low. Live it rests on the exchange as a MARK_PRICE trigger,
so it can fire on a wick the backtest would have priced differently. That is
also the right way round: the exchange enforcing the stop is the property that
makes an unattended position survivable.

WHAT THIS MODULE DOES NOT DECIDE
--------------------------------
Which symbols to scan, and how much of the account any one of them deserves.
That is the portfolio layer, and it does not exist yet. Until it does,
`Limits.max_positions` is the crude stand-in, and the honest description is
that this runs a few uncorrelated-ish symbols rather than a portfolio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from ..data import Candle
from ..engine import LONG, SHORT, Strategy, Trade
from ..store import CandleStore
from ..venues.base import (
    Balance, OrderRequest, Position, Venue, VenueError,
)
from . import guards, portfolio, regime as regime_mod
from .guards import Limits, Verdict
from .portfolio import ExposureLimits
from .regime import Regime
from .screener import ScreenRules, parse_tickers, screen, summarise
from .state import State, client_id

log = logging.getLogger("fxglitch.live")

INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
    "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400,
    "3d": 259200, "1w": 604800,
}


@dataclass
class Decision:
    """What the runner concluded for one symbol on one bar. The audit trail.

    Every cycle produces one of these per symbol whether or not anything
    happened, because "nothing happened" has causes worth reading: no signal,
    a guard refused, the bar was already handled. A log that only records
    actions cannot answer "why didn't it take that trade", which is the
    question you will actually have.
    """

    symbol: str
    bar_time: datetime
    action: str = "none"        # none | enter | exit | trail | blocked
    detail: str = ""
    sent: bool = False
    order_id: str = ""

    def __str__(self) -> str:
        mark = "SENT" if self.sent else "    "
        return (f"{mark} {self.bar_time:%Y-%m-%d %H:%M} {self.symbol:<14} "
                f"{self.action:<8} {self.detail}")


def position_as_trade(position: Position, stop: float | None) -> Trade:
    """Present a venue position to the strategy as the type it was tested with.

    Strategies read `self.position.direction`, mutate `self.position.sl` to
    trail, and check `self.position.entry_price`. In the backtest that object
    is an engine.Trade, so live it is an engine.Trade too - built from what the
    exchange reports rather than from anything we remember.

    Reusing the real type instead of a look-alike matters: a shim with the
    right attributes today drifts from Trade the first time someone adds a
    field to Trade, and the failure is silent.
    """
    return Trade(
        direction=position.direction,
        entry_time=position.opened_at or datetime.now(timezone.utc),
        entry_price=position.entry_price,
        size=position.qty,
        sl=stop,
        tp=position.take_profit,
        entry_reason="live",
        risk_amount=(abs(position.entry_price - stop) * position.qty
                     if stop else 0.0),
    )


class Runner:
    """Drives one strategy across many symbols on one venue.

    Read-only until `live=True`. In dry-run every decision is computed in full
    - guards, sizing, the exact order that would be sent - and then not sent.
    That is the mode to leave it in for a few weeks: the interesting question
    is not whether it can place an order, it is whether the orders it wants to
    place are the ones you would have placed.
    """

    def __init__(self, venue: Venue, strategy_class, symbols: list[str], *,
                 interval: str = "1d", params: dict | None = None,
                 limits: Limits | None = None, state: State | None = None,
                 history: int = 400, live: bool = False, feed=None,
                 paper_equity: float = 1000.0,
                 screen_rules: "ScreenRules | None" = None,
                 exposure_limits: "ExposureLimits | None" = None,
                 driver: str = "BTCUSDT", regime_period: int = 50,
                 use_gate: bool = True,
                 store: "CandleStore | None" = None) -> None:
        self.venue = venue
        self.strategy_class = strategy_class
        self.symbols = symbols
        self.interval = interval
        self.params = params or {}
        self.limits = limits or Limits()
        self.state = state or State()
        self.history = history
        self.live = live
        self.feed = feed
        self.paper_equity = paper_equity
        self.screen_rules = screen_rules
        self.exposure_limits = exposure_limits or ExposureLimits()
        self.driver = driver
        self.regime_period = regime_period
        self.use_gate = use_gate
        self.regime = Regime(regime_mod.NEUTRAL, detail="not measured yet")
        # With a store, each cycle fetches only the bars that are new. Without
        # one, every cycle re-downloads the full history for every symbol,
        # which is what makes a ten-symbol scan expensive enough to get
        # rate-limited.
        self.store = store

    def _account(self) -> tuple[Balance, list[Position]]:
        """The account, or a plausible stand-in for one.

        Dry-run has to work with no API key at all. Balance and positions are
        private endpoints, so requiring them would mean the recommended first
        step - watch it decide for a few weeks before trusting it - needs a key
        that can trade. That gets the order exactly backwards, and in practice
        it means nobody does the watching.

        With keys present we use the real account even in dry-run, because
        seeing a trade refused for want of margin is worth more than seeing it
        pass against an imaginary balance.
        """
        if getattr(self.venue, "authenticated", True):
            return self.venue.balance(), self.venue.positions()
        return Balance(self.venue.quote_currency, self.paper_equity, 0.0), []

    # --- one pass over every symbol ------------------------------------

    def cycle(self, now: datetime | None = None) -> list[Decision]:
        """One pass. Returns a Decision per symbol, whether or not it acted."""
        now = now or datetime.now(timezone.utc)

        if self.state.halted:
            log.warning("HALTED: %s", self.state.halt_reason)
            return [Decision("-", now, "blocked", f"halted: {self.state.halt_reason}")]

        balance, positions = self._account()
        if self.state.roll_day(balance.equity, now):
            log.info("new trading day, equity anchored at %.2f", balance.equity)
            self.state.save()

        # The daily loss check runs once per cycle, before any symbol, because
        # it is a statement about the account rather than about a trade.
        daily = guards.check_daily_loss(balance, self.state.day_open_equity, self.limits)
        if not daily:
            self.state.halt(f"daily loss: {daily.reason}")
            log.error("HALT %s", daily.reason)
            return [Decision("-", now, "blocked", daily.reason)]

        stops = self._read_stops()
        symbols = self._shortlist()
        self.regime = self._measure_regime()
        if self.use_gate:
            log.info("%s", self.regime.detail or f"BTC regime {self.regime.state}")
        log.info("%s", portfolio.describe(portfolio.measure(positions), balance.equity))

        decisions = []
        for symbol in symbols:
            try:
                decisions.append(self._one(symbol, positions, stops, balance, now))
            except VenueError as exc:
                # One bad symbol must not stop the scan. A delisted pair or a
                # rate-limit on symbol nine is not a reason to skip symbol ten.
                log.error("%s: %s", symbol, exc)
                decisions.append(Decision(symbol, now, "blocked", str(exc)))
        self.state.save()
        return decisions

    def _shortlist(self) -> list[str]:
        """Which symbols get the expensive per-symbol calls this cycle.

        With no screen rules this is just the configured list. With them, one
        request covers the whole universe and only the survivors cost anything
        further - which is the difference between a scan that completes and one
        that earns a bot-check cooldown at symbol eight.
        """
        if self.screen_rules is None:
            return self.symbols
        reader = getattr(self.venue, "tickers", None)
        if reader is None:
            log.warning("venue has no tickers endpoint - falling back to the "
                        "configured symbol list")
            return self.symbols
        try:
            tickers = reader()
        except VenueError as exc:
            # A failed screen must not silently become "trade everything".
            log.error("screen failed (%s) - falling back to the configured list", exc)
            return self.symbols
        chosen = screen(tickers, self.venue.instruments(), self.screen_rules)
        log.info("%s", summarise(tickers, chosen, self.screen_rules))
        return [t.symbol for t in chosen]

    def _candles(self, symbol: str, want: int) -> list[Candle]:
        """Bars for a symbol, through the store when there is one."""
        if self.store is None:
            return self.venue.candles(symbol, self.interval, limit=want)
        return self.store.sync(self.venue, symbol, self.interval, want)

    def _measure_regime(self) -> Regime:
        """Read the driver once per cycle, before judging anything that follows it."""
        if not self.use_gate:
            return Regime(regime_mod.NEUTRAL, detail="gate disabled")
        try:
            bars = self._candles(self.driver, max(self.regime_period + 5, 60))
        except VenueError as exc:
            # Unknown regime must not read as a permissive one. NEUTRAL allows
            # both directions, so failing to read BTC would quietly disable the
            # gate exactly when the market is doing something worth gating on.
            log.error("could not read %s for the regime gate: %s", self.driver, exc)
            return Regime(regime_mod.DOWN,
                          detail=f"could not read {self.driver} - assuming the "
                                 f"defensive state until it can be read")
        return regime_mod.btc_regime(bars, self.regime_period)

    def _read_stops(self) -> dict[str, float]:
        """Resting stops from the venue, falling back to what we last placed."""
        reader = getattr(self.venue, "stops", None)
        if reader is None or not getattr(self.venue, "authenticated", True):
            return {}
        try:
            return reader()
        except VenueError as exc:
            log.warning("could not read resting stops (%s) - falling back to "
                        "our own record, which may be out of date", exc)
            return {}

    # --- one symbol ----------------------------------------------------

    def _one(self, symbol: str, positions: list[Position],
             stops: dict[str, float], balance: Balance,
             now: datetime) -> Decision:
        candles = self._candles(symbol, self.history)
        if not candles:
            return Decision(symbol, now, "blocked", "no candles returned")

        bar = candles[-1]
        if self.state.already_decided(symbol, bar.time):
            return Decision(symbol, bar.time, "none", "already decided on this bar")

        fresh = guards.check_data_fresh(
            bar.time, INTERVAL_SECONDS.get(self.interval, 86400), self.limits, now)
        if not fresh:
            return Decision(symbol, bar.time, "blocked", fresh.reason)

        held = next((p for p in positions if p.symbol == symbol), None)
        known_stop = None
        if held:
            known_stop = stops.get(held.venue_id, self.state.stops.get(symbol))

        strategy = self._drive(symbol, candles, held, known_stop)

        # --- what did it want? ---
        if strategy._close_request and held:
            return self._exit(symbol, held, bar, strategy._close_request)

        if strategy._pending is not None and not held:
            return self._enter(symbol, strategy._pending, candles, positions,
                               balance, bar)

        if held and strategy.position is not None:
            moved = strategy.position.sl
            if moved is not None and moved != known_stop:
                return self._trail(symbol, held, moved, bar, known_stop)

        self.state.mark_decided(symbol, bar.time)
        return Decision(symbol, bar.time, "none",
                        "holding" if held else "no signal")

    def _drive(self, symbol: str, candles: list[Candle], held: Position | None,
               stop: float | None) -> Strategy:
        """Run the strategy over the history exactly as the backtester would.

        A fresh instance each cycle, deliberately. These strategies keep no
        state between bars beyond indicators and the open position, so a fresh
        instance plus prepare() is equivalent to a long-lived one - and it is
        immune to a process that has been up for three weeks accumulating
        something nobody intended.
        """
        strategy = self.strategy_class(**self.params)
        strategy.candles = candles
        strategy.feed = self.feed
        strategy.position = position_as_trade(held, stop) if held else None
        strategy._pending = None
        strategy._close_request = None
        strategy.prepare()
        strategy.on_bar(len(candles) - 1)
        return strategy

    # --- acting --------------------------------------------------------

    def _enter(self, symbol: str, order, candles: list[Candle],
               positions: list[Position], balance: Balance, bar: Candle) -> Decision:
        direction = order.direction
        entry = bar.close                      # the market order fills near here
        stop = order.sl

        allowed, why = regime_mod.gate(self.regime, symbol, direction,
                                       driver=self.driver)
        if self.use_gate and not allowed:
            return self._refuse(symbol, bar, Verdict(False, why))

        verdict = guards.check_all(
            guards.check_capacity(positions, symbol, self.limits),
            guards.check_stop(stop, entry, direction, self.limits),
        )
        if not verdict:
            return self._refuse(symbol, bar, verdict)

        risk_amount = balance.equity * self.limits.risk_pct / 100.0
        distance = abs(entry - stop)
        if distance <= 0:
            return self._refuse(symbol, bar,
                                Verdict(False, "stop distance is zero"))

        instrument = self.venue.instruments()[symbol]
        qty = instrument.round_qty(risk_amount / distance)
        fits, why = instrument.fits(qty)
        if not fits:
            return self._refuse(symbol, bar, Verdict(False, why))

        # Re-derive the risk from the ROUNDED size. Rounding down means the
        # real risk is slightly under the target, and the guard should judge
        # what will actually be sent rather than what we asked for.
        actual_risk = float(qty) * distance
        leverage = min(self.limits.max_leverage, instrument.max_leverage)
        notional = float(qty) * entry
        verdict = guards.check_all(
            guards.check_risk(actual_risk, balance, self.limits),
            guards.check_balance(balance, notional, leverage, self.limits),
            portfolio.check_exposure(portfolio.measure(positions), symbol,
                                     direction, notional, balance.equity,
                                     self.exposure_limits),
        )
        if not verdict:
            return self._refuse(symbol, bar, verdict)

        request = OrderRequest(
            symbol=symbol,
            direction=direction,
            qty=qty,
            stop_price=instrument.round_price(stop),
            take_profit=instrument.round_price(order.tp) if order.tp else None,
            client_id=client_id(symbol, bar.time, "enter"),
            reason=order.reason,
        )
        detail = (f"{'LONG' if direction == LONG else 'SHORT'} {qty} @ ~{entry:,.4f} "
                  f"stop {stop:,.4f} risk {actual_risk:,.2f} - {order.reason}")

        if not self.live:
            self.state.mark_decided(symbol, bar.time)
            return Decision(symbol, bar.time, "enter", f"[dry-run] {detail}")

        result = self.venue.place(request)
        self.state.stops[symbol] = float(stop)
        self.state.mark_decided(symbol, bar.time)
        return Decision(symbol, bar.time, "enter", detail, sent=True,
                        order_id=result.venue_order_id)

    def _exit(self, symbol: str, held: Position, bar: Candle, reason: str) -> Decision:
        detail = f"close {held.side} {held.qty} - {reason}"
        if not self.live:
            self.state.mark_decided(symbol, bar.time)
            return Decision(symbol, bar.time, "exit", f"[dry-run] {detail}")
        result = self.venue.close(held, reason=reason)
        self.state.stops.pop(symbol, None)
        self.state.mark_decided(symbol, bar.time)
        return Decision(symbol, bar.time, "exit", detail, sent=True,
                        order_id=result.venue_order_id)

    def _trail(self, symbol: str, held: Position, new_stop: float,
               bar: Candle, old_stop: float | None) -> Decision:
        """Move a stop, but only ever in the protective direction.

        The strategy is already written to trail one way. This is a second
        check on the same rule, at the boundary where money leaves, because a
        stop that can move against the position converts a bounded loss into an
        unbounded one and no other guard would catch it.
        """
        if old_stop is not None:
            backwards = (held.direction == LONG and new_stop < old_stop) or \
                        (held.direction == SHORT and new_stop > old_stop)
            if backwards:
                return Decision(symbol, bar.time, "blocked",
                                f"refused to move the stop against the position "
                                f"({old_stop:,.4f} -> {new_stop:,.4f})")

        detail = f"stop {old_stop if old_stop is None else f'{old_stop:,.4f}'} -> {new_stop:,.4f}"
        if not self.live:
            self.state.mark_decided(symbol, bar.time)
            return Decision(symbol, bar.time, "trail", f"[dry-run] {detail}")

        setter = getattr(self.venue, "set_stop", None)
        if setter is None:
            return Decision(symbol, bar.time, "blocked",
                            "venue cannot move stops")
        setter(held, new_stop)
        self.state.stops[symbol] = float(new_stop)
        self.state.mark_decided(symbol, bar.time)
        return Decision(symbol, bar.time, "trail", detail, sent=True)

    def _refuse(self, symbol: str, bar: Candle, verdict: Verdict) -> Decision:
        """A guard said no.

        The bar is NOT marked decided. A refusal for lack of margin or a full
        book is a fact about right now, and the next cycle should be free to
        reach a different conclusion. Only actions get marked, because only
        actions must never be repeated.
        """
        if verdict.fatal:
            self.state.halt(verdict.reason)
        return Decision(symbol, bar.time, "blocked", verdict.reason)

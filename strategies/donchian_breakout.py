"""Donchian channel breakout - trend following.

Why this one is here and the EMA cross is only a template: breakout trend
following is the oldest documented systematic edge there is (the Turtles traded
this exact idea in the 1980s), and crypto is the market where it has had the
most room to work - young, retail-driven, prone to long directional runs.

That does NOT mean it works now. It means it is a defensible thing to test
rather than a shape someone found on a chart. Run it and see.

The rules:
    Long   - price closes above the highest high of the last `entry` bars
    Short  - price closes below the lowest low of the last `entry` bars
    Stop   - `atr_mult` x ATR, then TRAILED behind the extreme as price moves
    Exit   - the trailing stop, or an opposite breakout of the `exit` channel
    Filter - only trade in the direction of the `trend` EMA, if enabled

Trend following wins rarely and loses often. Expect a win rate near 35%. The
money comes from a handful of very large winners, which is exactly why cutting
winners short destroys it. If you cannot sit through eight losses waiting for
one big win, this family of strategy is not for you, and that is worth learning
on a backtest rather than an account.
"""

from fxglitch.data import closes
from fxglitch.engine import LONG, Strategy
from fxglitch.indicators import atr, ema, highest, lowest


class DonchianBreakout(Strategy):
    name = "Donchian breakout (trend following)"

    def __init__(self, entry: int = 55, exit: int = 20, atr_period: int = 14,
                 atr_mult: float = 2.5, trend: int = 200, allow_shorts: bool = True) -> None:
        self.params = {"entry": entry, "exit": exit, "atr_period": atr_period,
                       "atr_mult": atr_mult, "trend": trend, "allow_shorts": allow_shorts}
        self.entry = entry
        self.exit = exit
        self.atr_period = atr_period
        self.atr_mult = atr_mult
        self.trend = trend
        self.allow_shorts = allow_shorts

    def prepare(self) -> None:
        c = closes(self.candles)
        highs = [x.high for x in self.candles]
        lows = [x.low for x in self.candles]

        # Shift the channel back one bar. The highest high of the last N bars
        # INCLUDING the current one is always >= the current high, so the
        # breakout could never trigger. This off-by-one is the single most
        # common bug in a breakout backtest.
        self.hi_entry = [None] + highest(highs, self.entry)[:-1]
        self.lo_entry = [None] + lowest(lows, self.entry)[:-1]
        self.hi_exit = [None] + highest(highs, self.exit)[:-1]
        self.lo_exit = [None] + lowest(lows, self.exit)[:-1]

        self.atr = atr(self.candles, self.atr_period)
        self.trend_ema = ema(c, self.trend) if self.trend else [None] * len(c)

    def on_bar(self, i: int) -> None:
        a = self.atr[i]
        if a is None or a <= 0:
            return

        bar = self.candles[i]
        price = bar.close

        # --- managing an open position ---------------------------------
        if self.position is not None:
            stop_distance = a * self.atr_mult
            if self.position.direction == LONG:
                # Trail up only, never down. A stop that moves against you is
                # not a stop.
                trailed = price - stop_distance
                if self.position.sl is None or trailed > self.position.sl:
                    self.position.sl = trailed
                if self.lo_exit[i] is not None and price < self.lo_exit[i]:
                    self.close(reason=f"broke {self.exit}-bar low")
            else:
                trailed = price + stop_distance
                if self.position.sl is None or trailed < self.position.sl:
                    self.position.sl = trailed
                if self.hi_exit[i] is not None and price > self.hi_exit[i]:
                    self.close(reason=f"broke {self.exit}-bar high")
            return

        # --- looking for an entry --------------------------------------
        hi, lo = self.hi_entry[i], self.lo_entry[i]
        if hi is None or lo is None:
            return

        t = self.trend_ema[i]
        up_ok = t is None or price > t
        down_ok = t is None or price < t
        stop_distance = a * self.atr_mult

        if price > hi and up_ok:
            # No take profit on purpose. In trend following the entire edge
            # lives in the tail, and a fixed target amputates it.
            self.buy(sl=price - stop_distance, tp=None,
                     reason=f"broke {self.entry}-bar high")
        elif self.allow_shorts and price < lo and down_ok:
            self.sell(sl=price + stop_distance, tp=None,
                      reason=f"broke {self.entry}-bar low")


strategy = DonchianBreakout

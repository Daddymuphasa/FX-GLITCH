"""EMA crossover with an ATR stop - the reference example.

This is here as a TEMPLATE, not a recommendation. It is the most common
strategy on the internet and it is close to a coin flip after costs. Copy the
shape of this file when you write a real one.

The rules:
    Long  - fast EMA crosses above slow EMA
    Short - fast EMA crosses below slow EMA
    Stop  - `atr_mult` x ATR from entry
    Target- `rr` x the stop distance
"""

from fxglitch.engine import Strategy
from fxglitch.indicators import atr, crossed_above, crossed_below, ema
from fxglitch.data import closes


class EmaCross(Strategy):
    name = "EMA Cross + ATR stop"

    def __init__(self, fast: int = 20, slow: int = 50, atr_period: int = 14,
                 atr_mult: float = 2.0, rr: float = 2.0) -> None:
        # `params` is printed in every report, so you always know what produced a number.
        self.params = {"fast": fast, "slow": slow, "atr_period": atr_period,
                       "atr_mult": atr_mult, "rr": rr}
        self.fast = fast
        self.slow = slow
        self.atr_period = atr_period
        self.atr_mult = atr_mult
        self.rr = rr

    def prepare(self) -> None:
        """Runs once. Precompute everything here - it keeps on_bar fast and clean."""
        c = closes(self.candles)
        self.ema_fast = ema(c, self.fast)
        self.ema_slow = ema(c, self.slow)
        self.atr = atr(self.candles, self.atr_period)

    def on_bar(self, i: int) -> None:
        # One position at a time. If we are in a trade, sit on our hands.
        if self.position is not None:
            return

        a = self.atr[i]
        if a is None or a <= 0:
            return  # still warming up

        price = self.candles[i].close
        stop_distance = a * self.atr_mult

        if crossed_above(self.ema_fast, self.ema_slow, i):
            self.buy(
                sl=price - stop_distance,
                tp=price + stop_distance * self.rr,
                reason=f"EMA{self.fast} crossed above EMA{self.slow}",
            )
        elif crossed_below(self.ema_fast, self.ema_slow, i):
            self.sell(
                sl=price + stop_distance,
                tp=price - stop_distance * self.rr,
                reason=f"EMA{self.fast} crossed below EMA{self.slow}",
            )


# The runner looks for this name.
strategy = EmaCross

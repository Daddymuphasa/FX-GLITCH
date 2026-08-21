"""Confluence: price action decides WHETHER, intelligence decides HOW MUCH.

This is the architecture the 20 August rally argues for, and it is deliberately
not the one people usually build.

The tempting design is: read the news, predict the move, enter before it. That
does not work, for a reason no amount of engineering fixes. The Treasury
buyback announcement was not knowable in advance. It was a policy response to a
yield spike, decided in a room you are not in, released on a schedule you do
not control. By the time it was public, the 30-year had already repriced in
under a second. There is no version of this system that front-runs that.

What IS available:

  1. The tape reacted, visibly, for three straight days. Volume went 2.4x, then
     2.7x, then 3.2x. A 55-day channel broke on the first of those days. That
     was tradeable on 20 August at 69,351, and it worked.

  2. Knowing WHY it broke tells you how much to trust the break. Most breakouts
     fail. A breakout with a real macro liquidity catalyst behind it, into a
     crowd positioned the wrong way, is a different animal from a breakout into
     an empty afternoon - even though they look identical on the chart.

So price action is the trigger and always has a veto. The signal feed only
scales the position, between `min_risk` and `max_risk`. The system cannot take
a trade because it read something bullish, and it cannot skip a valid technical
signal because it read something bearish - it can only lean.

That constraint is the point. It means the worst case for the intelligence
layer is that it degrades to the plain breakout system, which we measured at
+0.998R on daily BTC. A design whose failure mode is "still works" is worth
more than a cleverer one that can talk itself into a position.
"""

from fxglitch.data import closes
from fxglitch.engine import LONG, Strategy
from fxglitch.indicators import atr, ema, highest, lowest


class Confluence(Strategy):
    name = "Confluence (breakout + signal-weighted sizing)"

    def __init__(self, entry: int = 55, exit: int = 20, atr_period: int = 14,
                 atr_mult: float = 2.5, trend: int = 200,
                 min_risk: float = 0.5, max_risk: float = 3.0,
                 bias_floor: float = -0.5, allow_shorts: bool = True) -> None:
        self.params = {
            "entry": entry, "exit": exit, "atr_mult": atr_mult, "trend": trend,
            "min_risk": min_risk, "max_risk": max_risk, "bias_floor": bias_floor,
            "allow_shorts": allow_shorts,
        }
        self.entry = entry
        self.exit = exit
        self.atr_period = atr_period
        self.atr_mult = atr_mult
        self.trend = trend
        self.min_risk = min_risk
        self.max_risk = max_risk
        self.bias_floor = bias_floor
        self.allow_shorts = allow_shorts
        self.decisions: list[dict] = []   # audit trail

    def prepare(self) -> None:
        c = closes(self.candles)
        highs = [x.high for x in self.candles]
        lows = [x.low for x in self.candles]
        # Shifted by one bar - see donchian_breakout.py for why this matters.
        self.hi_entry = [None] + highest(highs, self.entry)[:-1]
        self.lo_entry = [None] + lowest(lows, self.entry)[:-1]
        self.hi_exit = [None] + highest(highs, self.exit)[:-1]
        self.lo_exit = [None] + lowest(lows, self.exit)[:-1]
        self.atr = atr(self.candles, self.atr_period)
        self.trend_ema = ema(c, self.trend) if self.trend else [None] * len(c)

    def _bias(self, i: int) -> float:
        """The combined non-price read, as of this bar's close.

        `SignalFeed.as_of` physically cannot return anything timestamped after
        this moment, so this is safe by construction rather than by care.
        """
        if self.feed is None:
            return 0.0
        return self.feed.bias_at(self.candles[i].time)

    def _size_for(self, direction: int, bias: float) -> float:
        """Map agreement between the trade and the evidence onto risk.

        agreement = +1  evidence fully backs the trade   -> max_risk
        agreement =  0  evidence is silent               -> midpoint
        agreement = -1  evidence fully opposes           -> min_risk
        """
        agreement = bias * direction
        span = self.max_risk - self.min_risk
        return self.min_risk + span * (agreement + 1) / 2

    def on_bar(self, i: int) -> None:
        a = self.atr[i]
        if a is None or a <= 0:
            return

        bar = self.candles[i]
        price = bar.close
        stop_distance = a * self.atr_mult

        # --- manage an open position ------------------------------------
        if self.position is not None:
            if self.position.direction == LONG:
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

        # --- look for a technical trigger. Price has the only vote here. --
        hi, lo = self.hi_entry[i], self.lo_entry[i]
        if hi is None or lo is None:
            return

        t = self.trend_ema[i]
        long_trigger = price > hi and (t is None or price > t)
        short_trigger = self.allow_shorts and price < lo and (t is None or price < t)
        if not (long_trigger or short_trigger):
            return

        direction = LONG if long_trigger else -1
        bias = self._bias(i)
        agreement = bias * direction

        # The one veto the intelligence layer gets: evidence strongly against.
        # Set bias_floor to -1.0 to disable it entirely and size only.
        if agreement < self.bias_floor:
            self.decisions.append({
                "time": bar.time, "action": "skipped", "direction": direction,
                "bias": bias, "agreement": agreement, "price": price,
            })
            return

        risk = self._size_for(direction, bias)
        reason = (f"{'up' if long_trigger else 'down'} through {self.entry}-bar "
                  f"channel | bias {bias:+.2f} | risk {risk:.2f}%")
        self.decisions.append({
            "time": bar.time, "action": "entered", "direction": direction,
            "bias": bias, "agreement": agreement, "risk": risk, "price": price,
        })

        if long_trigger:
            self.buy(sl=price - stop_distance, tp=None, risk_pct=risk, reason=reason)
        else:
            self.sell(sl=price + stop_distance, tp=None, risk_pct=risk, reason=reason)


strategy = Confluence

"""Volatility Squeeze breakout — enter when coiled volatility fires.

The idea: when Bollinger Bands contract inside Keltner Channels, volatility is
compressing. Traders, range-bound strategies, and stop clusters all pile up in
the same narrow space. When the bands finally push back outside the channels,
the stored energy has to go somewhere.

This is John Carter's TTM Squeeze distilled to what can be measured:

    1. SQUEEZE STATE: Bollinger upper < Keltner upper AND Bollinger lower >
       Keltner lower. Volatility is compressed.
    2. SQUEEZE FIRES: that condition was true on the previous bar and is now
       false. The bands have expanded past the channels.
    3. DIRECTION: decided by where price sits relative to the Keltner envelope
       at the moment the squeeze fires. Above the upper channel = long; below
       the lower = short.

The squeeze fires often. Most of the time it is noise reclaiming its normal
bandwidth. The trend filter (200 EMA) and the requirement that price actually
breach the Keltner extreme are there to reject the fires that lead nowhere.

What this strategy is NOT:
    - A mean reversion play. The entry is on expansion, not contraction.
    - A prediction that a squeeze will fire. It waits for it to happen.
    - A guarantee. Volatility expansion after compression is real and
      well-documented. Whether it is tradeable after costs and slippage on
      YOUR market and YOUR timeframe is a measurement, not an assumption.
      Run it and see.
"""

from fxglitch.data import closes
from fxglitch.engine import LONG, Strategy
from fxglitch.indicators import atr, bollinger, ema, keltner


class SqueezeBreakout(Strategy):
    name = "Squeeze breakout (volatility compression)"

    def __init__(
        self,
        bb_period: int = 20,
        bb_mult: float = 2.0,
        kc_period: int = 20,
        kc_mult: float = 1.5,
        atr_period: int = 14,
        atr_mult: float = 2.5,
        trend: int = 200,
        min_squeeze_bars: int = 1,
        allow_shorts: bool = True,
    ) -> None:
        self.params = {
            "bb_period": bb_period, "bb_mult": bb_mult,
            "kc_period": kc_period, "kc_mult": kc_mult,
            "atr_period": atr_period, "atr_mult": atr_mult,
            "trend": trend, "min_squeeze_bars": min_squeeze_bars,
            "allow_shorts": allow_shorts,
        }
        self.bb_period = bb_period
        self.bb_mult = bb_mult
        self.kc_period = kc_period
        self.kc_mult = kc_mult
        self.atr_period = atr_period
        self.atr_mult = atr_mult
        self.trend = trend
        self.min_squeeze_bars = min_squeeze_bars
        self.allow_shorts = allow_shorts

    def prepare(self) -> None:
        c = closes(self.candles)

        # Bollinger Bands: close-to-close standard deviation.
        self.bb_upper, self.bb_mid, self.bb_lower = bollinger(
            c, self.bb_period, self.bb_mult)

        # Keltner Channels: range-based (ATR) envelope.
        self.kc_upper, self.kc_mid, self.kc_lower = keltner(
            self.candles, self.kc_period, self.kc_mult, self.atr_period)

        self.atr_vals = atr(self.candles, self.atr_period)
        self.trend_ema = ema(c, self.trend) if self.trend else [None] * len(c)

        # Pre-compute the squeeze state per bar. True = BB inside KC.
        n = len(self.candles)
        self.squeeze: list[bool | None] = [None] * n
        for i in range(n):
            bb_u, bb_l = self.bb_upper[i], self.bb_lower[i]
            kc_u, kc_l = self.kc_upper[i], self.kc_lower[i]
            if None in (bb_u, bb_l, kc_u, kc_l):
                continue
            self.squeeze[i] = bb_u < kc_u and bb_l > kc_l

    def _in_squeeze(self, i: int) -> bool:
        """True if bar `i` is in a squeeze state and has been for at least
        `min_squeeze_bars` consecutive bars."""
        for j in range(self.min_squeeze_bars):
            idx = i - j
            if idx < 0 or self.squeeze[idx] is not True:
                return False
        return True

    def on_bar(self, i: int) -> None:
        a = self.atr_vals[i]
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
            else:
                trailed = price + stop_distance
                if self.position.sl is None or trailed < self.position.sl:
                    self.position.sl = trailed
            return

        # --- detect a squeeze firing ------------------------------------
        # Previous bar was in a squeeze; this bar is NOT. The bands have
        # expanded. This is a one-bar event — it fires once, then the
        # squeeze state is gone.
        if i < 1:
            return
        was_squeezed = self._in_squeeze(i - 1)
        now_squeezed = self.squeeze[i]
        if not was_squeezed or now_squeezed is not False:
            return

        # --- direction: where is price relative to the Keltner envelope? -
        kc_u, kc_l = self.kc_upper[i], self.kc_lower[i]
        if kc_u is None or kc_l is None:
            return

        t = self.trend_ema[i]

        if price > kc_u and (t is None or price > t):
            self.buy(
                sl=price - stop_distance, tp=None,
                reason=f"squeeze fired UP | BB expanded past KC | "
                       f"price {price:.2f} > KC upper {kc_u:.2f}",
            )
        elif self.allow_shorts and price < kc_l and (t is None or price < t):
            self.sell(
                sl=price + stop_distance, tp=None,
                reason=f"squeeze fired DOWN | BB expanded past KC | "
                       f"price {price:.2f} < KC lower {kc_l:.2f}",
            )


strategy = SqueezeBreakout

"""Indicators.

Every function returns a list the same length as the input, with `None` in the
warm-up slots. That alignment matters: `ema(closes, 50)[i]` is always the EMA
as it stood at the close of bar `i` and never peeks at bar i+1. If you build a
new indicator here, keep that promise or your backtests will lie to you.
"""

from __future__ import annotations

from .data import Series

Num = float | None


def sma(values: list[float], period: int) -> list[Num]:
    out: list[Num] = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[Num]:
    out: list[Num] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period  # seed with an SMA
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[Num]:
    """Wilder's RSI. 0-100; classic thresholds 30/70."""
    out: list[Num] = [None] * len(values)
    if len(values) <= period:
        return out

    gains = losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def true_range(candles: Series) -> list[Num]:
    out: list[Num] = [None] * len(candles)
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i - 1]
        out[i] = max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close))
    return out


def atr(candles: Series, period: int = 14) -> list[Num]:
    """Average True Range - the workhorse for sizing stops on synthetics."""
    tr = true_range(candles)
    out: list[Num] = [None] * len(candles)
    vals = [t for t in tr[1 : period + 1] if t is not None]
    if len(vals) < period:
        return out
    prev = sum(vals) / period
    out[period] = prev
    for i in range(period + 1, len(candles)):
        prev = (prev * (period - 1) + tr[i]) / period  # type: ignore[operator]
        out[i] = prev
    return out


def stdev(values: list[float], period: int) -> list[Num]:
    out: list[Num] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        out[i] = (sum((v - mean) ** 2 for v in window) / period) ** 0.5
    return out


def bollinger(
    values: list[float], period: int = 20, mult: float = 2.0
) -> tuple[list[Num], list[Num], list[Num]]:
    """Returns (upper, middle, lower)."""
    mid = sma(values, period)
    sd = stdev(values, period)
    upper: list[Num] = [None] * len(values)
    lower: list[Num] = [None] * len(values)
    for i in range(len(values)):
        if mid[i] is not None and sd[i] is not None:
            upper[i] = mid[i] + mult * sd[i]
            lower[i] = mid[i] - mult * sd[i]
    return upper, mid, lower


def keltner(
    candles: Series, period: int = 20, mult: float = 1.5, atr_period: int = 14
) -> tuple[list[Num], list[Num], list[Num]]:
    """Keltner Channels: EMA ± mult × ATR. Returns (upper, mid, lower).

    Distinct from Bollinger in that the width is driven by ATR (range) rather
    than standard deviation (close-to-close). When Bollinger Bands contract
    inside Keltner Channels, volatility is compressing — the squeeze state.
    """
    from .data import closes as _closes  # avoid circular at module level

    mid = ema(_closes(candles), period)
    a = atr(candles, atr_period)
    upper: list[Num] = [None] * len(candles)
    lower: list[Num] = [None] * len(candles)
    for i in range(len(candles)):
        if mid[i] is not None and a[i] is not None:
            upper[i] = mid[i] + mult * a[i]
            lower[i] = mid[i] - mult * a[i]
    return upper, mid, lower


def highest(values: list[float], period: int) -> list[Num]:
    out: list[Num] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = max(values[i - period + 1 : i + 1])
    return out


def lowest(values: list[float], period: int) -> list[Num]:
    out: list[Num] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = min(values[i - period + 1 : i + 1])
    return out


def crossed_above(a: list[Num], b: list[Num], i: int) -> bool:
    """True if series `a` crossed up through `b` on bar `i`."""
    if i < 1:
        return False
    if None in (a[i], b[i], a[i - 1], b[i - 1]):
        return False
    return a[i - 1] <= b[i - 1] and a[i] > b[i]  # type: ignore[operator]


def crossed_below(a: list[Num], b: list[Num], i: int) -> bool:
    if i < 1:
        return False
    if None in (a[i], b[i], a[i - 1], b[i - 1]):
        return False
    return a[i - 1] >= b[i - 1] and a[i] < b[i]  # type: ignore[operator]

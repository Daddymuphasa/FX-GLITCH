"""Synthetic price generators, so you can run the whole toolchain today
without waiting on any broker, API key or CSV download.

There is a second reason this file exists, and it is the more important one.
Deriv's synthetic indices are NOT a mystery: they are published as geometric
random walks driven by a random number generator, with a stated annual
volatility. The Volatility 75 Index is a random walk with 75% volatility.
Boom and Crash are random walks with a rare, large spike bolted on.

That has a consequence worth sitting with before you trade them: on a pure
random walk, no pattern in past prices predicts future prices. Support,
resistance, trendlines and chart patterns will all APPEAR in the data below -
you can generate it yourself and go find a perfect head and shoulders in it.
They appear because human eyes find shapes in noise, not because the shapes
mean anything.

So: run your strategy against this simulated data first. Whatever edge it
shows here is, by construction, pure luck. That number is your baseline. If
your strategy scores about the same on real data as it does on noise, you have
not found an edge - you have found a coincidence.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from .data import Candle, Series


def random_walk(
    bars: int = 5000,
    start_price: float = 1000.0,
    annual_vol: float = 0.75,
    bar_minutes: int = 1,
    drift: float = 0.0,
    seed: int | None = 42,
    start_time: datetime | None = None,
    ticks_per_bar: int = 12,
) -> Series:
    """A geometric random walk shaped like a Deriv volatility index.

    annual_vol=0.75 approximates the Volatility 75 Index, 0.10 approximates
    something calm like XAUUSD. Each bar is built from `ticks_per_bar`
    sub-moves so the highs and lows are realistic rather than just max(o, c).
    """
    rng = random.Random(seed)
    t0 = start_time or datetime(2024, 1, 1, tzinfo=timezone.utc)

    bars_per_year = 365 * 24 * 60 / bar_minutes
    per_bar_sigma = annual_vol / math.sqrt(bars_per_year)
    tick_sigma = per_bar_sigma / math.sqrt(ticks_per_bar)
    tick_drift = drift / (bars_per_year * ticks_per_bar)

    price = start_price
    out: Series = []
    for i in range(bars):
        o = price
        hi = lo = price
        for _ in range(ticks_per_bar):
            price *= math.exp(tick_drift + rng.gauss(0.0, tick_sigma))
            hi, lo = max(hi, price), min(lo, price)
        out.append(
            Candle(
                time=t0 + timedelta(minutes=bar_minutes * i),
                open=o, high=hi, low=lo, close=price,
                volume=float(ticks_per_bar),
            )
        )
    return out


def spike_index(
    bars: int = 5000,
    start_price: float = 1000.0,
    spike_every: int = 1000,
    direction: int = 1,
    spike_size: float = 0.015,
    seed: int | None = 42,
    **kwargs,
) -> Series:
    """A Boom/Crash-style series: a slow drip one way, rare violent spikes the other.

    direction=+1 gives Boom (drips down, spikes up), -1 gives Crash.
    `spike_every` is the AVERAGE bars between spikes, not a fixed interval -
    which is exactly why 'count the candles since the last spike' systems do
    not work: the wait is memoryless. Having gone 900 bars without a spike
    tells you nothing about whether the next bar spikes.
    """
    rng = random.Random(seed)
    base = random_walk(bars, start_price, seed=seed, **kwargs)

    # The drip: a small persistent move against the spike direction, sized so
    # that over `spike_every` bars it cancels one average spike. That is how
    # the real indices are built - the slow bleed pays for the violent jump.
    drip = -direction * spike_size / spike_every

    out: Series = []
    price = start_price
    for c in base:
        ret = math.log(c.close / c.open) * 0.35 + drip
        spiked = rng.random() < 1.0 / spike_every
        if spiked:
            ret += direction * spike_size * rng.uniform(0.7, 1.4)

        o = price
        price = o * math.exp(ret)
        if spiked:
            hi = max(o, price) if direction > 0 else max(o, price) * 1.0002
            lo = min(o, price) * 0.9998 if direction > 0 else min(o, price)
        else:
            span = abs(price - o) * rng.uniform(0.2, 1.2) + o * 0.0002
            hi, lo = max(o, price) + span, min(o, price) - span
        out.append(Candle(time=c.time, open=o, high=hi, low=lo, close=price, volume=1.0))
    return out


PRESETS = {
    # name          generator      kwargs
    "V10":   (random_walk, {"annual_vol": 0.10, "start_price": 6000.0}),
    "V25":   (random_walk, {"annual_vol": 0.25, "start_price": 2500.0}),
    "V50":   (random_walk, {"annual_vol": 0.50, "start_price": 9000.0}),
    "V75":   (random_walk, {"annual_vol": 0.75, "start_price": 400000.0}),
    "V100":  (random_walk, {"annual_vol": 1.00, "start_price": 1200.0}),
    "BOOM1000":  (spike_index, {"spike_every": 1000, "direction": 1, "start_price": 9500.0}),
    "CRASH1000": (spike_index, {"spike_every": 1000, "direction": -1, "start_price": 9500.0}),
    "BOOM500":   (spike_index, {"spike_every": 500, "direction": 1, "start_price": 9500.0}),
    "CRASH500":  (spike_index, {"spike_every": 500, "direction": -1, "start_price": 9500.0}),
    # A gold-like series: calmer, with a mild upward drift.
    "XAUUSD-SIM": (random_walk, {"annual_vol": 0.16, "start_price": 2350.0, "drift": 0.08,
                                 "bar_minutes": 15}),
}


def preset(name: str, bars: int = 5000, seed: int | None = 42) -> Series:
    """Generate a named simulated market. See PRESETS for the list."""
    key = name.upper()
    if key not in PRESETS:
        raise KeyError(f"Unknown preset {name!r}. Available: {', '.join(sorted(PRESETS))}")
    gen, kwargs = PRESETS[key]
    return gen(bars=bars, seed=seed, **kwargs)


def infer_bar_minutes(candles: Series) -> int:
    """Work out the timeframe of a series from its most common bar gap."""
    if len(candles) < 3:
        return 1
    gaps: dict[int, int] = {}
    for a, b in zip(candles, candles[1:]):
        m = int((b.time - a.time).total_seconds() // 60)
        if m > 0:
            gaps[m] = gaps.get(m, 0) + 1
    return max(gaps, key=gaps.get) if gaps else 1


def realised_vol(candles: Series) -> float:
    """Annualised volatility actually present in a price series.

    Used to build noise that matches the real market instead of some arbitrary
    default. Comparing a strategy on BTC against a 75%-vol random walk would be
    a rigged test in whichever direction happened to flatter it.
    """
    rets = []
    for a, b in zip(candles, candles[1:]):
        if a.close > 0 and b.close > 0:
            rets.append(math.log(b.close / a.close))
    if len(rets) < 2:
        return 0.5

    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    per_bar = math.sqrt(var)

    bars_per_year = 365 * 24 * 60 / infer_bar_minutes(candles)
    return per_bar * math.sqrt(bars_per_year)

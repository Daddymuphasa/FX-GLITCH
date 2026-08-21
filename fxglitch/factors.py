"""Turning price and volume into Signals.

These are the factors you can compute today, from data you already have, with
no API key and no vendor. They are deliberately first because of something the
20 August case study made obvious:

    The Treasury buyback announcement was the CAUSE of the move. But the tape
    told you about it too - volume went 2.4x on the breakout day and 3.2x by
    day three. A system watching only price and volume entered on 20 August at
    69,351 and was up 2R by the 21st, having never read a single headline.

That is the honest hierarchy. News explains *why*. Volume and range tell you
*that it is happening*, they are available instantly and free, and they cannot
be faked by a bad data vendor or a mistimed timestamp. Build these first, then
add news to size up and to filter - not to predict.
"""

from __future__ import annotations

from datetime import timedelta

from .data import Series
from .indicators import atr, ema, highest, lowest
from .signals import Kind, Signal


def volume_surge(candles: Series, lookback: int = 20, threshold: float = 2.0,
                 horizon_bars: int = 3) -> list[Signal]:
    """Unusual volume, signed by the direction of the bar.

    Volume is the closest thing to a free lunch in signal terms: it is
    published with the bar, it is not revised, and it is the footprint of real
    money rather than an opinion about it. A 3x volume day is somebody large
    doing something, whatever the news says.
    """
    out: list[Signal] = []
    bar_len = _bar_delta(candles)
    for i in range(lookback, len(candles)):
        c = candles[i]
        avg = sum(x.volume for x in candles[i - lookback:i]) / lookback
        if avg <= 0:
            continue
        ratio = c.volume / avg
        if ratio < threshold:
            continue

        move = (c.close - c.open) / c.open if c.open else 0.0
        if abs(move) < 0.002:
            continue  # big volume, no progress: churn, not conviction

        # Cap so a 10x volume day is not ten times the signal of a 2x day.
        strength = min(1.0, (ratio - 1) / 3)
        out.append(Signal(
            at=c.time,
            # A daily bar's volume is only final when the bar closes, so the
            # earliest you could act is the NEXT bar.
            available_at=c.time + bar_len,
            kind=Kind.TECHNICAL,
            name="volume_surge",
            score=strength * (1 if move > 0 else -1),
            confidence=min(0.9, 0.4 + ratio / 10),
            horizon=bar_len * horizon_bars,
            source="ohlcv",
            note=f"{ratio:.1f}x avg volume, bar {move*100:+.1f}%",
            meta={"ratio": ratio, "move_pct": move * 100},
        ))
    return out


def compression(candles: Series, period: int = 20, percentile: float = 0.25) -> list[Signal]:
    """Range contraction - the coiled-spring setup.

    Direction-neutral by design (score 0). This does not tell you which way
    price will go; it tells you that when it goes, it will go hard. Use it to
    size, not to choose a side.

    It is what made 19 August violent: the three sessions before it had ATR
    around 1,200 on a 63,000 price - under 2% daily range, unusually quiet for
    BTC. Quiet markets with crowded positioning are how you get a 26% move in
    four days.
    """
    a = atr(candles, period)
    bar_len = _bar_delta(candles)
    valid = [(i, a[i]) for i in range(len(a)) if a[i] is not None]
    if len(valid) < period * 3:
        return []

    out: list[Signal] = []
    window = period * 5
    for pos, (i, val) in enumerate(valid):
        if pos < window:
            continue
        recent = sorted(v for _, v in valid[pos - window:pos])
        cut = recent[int(len(recent) * percentile)]
        if val > cut:
            continue
        c = candles[i]
        tightness = 1.0 - (val / cut) if cut else 0.0
        out.append(Signal(
            at=c.time,
            available_at=c.time + bar_len,
            kind=Kind.TECHNICAL,
            name="compression",
            score=0.0,                       # no direction, on purpose
            confidence=min(0.8, 0.4 + tightness),
            horizon=bar_len * 10,
            source="ohlcv",
            note=f"ATR {val:,.0f} in bottom {percentile*100:.0f}% of {window} bars",
            meta={"atr": val, "atr_percentile_cut": cut},
        ))
    return out


def trend_regime(candles: Series, fast: int = 50, slow: int = 200) -> list[Signal]:
    """Which side of the market you should be leaning, structurally.

    Not a trade signal - a permission slip. Its job is to stop you shorting
    into an uptrend because one bearish headline appeared.
    """
    c = [x.close for x in candles]
    f, s = ema(c, fast), ema(c, slow)
    bar_len = _bar_delta(candles)
    out: list[Signal] = []
    for i in range(len(candles)):
        if f[i] is None or s[i] is None or s[i] == 0:
            continue
        gap = (f[i] - s[i]) / s[i]
        out.append(Signal(
            at=candles[i].time,
            available_at=candles[i].time + bar_len,
            kind=Kind.TECHNICAL,
            name="trend_regime",
            score=max(-1.0, min(1.0, gap * 10)),
            confidence=0.6,
            horizon=bar_len * 2,
            source="ohlcv",
            note=f"EMA{fast} vs EMA{slow}: {gap*100:+.1f}%",
        ))
    return out


def squeeze_fuel(candles: Series, lookback: int = 30, horizon_bars: int = 10) -> list[Signal]:
    """A proxy for crowded positioning, built only from price.

    Real positioning data - funding rates, open interest, long/short ratios -
    is what you actually want here, and `tools/fetch_derivs.py` is where that
    belongs once you have a source. Until then this approximates the setup that
    precedes a squeeze: a grinding, persistent move in one direction that has
    gone on long enough for the crowd to be leaning the same way, with no
    volume conviction behind it.

    The score is CONTRARIAN - it points against the recent grind, because that
    is the direction the squeeze runs. The 19-21 August move is the textbook
    case: months of range-bound pressure, everyone positioned short, then $2.7bn
    of forced buying when the catalyst hit.
    """
    if len(candles) < lookback * 2:
        return []
    bar_len = _bar_delta(candles)
    out: list[Signal] = []

    for i in range(lookback * 2, len(candles)):
        window = candles[i - lookback:i]
        closes_ = [x.close for x in window]
        drift = (closes_[-1] - closes_[0]) / closes_[0] if closes_[0] else 0.0

        # How one-sided was the path? Lots of down bars and a steady decline
        # means shorts are comfortable and stacked.
        down = sum(1 for a, b in zip(window, window[1:]) if b.close < a.close)
        one_sidedness = abs(down / max(1, len(window) - 1) - 0.5) * 2

        # Falling volume into the grind = no conviction, just drift.
        first_half = sum(x.volume for x in window[:lookback // 2])
        second_half = sum(x.volume for x in window[lookback // 2:])
        fading = second_half < first_half if first_half else False

        if abs(drift) < 0.03 or one_sidedness < 0.3 or not fading:
            continue

        out.append(Signal(
            at=candles[i].time,
            available_at=candles[i].time + bar_len,
            kind=Kind.POSITIONING,
            name="squeeze_fuel",
            score=max(-1.0, min(1.0, -drift * 5)),   # contrarian
            confidence=min(0.6, 0.25 + one_sidedness * 0.4),   # capped: it is a proxy
            horizon=bar_len * horizon_bars,
            source="ohlcv-proxy",
            note=f"{lookback}-bar drift {drift*100:+.1f}%, "
                 f"{one_sidedness:.0%} one-sided, volume fading",
            meta={"drift_pct": drift * 100, "one_sidedness": one_sidedness},
        ))
    return out


def breakout_confirmed(candles: Series, channel: int = 55,
                       vol_mult: float = 1.5) -> list[Signal]:
    """A channel break that real volume showed up for.

    Breakouts fail constantly. The ones that do not tend to arrive with volume,
    because a genuine repricing requires someone to actually buy. This is the
    single highest-value filter in this file, and it is what fired on 19 August:
    close 69,266 through a 55-day high of 66,910, on 2.4x volume.
    """
    highs = [x.high for x in candles]
    lows = [x.low for x in candles]
    hi = [None] + highest(highs, channel)[:-1]
    lo = [None] + lowest(lows, channel)[:-1]
    bar_len = _bar_delta(candles)

    out: list[Signal] = []
    for i in range(channel + 20, len(candles)):
        c = candles[i]
        avg_vol = sum(x.volume for x in candles[i - 20:i]) / 20
        vol_ok = avg_vol > 0 and c.volume >= avg_vol * vol_mult
        ratio = c.volume / avg_vol if avg_vol else 0.0

        up = hi[i] is not None and c.close > hi[i]
        down = lo[i] is not None and c.close < lo[i]
        if not (up or down):
            continue

        out.append(Signal(
            at=c.time,
            available_at=c.time + bar_len,
            kind=Kind.TECHNICAL,
            name="breakout_confirmed" if vol_ok else "breakout_unconfirmed",
            score=(0.8 if vol_ok else 0.3) * (1 if up else -1),
            confidence=0.75 if vol_ok else 0.35,
            horizon=bar_len * 5,
            source="ohlcv",
            note=f"{'up' if up else 'down'} through {channel}-bar "
                 f"{'high' if up else 'low'}, {ratio:.1f}x volume",
            meta={"vol_ratio": ratio, "confirmed": vol_ok},
        ))
    return out


def all_price_factors(candles: Series) -> list[Signal]:
    """Everything computable from OHLCV alone."""
    return (volume_surge(candles) + compression(candles) + trend_regime(candles)
            + squeeze_fuel(candles) + breakout_confirmed(candles))


def _bar_delta(candles: Series) -> timedelta:
    """The timeframe of the series, used to keep availability honest."""
    if len(candles) < 3:
        return timedelta(days=1)
    gaps: dict[float, int] = {}
    for a, b in zip(candles[:200], candles[1:200]):
        g = (b.time - a.time).total_seconds()
        if g > 0:
            gaps[g] = gaps.get(g, 0) + 1
    return timedelta(seconds=max(gaps, key=gaps.get)) if gaps else timedelta(days=1)

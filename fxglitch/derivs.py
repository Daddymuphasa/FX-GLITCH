"""Funding rates and open interest - what the crowd is actually doing.

This is the file the 19 August squeeze argued for. Roughly $2.7bn of shorts
were force-bought in a cascade, and the setup for that was not a secret: it was
published continuously, for free, in funding and open-interest data that almost
nobody reads. That is the asymmetry worth building on - not private information,
but public information nobody bothers with.

WHY THIS DATA IS DIFFERENT FROM EVERYTHING ELSE IN THE REPO
-----------------------------------------------------------
Every other factor here is derived from price. That makes them correlated with
the very breakouts they are asked to judge - a confound we measured and named
in docs/INTELLIGENCE.md. Funding and open interest are genuinely independent
inputs: they describe POSITIONING, not price. They can tell you that a move up
happened on shorts covering rather than new buyers arriving, which price alone
absolutely cannot.

READING FUNDING
---------------
On a perpetual swap, funding is the payment that tethers the contract to spot.
Positive funding means longs pay shorts, which means the crowd is leaning long.

    funding  +0.01%/8h    the resting baseline on Binance. Means nothing.
    funding  > +0.05%/8h  crowd is aggressively long and PAYING to stay there
    funding  > +0.10%/8h  euphoric. Long liquidations become the fuel.
    funding  < -0.01%/8h  shorts are paying. Crowd is leaning short.
    funding  < -0.05%/8h  heavily short. This is squeeze fuel.

The signal is CONTRARIAN at the extremes and meaningless in the middle. Crowded
positioning is not a prediction that price falls; it is a statement about what
happens if price rises anyway - somebody is forced to buy.

READING OPEN INTEREST AGAINST PRICE
------------------------------------
Open interest is the number of contracts outstanding. On its own it says little.
Paired with price direction it is one of the most informative things available:

    price UP   + OI UP     new longs opening       genuine trend, real money
    price UP   + OI DOWN   shorts covering         a SQUEEZE - can be violent
                                                    but exhausts itself
    price DOWN + OI UP     new shorts opening      genuine downtrend
    price DOWN + OI DOWN   longs liquidating       capitulation, often a low

The distinction in row two is the entire point of this module. A rally on
rising OI and a rally on falling OI look identical on a chart and mean opposite
things. The first has buyers behind it. The second is people who were forced,
and when the forcing stops, so does the move.

A DATA WARNING THAT WILL BITE YOU
----------------------------------
Binance's free open-interest history endpoint returns only the LAST 30 DAYS.
Funding history goes back to 2019, but OI does not. You cannot backtest an
OI-based factor over years on free data - this is a real constraint, not a bug
in the fetcher. Options: start logging OI daily from today onward, or pay for a
vendor. Do not quietly backtest OI on 30 days and call it validated.
"""

from __future__ import annotations

import bisect
import csv
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .data import Series
from .signals import Kind, Signal

# Binance's resting funding rate. Anything near this is noise, not information.
BASELINE_FUNDING = 0.0001          # 0.01% per 8h
FUNDING_ELEVATED = 0.0005          # 0.05%
FUNDING_EXTREME = 0.0010           # 0.10%


@dataclass(slots=True)
class Point:
    """One timestamped observation of a positioning metric."""

    time: datetime
    value: float


def _parse(raw: str) -> datetime:
    raw = str(raw).strip()
    if raw.isdigit():
        ts = int(raw)
        if ts > 1e11:
            ts //= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp: {raw!r}")


def load_series_csv(path: str, value_column: str) -> list[Point]:
    """Read a two-column time series csv written by tools/fetch_derivs.py."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No file at {path}. Run:  python tools/fetch_derivs.py BTCUSDT")
    out: list[Point] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if not row.get(value_column):
                continue
            out.append(Point(_parse(row["time"]), float(row[value_column])))
    out.sort(key=lambda p: p.time)
    return out


def resample_daily(points: list[Point], how: str = "mean") -> list[Point]:
    """Collapse 8-hourly funding into daily values aligned to daily bars.

    Funding prints three times a day; a daily strategy wants one number. `sum`
    gives the total cost of holding for that day, `mean` the average pressure.
    """
    buckets: dict[datetime, list[float]] = {}
    for p in points:
        day = p.time.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets.setdefault(day, []).append(p.value)
    fn = {"mean": lambda v: sum(v) / len(v), "sum": sum,
          "last": lambda v: v[-1], "max": max}[how]
    return [Point(d, fn(v)) for d, v in sorted(buckets.items())]


# --------------------------------------------------------------------------
# Factors
# --------------------------------------------------------------------------

def funding_extreme(points: list[Point], lag: timedelta = timedelta(hours=8),
                    horizon_hours: int = 48) -> list[Signal]:
    """Contrarian signal when funding shows a crowded book.

    `lag` exists because a funding rate is settled at the end of its interval.
    Reading it at the instant it prints is borderline; one interval of delay is
    the honest assumption.
    """
    out: list[Signal] = []
    for p in points:
        f = p.value
        if abs(f) < FUNDING_ELEVATED:
            continue  # inside the noise band around baseline

        # Scale from elevated to extreme, capped.
        magnitude = min(1.0, (abs(f) - FUNDING_ELEVATED)
                        / (FUNDING_EXTREME - FUNDING_ELEVATED))
        score = -magnitude if f > 0 else magnitude   # contrarian

        out.append(Signal(
            at=p.time,
            available_at=p.time + lag,
            kind=Kind.POSITIONING,
            name="funding_extreme",
            score=score,
            confidence=min(0.8, 0.45 + magnitude * 0.35),
            horizon=timedelta(hours=horizon_hours),
            source="perp funding",
            note=f"funding {f*100:+.4f}%/8h - crowd "
                 f"{'long, paying to stay' if f > 0 else 'short, paying to stay'}",
            meta={"funding": f},
        ))
    return out


def oi_price_divergence(candles: Series, oi: list[Point],
                        lookback_bars: int = 3,
                        min_move_pct: float = 1.5) -> list[Signal]:
    """The four quadrants of price direction against open interest change.

    This is the factor that can tell a real rally from a short squeeze, which
    is the single most useful distinction in a leveraged market.
    """
    if not oi or len(candles) < lookback_bars + 2:
        return []

    oi_times = [p.time for p in oi]

    def oi_at(t: datetime) -> float | None:
        i = bisect.bisect_right(oi_times, t) - 1
        return oi[i].value if i >= 0 else None

    bar_len = candles[1].time - candles[0].time if len(candles) > 1 else timedelta(days=1)
    out: list[Signal] = []

    for i in range(lookback_bars, len(candles)):
        c = candles[i]
        prev = candles[i - lookback_bars]
        move = (c.close - prev.close) / prev.close * 100 if prev.close else 0.0
        if abs(move) < min_move_pct:
            continue

        now, then = oi_at(c.time), oi_at(prev.time)
        if now is None or then is None or then <= 0:
            continue
        oi_change = (now - then) / then * 100

        price_up, oi_up = move > 0, oi_change > 0

        if price_up and oi_up:
            label, score, conf = "new_longs", 0.6, 0.65
            note = "price up on RISING OI - new money, genuine trend"
        elif price_up and not oi_up:
            # A squeeze. Violent, but it burns out - the buying is forced and
            # finite. Mildly positive now, and a warning about what follows.
            label, score, conf = "short_squeeze", 0.25, 0.6
            note = "price up on FALLING OI - shorts covering, squeeze exhausts"
        elif not price_up and oi_up:
            label, score, conf = "new_shorts", -0.6, 0.65
            note = "price down on RISING OI - new shorts, genuine downtrend"
        else:
            # Longs being liquidated. Capitulation often marks a low.
            label, score, conf = "long_liquidation", -0.2, 0.6
            note = "price down on FALLING OI - longs flushed, often a low"

        out.append(Signal(
            at=c.time,
            available_at=c.time + bar_len,
            kind=Kind.POSITIONING,
            name=f"oi_{label}",
            score=score,
            confidence=conf,
            horizon=bar_len * 4,
            source="open interest",
            note=f"{note} ({move:+.1f}% price, {oi_change:+.1f}% OI)",
            meta={"price_move_pct": move, "oi_change_pct": oi_change},
        ))
    return out


def squeeze_setup(funding: list[Point], oi: list[Point], candles: Series,
                  funding_threshold: float = -0.0002) -> list[Signal]:
    """The specific configuration that preceded 19 August.

    Three things at once:
      1. Funding negative - shorts are paying, so the crowd is short.
      2. Open interest high or rising - and there are a lot of them.
      3. Price has been grinding down or flat - they are comfortable.

    That is not a prediction that price rises. It is a statement that IF price
    rises, the move will be amplified by forced buying. It belongs to sizing
    and to stop placement, not to entry timing.
    """
    if not funding or not oi:
        return []

    f_times = [p.time for p in funding]
    oi_times = [p.time for p in oi]
    bar_len = candles[1].time - candles[0].time if len(candles) > 1 else timedelta(days=1)
    out: list[Signal] = []

    for i in range(20, len(candles)):
        c = candles[i]

        fi = bisect.bisect_right(f_times, c.time) - 1
        oi_i = bisect.bisect_right(oi_times, c.time) - 1
        if fi < 0 or oi_i < 5:
            continue

        f = funding[fi].value
        if f > funding_threshold:
            continue  # crowd is not short enough to matter

        oi_now = oi[oi_i].value
        oi_then = oi[max(0, oi_i - 5)].value
        if oi_then <= 0:
            continue
        oi_trend = (oi_now - oi_then) / oi_then * 100
        if oi_trend < -5:
            continue  # positions are being closed; the fuel is draining

        drift = (c.close - candles[i - 20].close) / candles[i - 20].close * 100
        if drift > 5:
            continue  # already running; the setup has become the move

        crowding = min(1.0, abs(f) / 0.0005)
        out.append(Signal(
            at=c.time,
            available_at=c.time + bar_len,
            kind=Kind.POSITIONING,
            name="squeeze_setup",
            score=0.5 * crowding,
            confidence=min(0.75, 0.4 + crowding * 0.35),
            horizon=bar_len * 7,
            source="funding + OI",
            note=f"funding {f*100:+.4f}%, OI {oi_trend:+.1f}%, "
                 f"20-bar drift {drift:+.1f}% - shorts crowded and comfortable",
            meta={"funding": f, "oi_trend_pct": oi_trend, "drift_pct": drift},
        ))
    return out


def all_deriv_factors(candles: Series, funding: list[Point] | None = None,
                      oi: list[Point] | None = None) -> list[Signal]:
    """Everything computable from positioning data you have."""
    out: list[Signal] = []
    if funding:
        out += funding_extreme(funding)
    if oi:
        out += oi_price_divergence(candles, oi)
    if funding and oi:
        out += squeeze_setup(funding, oi, candles)
    return out

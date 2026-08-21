"""Signals: everything that is not price, expressed so it can be tested.

THE ONE RULE THIS FILE EXISTS TO ENFORCE
----------------------------------------
Every signal carries `available_at`: the moment a trader could actually have
acted on it. Not when the event happened - when it was *knowable*.

This distinction is the entire ballgame for news-driven systems, and it is
where almost every "my AI reads the news" backtest quietly dies. Examples of
the gap:

  - The Treasury announced expanded buybacks at 09:00. A headline you scraped
    is timestamped 09:00. But the wire hit at 08:59:58, the algos moved the
    30-year in 40 milliseconds, and the price you would have paid at 09:00:30
    already contained it.
  - An ETF flow number is dated Tuesday. It is *published* Wednesday evening.
    Backtesting it against Tuesday's close is time travel, and it will show a
    spectacular, entirely fake edge.
  - A CPI print is dated the 12th. Revisions land months later. If your data
    vendor gives you the revised series, every historical bar contains
    information from the future.

So: `at` is when it happened, `available_at` is when you could trade it. If you
do not know the second one, be pessimistic. The cost of being pessimistic is a
strategy that looks worse than it is. The cost of being optimistic is losing
real money on an edge that never existed.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class Direction(Enum):
    BULLISH = 1
    NEUTRAL = 0
    BEARISH = -1


class Kind(str, Enum):
    """What family a signal belongs to. Used for weighting and attribution."""

    MACRO = "macro"                 # rates, liquidity, DXY, Treasury ops
    REGULATORY = "regulatory"       # bills, rulings, enforcement, approvals
    FLOW = "flow"                   # ETF creations/redemptions, exchange netflow
    POSITIONING = "positioning"     # funding, open interest, long/short ratio
    ONCHAIN = "onchain"             # whale moves, dormancy, supply on exchanges
    TECHNICAL = "technical"         # anything derived from price and volume
    SENTIMENT = "sentiment"         # social, fear/greed, search trends


@dataclass(slots=True)
class Signal:
    """One piece of evidence, at one point in time."""

    at: datetime                    # when the event occurred
    kind: Kind
    name: str                       # short identifier, e.g. "treasury_buyback"
    score: float                    # -1.0 (max bearish) .. +1.0 (max bullish)
    confidence: float = 0.5         # 0.0 .. 1.0 - how much you trust this read
    available_at: datetime | None = None   # when it became tradeable
    horizon: timedelta = timedelta(days=3)  # how long it plausibly stays relevant
    source: str = ""
    note: str = ""
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not -1.0 <= self.score <= 1.0:
            raise ValueError(f"score must be in [-1, 1], got {self.score}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if self.available_at is None:
            # Default to the event time. Fine for a live feed you are reading in
            # real time; DANGEROUS for historical data, which is why loaders in
            # this repo set it explicitly.
            self.available_at = self.at
        if self.available_at < self.at:
            raise ValueError(
                f"{self.name}: available_at {self.available_at} is before the event "
                f"at {self.at}. You cannot trade information before it exists."
            )

    @property
    def direction(self) -> Direction:
        if self.score > 0.1:
            return Direction.BULLISH
        if self.score < -0.1:
            return Direction.BEARISH
        return Direction.NEUTRAL

    @property
    def weight(self) -> float:
        """Score tempered by how much we trust it."""
        return self.score * self.confidence

    def is_live_at(self, t: datetime) -> bool:
        """Known by time t, and not yet stale."""
        return self.available_at <= t <= self.available_at + self.horizon

    def decayed_weight(self, t: datetime, half_life_frac: float = 0.5) -> float:
        """Weight that fades as the signal ages.

        News is not a step function. A regulatory headline moves the market on
        day one and is priced in by day five. Linear decay across the horizon
        is crude but far closer to the truth than treating a week-old headline
        as fresh.
        """
        if not self.is_live_at(t):
            return 0.0
        age = (t - self.available_at).total_seconds()
        span = self.horizon.total_seconds()
        if span <= 0:
            return self.weight
        remaining = max(0.0, 1.0 - (age / span))
        return self.weight * remaining


class SignalFeed:
    """A time-ordered store of signals with point-in-time lookup.

    The only way to read from this is `as_of(t)`, which physically cannot
    return anything that was not knowable at t. That is deliberate: a feed you
    can accidentally read the future from is a feed you eventually will.
    """

    def __init__(self, signals: list[Signal] | None = None) -> None:
        self._signals: list[Signal] = []
        self._keys: list[datetime] = []
        for s in signals or []:
            self.add(s)

    def add(self, signal: Signal) -> None:
        i = bisect.bisect_right(self._keys, signal.available_at)
        self._keys.insert(i, signal.available_at)
        self._signals.insert(i, signal)

    def extend(self, signals: list[Signal]) -> None:
        for s in signals:
            self.add(s)

    def __len__(self) -> int:
        return len(self._signals)

    def as_of(self, t: datetime, kinds: set[Kind] | None = None) -> list[Signal]:
        """Every signal knowable at t and still within its horizon."""
        cut = bisect.bisect_right(self._keys, t)
        out = [s for s in self._signals[:cut] if s.is_live_at(t)]
        if kinds:
            out = [s for s in out if s.kind in kinds]
        return out

    def bias_at(self, t: datetime, weights: dict[Kind, float] | None = None,
                decay: bool = True) -> float:
        """Combined directional read at time t, in roughly [-1, +1].

        Signals of the same kind are averaged before kinds are combined, so
        five correlated headlines about one event do not count five times.
        That clustering is the norm, not the exception: a single Treasury
        announcement generates a dozen articles, and naive summing turns one
        fact into an overwhelming signal.
        """
        # One per factor name, freshest only. A factor that fires every bar
        # would otherwise vote once per bar it has been alive.
        live = self.latest_by_name(t)
        if not live:
            return 0.0

        weights = weights or DEFAULT_WEIGHTS
        by_kind: dict[Kind, list[float]] = {}
        for s in live:
            w = s.decayed_weight(t) if decay else s.weight
            by_kind.setdefault(s.kind, []).append(w)

        total = 0.0
        total_w = 0.0
        for kind, vals in by_kind.items():
            kw = weights.get(kind, 1.0)
            total += (sum(vals) / len(vals)) * kw
            total_w += kw
        return total / total_w if total_w else 0.0

    def latest_by_name(self, t: datetime) -> list[Signal]:
        """One signal per name - the freshest.

        A factor that fires on every bar produces a live signal on every bar,
        and ten copies of `compression` saying the same thing is not ten pieces
        of evidence. Collapsing to the newest is what you want for reading and
        for deciding.
        """
        newest: dict[str, Signal] = {}
        for s in self.as_of(t):
            prev = newest.get(s.name)
            if prev is None or s.available_at > prev.available_at:
                newest[s.name] = s
        return list(newest.values())

    def explain(self, t: datetime, verbose: bool = False) -> str:
        """Why the model thinks what it thinks, at time t.

        Read this before trusting any decision the system makes. A model that
        cannot tell you why is a model you cannot debug, and one you should
        certainly not fund.
        """
        live = self.latest_by_name(t)
        if not live:
            return f"{t:%Y-%m-%d %H:%M}  no live signals"

        shown = live if verbose else [s for s in live
                                      if abs(s.decayed_weight(t)) > 0.001
                                      or s.confidence >= 0.6]
        lines = [f"{t:%Y-%m-%d %H:%M}   bias {self.bias_at(t):+.3f}"
                 f"   ({len(live)} live signals)"]
        for s in sorted(shown, key=lambda x: -abs(x.decayed_weight(t))):
            age = (t - s.available_at).days
            w = s.decayed_weight(t)
            marker = "  " if abs(w) > 0.001 else " ~"   # ~ = context, no direction
            lines.append(
                f" {marker}{s.kind.value:<11} {s.name:<22} {w:+.3f}"
                f"  ({s.score:+.2f}x{s.confidence:.2f}, {age}d)  {s.note[:44]}"
            )
        return "\n".join(lines)


# How much each family counts toward the combined read.
#
# These are a starting point, not a discovery. MACRO and FLOW lead because
# liquidity and real buying are what actually move price; SENTIMENT trails
# because by the time retail sentiment is measurable it is usually the move,
# not the cause of it. Re-derive these from your own event studies rather than
# trusting the defaults - `tools/event_study.py` exists for that.
DEFAULT_WEIGHTS: dict[Kind, float] = {
    Kind.MACRO: 1.5,
    Kind.FLOW: 1.3,
    Kind.REGULATORY: 1.2,
    Kind.POSITIONING: 1.0,
    Kind.ONCHAIN: 0.9,
    Kind.TECHNICAL: 0.8,
    Kind.SENTIMENT: 0.4,
}

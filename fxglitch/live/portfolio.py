"""Counting the bet you are actually making.

THE MISTAKE THIS PREVENTS
-------------------------
Twenty alt longs look like twenty positions. A position limit counts twenty. A
risk-per-trade limit sees twenty separate 1% risks and reports 20% at risk in
the worst case, which sounds survivable.

It is not twenty bets. Alts follow BTC, so when BTC drops 5% they do not fail
one at a time in some convenient order - they all lose together, on the same
candle, and the stops fill into the same cascade with the same slippage. The
real position was one leveraged BTC bet, entered twenty times, paying twenty
sets of fees for the privilege of feeling diversified.

Every account that dies in a crypto drawdown dies of this. Not of a bad
strategy - of a risk system that counted the number of tickets instead of the
size of the bet.

HOW EXPOSURE IS COUNTED HERE
----------------------------
Every non-stablecoin perp is treated as BTC exposure, scaled by a beta. The
default beta of 1.0 for alts is deliberately crude and deliberately
conservative: it says an alt long is at least as much of a BTC bet as a BTC
long of the same size. In reality most alts run a beta above 1 - they fall
further - so 1.0 understates the risk and the limits are set accordingly tight.

A measured per-symbol beta would be better and is not hard, but it needs alt
price history this repo does not have yet. Until it does, a crude number that
is honest about being crude beats a precise one that is invented.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not net longs against shorts. A long on one alt and a short on another
is not flat - it is a bet on the SPREAD between them, which is a different
strategy with a different risk profile that nothing here has measured. Netting
them would report a risk of zero for a position that can absolutely lose money.
Opposing exposure is tracked separately and counted at its gross size.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..engine import LONG, SHORT
from ..venues.base import Position
from .guards import Verdict, ALLOW

# Symbols whose correlation to BTC is not ~1. Extend as the platform grows
# beyond crypto: FX majors on Deriv belong here with a beta of 0.
BETA: dict[str, float] = {
    "BTCUSDT": 1.0,
    "ETHUSDT": 1.1,     # historically moves slightly harder than BTC
}
DEFAULT_ALT_BETA = 1.0


def beta(symbol: str) -> float:
    return BETA.get(symbol, DEFAULT_ALT_BETA)


@dataclass
class ExposureLimits:
    """Ceilings on the bet, rather than on the number of tickets.

    max_directional_pct is the headline. At 60, the sum of same-direction
    BTC-equivalent notional cannot exceed 60% of equity - so at 5x leverage
    that is a little over a tenth of the account committed as margin in one
    direction, and a 10% adverse move in BTC costs about 6% of equity rather
    than wiping it.
    """

    max_directional_pct: float = 60.0    # BTC-equivalent notional, one side
    max_gross_pct: float = 100.0         # both sides added, never netted
    max_same_direction: int = 3          # tickets pointing the same way


@dataclass
class Exposure:
    long_notional: float = 0.0
    short_notional: float = 0.0
    long_count: int = 0
    short_count: int = 0
    by_symbol: dict[str, float] = field(default_factory=dict)

    @property
    def gross(self) -> float:
        return self.long_notional + self.short_notional

    @property
    def net(self) -> float:
        """Reported for information, never used as a limit. See module notes."""
        return self.long_notional - self.short_notional

    def side(self, direction: int) -> float:
        return self.long_notional if direction == LONG else self.short_notional

    def count(self, direction: int) -> int:
        return self.long_count if direction == LONG else self.short_count


def measure(positions: list[Position]) -> Exposure:
    """Convert open positions into BTC-equivalent exposure."""
    e = Exposure()
    for p in positions:
        value = p.notional * beta(p.symbol)
        e.by_symbol[p.symbol] = e.by_symbol.get(p.symbol, 0.0) + value
        if p.direction == LONG:
            e.long_notional += value
            e.long_count += 1
        else:
            e.short_notional += value
            e.short_count += 1
    return e


def check_exposure(exposure: Exposure, symbol: str, direction: int,
                   notional: float, equity: float,
                   limits: ExposureLimits) -> Verdict:
    """Would this trade push the real bet past what the account can carry?"""
    if equity <= 0:
        return Verdict(False, "no equity", fatal=True)

    adding = notional * beta(symbol)
    side_after = exposure.side(direction) + adding
    gross_after = exposure.gross + adding
    word = "long" if direction == LONG else "short"

    if exposure.count(direction) >= limits.max_same_direction:
        held = ", ".join(s for s, v in exposure.by_symbol.items())
        return Verdict(False, f"already {exposure.count(direction)} {word} "
                              f"positions ({held}) - that is one BTC bet "
                              f"{exposure.count(direction)} times over")

    side_pct = side_after / equity * 100.0
    if side_pct > limits.max_directional_pct:
        return Verdict(False, f"{word} exposure would reach {side_pct:.0f}% of "
                              f"equity in BTC-equivalent terms, over the "
                              f"{limits.max_directional_pct:.0f}% limit")

    gross_pct = gross_after / equity * 100.0
    if gross_pct > limits.max_gross_pct:
        return Verdict(False, f"gross exposure would reach {gross_pct:.0f}% of "
                              f"equity, over the {limits.max_gross_pct:.0f}% limit")

    return ALLOW


def describe(exposure: Exposure, equity: float) -> str:
    if not exposure.by_symbol:
        return "exposure: flat"
    pct = lambda v: (v / equity * 100.0) if equity > 0 else 0.0
    return (f"exposure: {exposure.long_count} long {pct(exposure.long_notional):.0f}%, "
            f"{exposure.short_count} short {pct(exposure.short_notional):.0f}%, "
            f"gross {pct(exposure.gross):.0f}% of equity (BTC-equivalent)")

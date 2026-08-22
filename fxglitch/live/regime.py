"""BTC as the master switch.

WHY THIS EXISTS
---------------
Alt perps do not move on their own. They move when BTC moves, most of the time,
and harder. So a long on an alt is mostly a long on BTC with extra volatility
and worse liquidity attached - which means judging an alt chart without looking
at BTC first is judging the small part and ignoring the large one.

This module reduces BTC to one of three states and lets the runner use it as a
gate: alt longs blocked while BTC is falling, alt shorts blocked while it is
rising. BTC itself is never gated by its own regime - it is the driver, and it
trades on its own signal.

HOW THE STATE IS DECIDED, AND WHY IT IS THIS AND NOT SOMETHING CLEVERER
-----------------------------------------------------------------------
Price against its own moving average, plus a neutral band around the crossing.

The neutral band is the part that earns its place. A bare above/below test
flickers every time price grazes the average, and each flip changes what the
whole portfolio is allowed to do - so a symbol becomes tradeable and untradeable
on consecutive days for no reason anyone would recognise as a market event. The
band means BTC has to be meaningfully clear of the average before the state
changes.

BE CLEAR ABOUT WHAT THIS IS
---------------------------
A PRIOR, NOT A MEASUREMENT. Nothing in this repo has tested whether gating alt
entries on BTC's trend improves anything. It is a well-founded prior - the
correlation is real and visible in any two crypto charts - but this repo has a
specific history with well-founded priors: DEFAULT_WEIGHTS had MACRO at 1.5 on
reasoning every bit as sensible, and measurement cut it to 0.4.

So the honest framing is that the gate is here to REDUCE CORRELATED EXPOSURE,
which is a risk claim and defensible on its own, rather than to improve
expectancy, which is an edge claim and unproven. If it turns out to cost
expectancy, that is a trade the risk reduction may still be worth - but you
should know which one you are making.

The measurement that would settle it: run ETH and a few alts through
tools/walkforward.py with the gate on and off, out of sample. That needs alt
history this repo does not have yet, which is the honest reason it has not been
done rather than an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data import Series, closes
from ..engine import LONG, SHORT
from ..indicators import ema

UP, DOWN, NEUTRAL = "UP", "DOWN", "NEUTRAL"


@dataclass(frozen=True)
class Regime:
    state: str
    price: float = 0.0
    reference: float = 0.0
    distance_pct: float = 0.0
    detail: str = ""

    def allows(self, direction: int) -> bool:
        """Is a position in this direction permitted for a follower?

        NEUTRAL allows both. It means 'no strong view', and a gate that blocks
        everything whenever it is unsure would stop the system trading for most
        of its life - the cost of which is invisible, unlike a loss, and
        therefore easy to keep paying.
        """
        if self.state == UP:
            return direction == LONG
        if self.state == DOWN:
            return direction == SHORT
        return True


def btc_regime(candles: Series, period: int = 50, band_pct: float = 1.0) -> Regime:
    """Classify BTC from its own candles.

    `band_pct` is the dead zone either side of the average, in percent. At 1.0,
    BTC must sit more than 1% clear of its 50-day average before the state
    commits - which on a market that routinely moves 3% in a day is a low bar
    that still removes most of the flicker.
    """
    if not candles or len(candles) < period + 1:
        return Regime(NEUTRAL, detail=f"need {period + 1} bars, have {len(candles)}")

    reference = ema(closes(candles), period)[-1]
    price = candles[-1].close
    if reference is None or reference <= 0:
        return Regime(NEUTRAL, detail="no reference average yet")

    distance = (price - reference) / reference * 100.0
    if distance > band_pct:
        state = UP
    elif distance < -band_pct:
        state = DOWN
    else:
        state = NEUTRAL

    return Regime(
        state=state, price=price, reference=reference, distance_pct=distance,
        detail=(f"BTC {price:,.0f} is {distance:+.1f}% vs its {period}-bar "
                f"average {reference:,.0f}"),
    )


def gate(regime: Regime, symbol: str, direction: int, *,
         driver: str = "BTCUSDT") -> tuple[bool, str]:
    """Should this entry be allowed, given what BTC is doing?

    The driver is exempt from its own gate. Gating BTC on BTC's trend would
    silently replace the strategy's entry rule with this module's, and the
    strategy is the part that has actually been measured.
    """
    if symbol == driver:
        return True, ""
    if regime.allows(direction):
        return True, ""
    side = "long" if direction == LONG else "short"
    return False, (f"{side} blocked: BTC regime is {regime.state} - "
                   f"{regime.detail}")

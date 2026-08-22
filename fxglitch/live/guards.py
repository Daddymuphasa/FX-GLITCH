"""The reasons to refuse.

Every guard here answers one question: given what we know right now, is it
acceptable to send this order? They exist because the failure modes of a live
trading process are not the failure modes of a backtest.

A backtest cannot lose more than the data allows. A running process can lose
money to things that never appear in a backtest at all: a stale feed that makes
it act on last week's price, a bug that opens the same position forty times, a
correlated basket that looked like twenty independent bets and behaved like one.

WHAT A GUARD IS ALLOWED TO DO
-----------------------------
Refuse, and say why. Nothing else. No guard resizes an order, picks a different
symbol, or waits and retries - a guard that quietly modifies the trade makes
the strategy's behaviour unattributable, and the first thing you need after a
bad week is to know what actually decided what.

THE ASYMMETRY THAT SHAPES THE DEFAULTS
--------------------------------------
A guard that is too tight costs you trades. A guard that is too loose costs you
the account. Those are not comparable, so every default here errs tight, and
every one of them is a number you should raise deliberately rather than a
number to tune for returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..venues.base import Balance, Position


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""
    fatal: bool = False     # fatal means halt the session, not just skip this trade

    def __bool__(self) -> bool:
        return self.ok


ALLOW = Verdict(True)


@dataclass
class Limits:
    """The box the runner is allowed to operate in.

    Defaults are deliberately conservative. `max_positions` at 3 will feel
    restrictive on a 625-symbol universe, and that is intentional until the
    portfolio layer exists: without correlation awareness, twenty alt longs are
    one leveraged BTC bet wearing a disguise, and the account finds out during
    the first cascade rather than in a report.
    """

    risk_pct: float = 1.0                 # percent of equity risked per trade
    max_risk_pct: float = 2.0             # hard ceiling on that, whatever asks
    max_positions: int = 3                # concurrent open positions
    max_leverage: int = 5
    max_daily_loss_pct: float = 6.0       # measured from the day's opening equity
    min_free_balance_pct: float = 20.0    # keep this much uncommitted
    max_bar_age_multiple: float = 2.5     # staleness, in multiples of the bar
    require_stop: bool = True             # never open a position without one


def check_data_fresh(bar_time: datetime, interval_seconds: int, limits: Limits,
                     now: datetime | None = None) -> Verdict:
    """Is the newest closed bar recent enough to act on?

    The dangerous version of a stale feed is not an outage - it is a feed that
    keeps returning the last bar it saw. Nothing errors; the process simply
    trades a market that has moved on. Age is measured in multiples of the bar
    so the same rule works on 1m and 1d.
    """
    now = now or datetime.now(timezone.utc)
    age = (now - bar_time).total_seconds()
    limit = interval_seconds * limits.max_bar_age_multiple
    if age > limit:
        return Verdict(False, f"newest bar is {age / 60:.0f} min old, over the "
                              f"{limit / 60:.0f} min limit - stale feed, not trading",
                       fatal=False)
    return ALLOW


def check_daily_loss(balance: Balance, day_open_equity: float,
                     limits: Limits) -> Verdict:
    """Have we lost more today than the day is allowed to cost?

    Fatal on purpose. A day that is down 6% is a day where something may be
    wrong with the market, the strategy, or the code, and none of those get
    better by continuing to trade while you find out.
    """
    if day_open_equity <= 0:
        return ALLOW
    drop = (day_open_equity - balance.equity) / day_open_equity * 100.0
    if drop >= limits.max_daily_loss_pct:
        return Verdict(False, f"daily loss {drop:.1f}% hit the "
                              f"{limits.max_daily_loss_pct:.1f}% limit "
                              f"({day_open_equity:,.2f} -> {balance.equity:,.2f})",
                       fatal=True)
    return ALLOW


def check_capacity(positions: list[Position], symbol: str,
                   limits: Limits) -> Verdict:
    """Room for another position, and not already in this one."""
    if any(p.symbol == symbol for p in positions):
        return Verdict(False, f"already holding {symbol}")
    if len(positions) >= limits.max_positions:
        held = ", ".join(sorted(p.symbol for p in positions))
        return Verdict(False, f"at the {limits.max_positions}-position limit ({held})")
    return ALLOW


def check_balance(balance: Balance, notional: float, leverage: int,
                  limits: Limits) -> Verdict:
    """Is there margin for this, with room left over?

    The reserve matters more than it looks. An account with every last USDT
    committed as margin has no buffer for adverse moves on the positions it
    already holds, which is how a bad hour becomes a liquidation rather than a
    drawdown.
    """
    if leverage > limits.max_leverage:
        return Verdict(False, f"{leverage}x exceeds the {limits.max_leverage}x limit")
    margin = notional / max(leverage, 1)
    if margin > balance.available:
        return Verdict(False, f"needs {margin:,.2f} margin, {balance.available:,.2f} free")
    remaining = balance.available - margin
    floor = balance.equity * limits.min_free_balance_pct / 100.0
    if remaining < floor:
        return Verdict(False, f"would leave {remaining:,.2f} free, below the "
                              f"{limits.min_free_balance_pct:.0f}% reserve ({floor:,.2f})")
    return ALLOW


def check_risk(risk_amount: float, balance: Balance, limits: Limits) -> Verdict:
    """Is the amount at risk on this trade within the ceiling?

    This catches the sizing bug rather than the sizing choice. If a stop is
    computed absurdly close to the entry, the size needed to risk 1% becomes
    enormous, and the account is one bad tick from ruin while every individual
    number still looks reasonable.
    """
    if balance.equity <= 0:
        return Verdict(False, "no equity", fatal=True)
    pct = risk_amount / balance.equity * 100.0
    if pct > limits.max_risk_pct:
        return Verdict(False, f"risking {pct:.2f}% of equity, over the "
                              f"{limits.max_risk_pct:.2f}% ceiling - check the stop distance")
    return ALLOW


def check_stop(stop_price: float | None, entry_price: float, direction: int,
               limits: Limits) -> Verdict:
    """Is there a stop, and is it on the correct side of the entry?

    A stop above the entry on a long is not a stop, it is an instant exit at a
    loss - and it is exactly what an inverted sign produces. Cheap to check,
    and the kind of bug that only shows up with money on the line.
    """
    if stop_price is None:
        if limits.require_stop:
            return Verdict(False, "no stop price - refusing to open an unprotected position")
        return ALLOW
    if direction > 0 and stop_price >= entry_price:
        return Verdict(False, f"long stop {stop_price:,.4f} is at or above entry "
                              f"{entry_price:,.4f} - the sign is inverted")
    if direction < 0 and stop_price <= entry_price:
        return Verdict(False, f"short stop {stop_price:,.4f} is at or below entry "
                              f"{entry_price:,.4f} - the sign is inverted")
    return ALLOW


def check_all(*verdicts: Verdict) -> Verdict:
    """First refusal wins, and a fatal one is reported as fatal."""
    for v in verdicts:
        if not v.ok:
            return v
    return ALLOW

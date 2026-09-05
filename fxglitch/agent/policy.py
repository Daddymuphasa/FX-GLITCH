"""Deterministic policy check for any proposed Binance futures trade.

An agent may propose. This module answers one question: given the account,
the limits, the positioning briefing, and the order, is it acceptable to
send? Guards refuse. They never resize, never pick a different symbol, and
never retry. A refusal is attributable.

Chart indicators are not consulted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..engine import LONG, SHORT
from ..live.guards import Limits, Verdict, check_all, check_balance, check_capacity, check_daily_loss, check_risk, check_stop
from ..live.portfolio import ExposureLimits, check_exposure, measure
from ..venues.base import Balance, Position
from .positioning import Briefing


@dataclass
class ProposedTrade:
    """What an agent (or a pasted signal) wants to do. Not yet an order."""

    symbol: str
    action: str                  # stand_aside | open | close | reduce
    direction: str | None = None  # LONG | SHORT
    leverage: int = 5
    risk_pct: float = 1.0
    stop_pct: float = 2.0        # risk box, not an indicator
    qty: float | None = None
    entry: float | None = None
    stop: float | None = None
    take_profit: float | None = None
    reason: str = ""
    source: str = "agent"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def side(self) -> int | None:
        if self.direction is None:
            return None
        return LONG if self.direction.upper() == "LONG" else SHORT


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    fatal: bool = False
    checks: list[dict] = field(default_factory=list)
    sized_qty: float | None = None
    sized_notional: float | None = None
    sized_risk: float | None = None
    stop: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _size(proposal: ProposedTrade, entry: float, stop: float, equity: float) -> tuple[float, float, float]:
    """qty, notional, cash at risk. Stop distance is the risk unit (1R)."""
    risk_pct = min(proposal.risk_pct, 100.0)
    cash_at_risk = equity * risk_pct / 100.0
    stop_dist = abs(entry - stop)
    if stop_dist <= 0 or entry <= 0:
        return 0.0, 0.0, cash_at_risk
    qty = cash_at_risk / stop_dist
    notional = qty * entry
    return qty, notional, cash_at_risk


def evaluate(
    proposal: ProposedTrade,
    *,
    balance: Balance,
    positions: list[Position],
    briefing: Briefing | None = None,
    limits: Limits | None = None,
    exposure_limits: ExposureLimits | None = None,
    day_open_equity: float | None = None,
    now: datetime | None = None,
) -> PolicyDecision:
    """Run every hard check. First refusal wins."""
    now = now or datetime.now(timezone.utc)
    limits = limits or Limits()
    exposure_limits = exposure_limits or ExposureLimits()
    checks: list[dict] = []

    def record(name: str, verdict: Verdict) -> Verdict:
        checks.append({"name": name, "ok": verdict.ok, "reason": verdict.reason,
                       "fatal": verdict.fatal})
        return verdict

    if proposal.action == "stand_aside":
        return PolicyDecision(
            allowed=True,
            reason="no order — standing aside is always allowed",
            checks=[{"name": "stand_aside", "ok": True, "reason": proposal.reason, "fatal": False}],
        )

    if proposal.action not in ("open", "close", "reduce"):
        return PolicyDecision(False, f"unknown action {proposal.action!r}", checks=checks)

    if proposal.action == "open":
        if proposal.direction not in ("LONG", "SHORT"):
            return PolicyDecision(False, "open requires direction LONG or SHORT", checks=checks)

        if briefing is not None:
            if proposal.direction == "LONG" and briefing.veto_long:
                v = record("positioning_veto", Verdict(
                    False, f"positioning veto: {briefing.crowd}"))
                return PolicyDecision(False, v.reason, checks=checks)
            if proposal.direction == "SHORT" and briefing.veto_short:
                v = record("positioning_veto", Verdict(
                    False, f"positioning veto: {briefing.crowd}"))
                return PolicyDecision(False, v.reason, checks=checks)

        entry = proposal.entry
        if entry is None and briefing is not None:
            entry = briefing.mark_price
        if entry is None or entry <= 0:
            return PolicyDecision(False, "no mark/entry price to size against", checks=checks)

        stop = proposal.stop
        if stop is None and proposal.stop_pct > 0:
            dist = entry * proposal.stop_pct / 100.0
            stop = entry - dist if proposal.direction == "LONG" else entry + dist

        side = proposal.side or LONG
        v_stop = record("stop", check_stop(stop, entry, side, limits))
        if not v_stop:
            return PolicyDecision(False, v_stop.reason, fatal=v_stop.fatal, checks=checks)

        v_cap = record("capacity", check_capacity(positions, proposal.symbol, limits))
        if not v_cap:
            return PolicyDecision(False, v_cap.reason, fatal=v_cap.fatal, checks=checks)

        v_day = record("daily_loss", check_daily_loss(
            balance, day_open_equity if day_open_equity is not None else balance.equity, limits))
        if not v_day:
            return PolicyDecision(False, v_day.reason, fatal=v_day.fatal, checks=checks)

        qty, notional, cash_at_risk = _size(proposal, entry, stop, balance.equity)
        if briefing is not None:
            qty *= briefing.size_multiplier
            notional *= briefing.size_multiplier
            cash_at_risk *= briefing.size_multiplier

        v_risk = record("risk", check_risk(cash_at_risk, balance, limits))
        if not v_risk:
            return PolicyDecision(False, v_risk.reason, fatal=v_risk.fatal, checks=checks)

        v_bal = record("margin", check_balance(balance, notional, proposal.leverage, limits))
        if not v_bal:
            return PolicyDecision(False, v_bal.reason, fatal=v_bal.fatal, checks=checks)

        exposure = measure(positions)
        v_exp = record("exposure", check_exposure(
            exposure, proposal.symbol, side, notional, balance.equity, exposure_limits))
        if not v_exp:
            return PolicyDecision(False, v_exp.reason, fatal=v_exp.fatal, checks=checks)

        return PolicyDecision(
            allowed=True,
            reason="all guards passed — dry-run until --live / FXGLITCH_LIVE=1",
            checks=checks,
            sized_qty=qty,
            sized_notional=notional,
            sized_risk=cash_at_risk,
            stop=stop,
        )

    # close / reduce: still refuse if we don't hold it
    held = [p for p in positions if p.symbol == proposal.symbol]
    if not held:
        return PolicyDecision(False, f"no open {proposal.symbol} position to {proposal.action}",
                              checks=checks)
    return PolicyDecision(True, f"{proposal.action} allowed on existing {proposal.symbol}",
                          checks=checks, sized_qty=held[0].qty)

"""One cycle: briefing → proposal → policy → optional send.

Default is dry-run. Sending requires both `live=True` and a venue that
is authenticated. That double latch is intentional.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from ..engine import LONG
from ..live.guards import Limits
from ..live.portfolio import ExposureLimits
from ..signal_copy import SignalDirection, parse_signal
from ..venues.base import Balance, OrderRequest, Position, Venue
from .policy import PolicyDecision, ProposedTrade, evaluate
from .positioning import Briefing, fetch_briefing


def paper_balance(equity: float = 1000.0) -> Balance:
    return Balance(currency="USDT", available=equity, used=0.0, unrealised_pnl=0.0)


def from_signal(message: str, *, leverage_cap: int = 5) -> ProposedTrade:
    parsed = parse_signal(message)
    if not parsed.can_open:
        return ProposedTrade(
            symbol=parsed.symbol or "UNKNOWN",
            action="stand_aside",
            reason="signal is not an openable instruction: " + "; ".join(parsed.warnings),
            source="signal",
        )
    lev = int(parsed.leverage) if parsed.leverage is not None else 5
    return ProposedTrade(
        symbol=parsed.symbol or "BTCUSDT",
        action="open",
        direction=parsed.direction.value if parsed.direction else None,
        leverage=min(lev, leverage_cap),
        entry=float(parsed.entry) if parsed.entry is not None else None,
        stop=float(parsed.stop_loss) if parsed.stop_loss is not None else None,
        take_profit=float(parsed.take_profit) if parsed.take_profit is not None else None,
        reason="external signal candidate — still subject to policy",
        source="signal",
    )


def from_briefing(briefing: Briefing) -> ProposedTrade:
    """Positioning never invents an entry. Default is stand aside.

    The intelligence layer may only scale or veto. That constraint is the
    product: failure mode is 'do nothing', not 'guess a breakout'.
    """
    return ProposedTrade(
        symbol=briefing.symbol,
        action="stand_aside",
        reason=(f"{briefing.crowd}. Standing aside. "
                f"Size multiplier if something else proposes: {briefing.size_multiplier:.2f}."),
        source="positioning",
    )


@dataclass
class CycleResult:
    briefing: dict
    proposal: dict
    policy: dict
    sent: bool = False
    dry_run: bool = True
    order: dict | None = None
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def to_dict(self) -> dict:
        return asdict(self)


def run_cycle(
    symbol: str = "BTCUSDT",
    *,
    message: str | None = None,
    live: bool = False,
    venue: Venue | None = None,
    paper_equity: float = 1000.0,
    limits: Limits | None = None,
    exposure_limits: ExposureLimits | None = None,
    briefing: Briefing | None = None,
    proposal: ProposedTrade | None = None,
    positions: list[Position] | None = None,
    balance: Balance | None = None,
    transport=None,
) -> CycleResult:
    limits = limits or Limits()
    exposure_limits = exposure_limits or ExposureLimits()

    if briefing is None:
        kwargs = {} if transport is None else {"transport": transport}
        briefing = fetch_briefing(symbol, **kwargs)

    if proposal is None:
        proposal = from_signal(message, leverage_cap=limits.max_leverage) if message else from_briefing(briefing)

    if venue is not None and getattr(venue, "authenticated", False) and balance is None:
        balance = venue.balance()
        if positions is None:
            positions = venue.positions()
    if balance is None:
        balance = paper_balance(paper_equity)
    if positions is None:
        positions = []

    policy = evaluate(
        proposal,
        balance=balance,
        positions=positions,
        briefing=briefing,
        limits=limits,
        exposure_limits=exposure_limits,
        day_open_equity=balance.equity,
    )

    sent = False
    order = None
    allow_live = live or os.environ.get("FXGLITCH_LIVE") == "1"
    if allow_live and policy.allowed and proposal.action == "open" and venue is not None:
        if not getattr(venue, "authenticated", False):
            policy = PolicyDecision(False, "live send refused: venue has no API keys", checks=policy.checks)
        elif policy.sized_qty:
            side = LONG if proposal.direction == "LONG" else -1
            result = venue.place(OrderRequest(
                symbol=proposal.symbol,
                direction=side,
                qty=Decimal(str(policy.sized_qty)),
                stop_price=Decimal(str(policy.stop)) if policy.stop else None,
                take_profit=(Decimal(str(proposal.take_profit))
                             if proposal.take_profit else None),
                client_id=f"fxg-{proposal.symbol}-{int(datetime.now(timezone.utc).timestamp())}",
                reason=proposal.reason,
            ))
            sent = bool(result.accepted)
            order = {"accepted": result.accepted, "id": result.venue_order_id,
                     "message": result.message}

    return CycleResult(
        briefing=briefing.to_dict(),
        proposal=proposal.to_dict(),
        policy=policy.to_dict(),
        sent=sent,
        dry_run=not sent,
        order=order,
    )


def judge_demo() -> list[dict]:
    """Offline 60-second flow for judges. No network, no keys, no TA.

    Four cases that show what this MCP is for: it is the layer that says no.
    """
    from ..venues.base import Position as VenuePosition

    btc = Briefing(
        symbol="BTCUSDT", as_of="2026-09-06T12:00:00Z", mark_price=110_000.0,
        last_funding=0.00012, funding_label="baseline", open_interest=80_000.0,
        oi_change_pct=1.2, price_change_24h_pct=0.8, oi_quadrant="new_longs",
        long_short_ratio=1.05, crowd="funding near baseline — no crowd signal",
        notes=["Demo fixture. Not a live print."], size_multiplier=1.0,
        veto_long=False, veto_short=False, source="demo",
    )
    crowded = Briefing(
        symbol="BTCUSDT", as_of="2026-09-06T12:00:00Z", mark_price=110_000.0,
        last_funding=0.0012, funding_label="extreme_long", open_interest=80_000.0,
        oi_change_pct=-3.0, price_change_24h_pct=4.0, oi_quadrant="short_squeeze",
        long_short_ratio=1.8, crowd="euphoric longs — they are paying to stay",
        notes=["Demo fixture: funding +0.12%/8h."], size_multiplier=0.4,
        veto_long=True, veto_short=False, source="demo",
    )
    paper = paper_balance(1000.0)
    alts = [
        VenuePosition("ETHUSDT", LONG, 1.0, 4000.0, venue_id="ETHUSDT:LONG"),
        VenuePosition("SOLUSDT", LONG, 20.0, 180.0, venue_id="SOLUSDT:LONG"),
        VenuePosition("DOGEUSDT", LONG, 10_000.0, 0.12, venue_id="DOGEUSDT:LONG"),
    ]

    cases = [
        ("no-stop 20x", ProposedTrade(
            "DOGEUSDT", "open", "LONG", leverage=20, stop_pct=0.0,
            entry=0.12, stop=None, reason="agent asked for 20x with no stop",
            source="demo-agent"), Briefing(
                symbol="DOGEUSDT", as_of="2026-09-06T12:00:00Z", mark_price=0.12,
                last_funding=0.0001, funding_label="baseline", open_interest=1.0,
                oi_change_pct=0.0, price_change_24h_pct=0.0, oi_quadrant="unknown",
                long_short_ratio=1.0, crowd="demo", notes=[], size_multiplier=1.0,
                source="demo"), paper, [], Limits(require_stop=True, max_leverage=5)),
        ("correlated alts", ProposedTrade(
            "WIFUSDT", "open", "LONG", leverage=5, risk_pct=1.0, stop_pct=2.0,
            entry=2.0, reason="fourth alt long — looks diversified",
            source="demo-agent"), btc, paper, alts, Limits(max_positions=10)),
        ("crowded long veto", ProposedTrade(
            "BTCUSDT", "open", "LONG", leverage=5, risk_pct=1.0, stop_pct=2.0,
            entry=110_000.0, reason="agent wants to chase the squeeze",
            source="demo-agent"), crowded, paper, [], Limits()),
        ("clean dry-run", ProposedTrade(
            "BTCUSDT", "open", "LONG", leverage=5, risk_pct=1.0, stop_pct=2.0,
            entry=110_000.0, reason="baseline funding, stop present, 1% risk",
            source="demo-agent"), btc, paper, [], Limits()),
    ]

    out = []
    for title, proposal, briefing, bal, pos, limits in cases:
        result = run_cycle(
            proposal.symbol, live=False, briefing=briefing, proposal=proposal,
            balance=bal, positions=pos, limits=limits,
        )
        out.append({"title": title, **result.to_dict()})
    return out

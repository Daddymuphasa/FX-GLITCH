"""Four risk plans from a Bitunix-style signal and the user's balance.

Stop-loss stays the signal's invalidation. What changes by tier is how much
of the account is risked, the leverage, and which take-profit is used.

Win/loss numbers are the R-multiple of THIS setup (distance to TP vs SL).
They are not a historical win rate and are not a promise.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..live.guards import Limits
from ..signal_copy import ExternalSignal, parse_signal
from ..venues.base import Balance, Instrument
from .pairs import PairMap, map_to_binance
from .policy import ProposedTrade, evaluate
from .session import paper_balance

# Ordered daredevil → low. Caps are ceilings, not targets.
TIERS: tuple[dict, ...] = (
    {
        "id": "daredevil",
        "label": "Daredevil",
        "risk_pct": 8.0,
        "leverage_mult": 2.0,
        "leverage_floor": 15,
        "leverage_cap": 25,
        "tp_index": -1,
        "fallback_r": 4.0,
        "blurb": "Very high risk / reward. A full stop hurts.",
    },
    {
        "id": "high",
        "label": "High risk",
        "risk_pct": 3.0,
        "leverage_mult": 1.0,
        "leverage_floor": 8,
        "leverage_cap": 15,
        "tp_index": -1,
        "fallback_r": 3.0,
        "blurb": "Near the group’s leverage. Size is still aggressive.",
    },
    {
        "id": "mid",
        "label": "Mid risk",
        "risk_pct": 1.0,
        "leverage_mult": 0.5,
        "leverage_floor": 3,
        "leverage_cap": 5,
        "tp_index": 0,
        "fallback_r": 2.0,
        "blurb": "1% of equity at risk. Default if you would shrug at the loss.",
    },
    {
        "id": "low",
        "label": "Low risk",
        "risk_pct": 0.5,
        "leverage_mult": 0.3,
        "leverage_floor": 2,
        "leverage_cap": 3,
        "tp_index": 0,
        "fallback_r": 1.5,
        "blurb": "Smallest size. Same stop, less damage if it is wrong.",
    },
)


@dataclass
class RiskPlan:
    id: str
    label: str
    blurb: str
    leverage: int
    risk_pct: float
    entry: float
    stop: float
    take_profit: float
    qty: float
    notional: float
    margin: float
    loss_if_sl: float
    win_if_tp: float
    reward_risk: float
    policy_ok: bool
    policy_reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Recommendation:
    raw: str
    bitunix_symbol: str | None
    binance_symbol: str | None
    pair: dict
    direction: str | None
    entry: float | None
    entry_is_market: bool
    stop: float | None
    take_profits: list[float]
    signal_leverage: float | None
    warnings: list[str]
    notes: list[str]
    equity: float
    mark: float | None
    can_recommend: bool
    plans: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _f(value) -> float | None:
    return None if value is None else float(value)


def _leverage(signal_lev: float | None, tier: dict, max_lev: int | None) -> int:
    base = signal_lev if signal_lev and signal_lev > 0 else 5.0
    raw = max(base * tier["leverage_mult"], float(tier["leverage_floor"]))
    cap = float(tier["leverage_cap"])
    if max_lev:
        cap = min(cap, float(max_lev))
    return max(1, int(min(raw, cap)))


def _take_profit(signal: ExternalSignal, entry: float, stop: float, tier: dict) -> float:
    tps = [float(x) for x in signal.take_profits] if signal.take_profits else []
    if signal.take_profit is not None:
        first = float(signal.take_profit)
        if first not in tps:
            tps.insert(0, first)
    if tps:
        index = tier["tp_index"]
        if index < 0:
            return tps[-1]
        return tps[min(index, len(tps) - 1)]
    dist = abs(entry - stop)
    r = float(tier["fallback_r"])
    if entry >= stop:
        return entry + dist * r
    return entry - dist * r


def recommend(
    message: str,
    *,
    equity: float = 1000.0,
    mark: float | None = None,
    instruments: dict[str, Instrument] | None = None,
    balance: Balance | None = None,
) -> Recommendation:
    signal = parse_signal(message)
    pair: PairMap = map_to_binance(signal.symbol, instruments)
    warnings = list(signal.warnings)
    notes = [
        "Stop is the signal’s invalidation. Tiers change size and leverage, not the idea.",
        "Win/loss is this setup’s TP vs SL distance. Not a historical win rate.",
    ]
    entry = _f(signal.entry)
    if entry is None and (signal.entry_is_market or mark):
        entry = mark
        if mark:
            notes.append(f"Entry treated as mark {mark}.")
    stop = _f(signal.stop_loss)
    direction = signal.direction.value if signal.direction else None
    tps = [float(x) for x in signal.take_profits] if signal.take_profits else []
    if signal.take_profit is not None and float(signal.take_profit) not in tps:
        tps.insert(0, float(signal.take_profit))
    if entry is None and stop and tps:
        entry = (float(stop) + float(tps[0])) / 2.0
        notes.append("No live mark; entry estimated between stop and first TP so the desk can still size.")

    can = bool(pair.binance and direction and entry and stop and entry > 0 and stop > 0
               and abs(entry - stop) > 0)
    if instruments is not None and not pair.tradeable:
        warnings.append(pair.note)

    rec = Recommendation(
        raw=message,
        bitunix_symbol=pair.bitunix or signal.symbol,
        binance_symbol=pair.binance or None,
        pair=pair.to_dict(),
        direction=direction,
        entry=entry,
        entry_is_market=bool(signal.entry_is_market or signal.entry is None),
        stop=stop,
        take_profits=tps,
        signal_leverage=_f(signal.leverage),
        warnings=warnings,
        notes=notes,
        equity=equity,
        mark=mark,
        can_recommend=can,
    )
    if not can:
        return rec

    bal = balance or paper_balance(equity)
    plans: list[dict] = []
    for tier in TIERS:
        lev = _leverage(rec.signal_leverage, tier, pair.max_leverage)
        tp = _take_profit(signal, entry, stop, tier)
        proposal = ProposedTrade(
            symbol=pair.binance,
            action="open",
            direction=direction,
            leverage=lev,
            risk_pct=tier["risk_pct"],
            stop_pct=0.0,
            entry=entry,
            stop=stop,
            take_profit=tp,
            reason=f"{tier['label']} from Telegram signal",
            source="telegram",
        )
        limits = Limits(
            risk_pct=tier["risk_pct"],
            max_risk_pct=max(tier["risk_pct"], 0.5),
            max_leverage=lev,
            require_stop=True,
        )
        decision = evaluate(proposal, balance=bal, positions=[], limits=limits)
        qty = decision.sized_qty or 0.0
        notional = decision.sized_notional or 0.0
        loss = decision.sized_risk or (equity * tier["risk_pct"] / 100.0)
        rr = abs(tp - entry) / abs(entry - stop)
        win = loss * rr
        ok = decision.allowed
        reason = decision.reason
        if instruments is not None and not pair.tradeable:
            ok = False
            reason = pair.note
        plans.append(RiskPlan(
            id=tier["id"],
            label=tier["label"],
            blurb=tier["blurb"],
            leverage=lev,
            risk_pct=tier["risk_pct"],
            entry=entry,
            stop=stop,
            take_profit=tp,
            qty=qty,
            notional=notional,
            margin=notional / max(lev, 1),
            loss_if_sl=loss,
            win_if_tp=win,
            reward_risk=rr,
            policy_ok=ok,
            policy_reason=reason,
        ).to_dict())
    rec.plans = plans
    rec.can_recommend = any(p["policy_ok"] for p in plans)
    return rec


def live_entry_check(
    *,
    direction: str | None,
    entry: float | None,
    stop: float | None,
    take_profits: list[float],
    mark: float | None,
) -> dict:
    """Is this signal still enterable at the current price?

    Uses the live mark vs the original SL/TP. Does not invent a win rate.
    """
    if direction not in ("LONG", "SHORT"):
        return {"ok": False, "reason": "no direction", "live_rr": None, "mark": mark}
    if stop is None or stop <= 0:
        return {"ok": False, "reason": "no stop", "live_rr": None, "mark": mark}
    if mark is None or mark <= 0:
        return {"ok": False, "reason": "no live mark to judge the entry", "live_rr": None, "mark": mark}
    tp = take_profits[0] if take_profits else None
    if direction == "LONG":
        if mark <= stop:
            return {"ok": False, "reason": "stop already hit (mark at or below SL)", "live_rr": None, "mark": mark}
        if tp is not None and mark >= tp:
            return {"ok": False, "reason": "take-profit already reached", "live_rr": None, "mark": mark}
        risk = mark - stop
        reward = (tp - mark) if tp else None
    else:
        if mark >= stop:
            return {"ok": False, "reason": "stop already hit (mark at or above SL)", "live_rr": None, "mark": mark}
        if tp is not None and mark <= tp:
            return {"ok": False, "reason": "take-profit already reached", "live_rr": None, "mark": mark}
        risk = stop - mark
        reward = (mark - tp) if tp else None
    if risk <= 0:
        return {"ok": False, "reason": "no room to the stop from here", "live_rr": None, "mark": mark}
    live_rr = (reward / risk) if reward is not None else None
    if live_rr is not None and live_rr < 1.0:
        return {"ok": False, "reason": f"live reward:risk is 1:{live_rr:.2f} — chasing, not entering",
                "live_rr": live_rr, "mark": mark}
    if entry and tp:
        span = abs(tp - entry)
        if span > 0:
            progress = abs(mark - entry) / span
            toward_tp = (mark - entry) if direction == "LONG" else (entry - mark)
            if toward_tp > 0 and progress >= 0.8:
                return {"ok": False, "reason": "price already ran ~80% of the way to TP — late",
                        "live_rr": live_rr, "mark": mark}
    reason = "mark still between SL and TP"
    if live_rr is not None:
        reason += f"; live R:R 1:{live_rr:.2f}"
    return {"ok": True, "reason": reason, "live_rr": live_rr, "mark": mark}


def fetch_mark(symbol: str | None) -> float | None:
    if not symbol:
        return None
    try:
        from ..venues.bitunix import Bitunix
        mark = Bitunix().mark_price(symbol)
        if mark:
            return mark
    except Exception:
        pass
    try:
        from ..venues.binance import Binance
        row = Binance()._request("GET", "/fapi/v1/premiumIndex", query={"symbol": symbol})
        value = float(row.get("markPrice") or 0)
        return value or None
    except Exception:
        return None


def plan_by_id(rec: Recommendation, plan_id: str) -> dict | None:
    for plan in rec.plans:
        if plan["id"] == plan_id:
            return plan
    return None


def proposal_from_plan(rec: Recommendation, plan: dict) -> ProposedTrade:
    symbol = rec.bitunix_symbol or rec.binance_symbol or "UNKNOWN"
    return ProposedTrade(
        symbol=symbol,
        action="open",
        direction=rec.direction,
        leverage=int(plan["leverage"]),
        risk_pct=float(plan["risk_pct"]),
        stop_pct=0.0,
        qty=plan.get("qty"),
        entry=plan["entry"],
        stop=plan["stop"],
        take_profit=plan["take_profit"],
        reason=f"{plan['label']} — Telegram {symbol} on Bitunix",
        source="telegram",
    )


def place_plan(venue, rec: Recommendation, plan: dict) -> dict:
    """Set leverage, then market-open with SL/TP on the same Bitunix call."""
    from datetime import datetime, timezone
    from decimal import Decimal

    from ..engine import LONG, SHORT
    from ..venues.base import OrderRequest, VenueError

    symbol = rec.bitunix_symbol or rec.binance_symbol
    if not symbol:
        raise VenueError(getattr(venue, "name", "bitunix"), "no-symbol",
                         "no pair on this signal")
    inst = venue.instruments().get(symbol)
    if inst is None or not inst.tradeable:
        raise VenueError(getattr(venue, "name", "bitunix"), "unknown-symbol",
                         f"{symbol} is not a tradeable Bitunix perpetual")
    qty = inst.round_qty(plan["qty"])
    ok, why = inst.fits(qty)
    if not ok:
        raise VenueError(getattr(venue, "name", "bitunix"), "size-rejected", why)
    venue.set_leverage(symbol, int(plan["leverage"]))
    result = venue.place(OrderRequest(
        symbol=symbol,
        direction=LONG if rec.direction == "LONG" else SHORT,
        qty=qty,
        stop_price=Decimal(str(plan["stop"])),
        take_profit=Decimal(str(plan["take_profit"])),
        client_id=f"fxg-{symbol}-{int(datetime.now(timezone.utc).timestamp())}",
        reason=f"{plan['label']} Telegram {symbol}",
    ))
    return {
        "sent": bool(result.accepted),
        "order_id": result.venue_order_id,
        "message": result.message,
        "symbol": symbol,
        "qty": str(qty),
        "leverage": int(plan["leverage"]),
    }

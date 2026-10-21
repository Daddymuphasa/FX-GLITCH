"""Four risk plans from a Bitunix-style signal and the user's balance.

Stop-loss stays the signal's invalidation. What changes by tier is how much
of the account is risked, the leverage, and which take-profit is used.

Win/loss numbers are the R-multiple of THIS setup (distance to TP vs SL).
They are not a historical win rate and are not a promise.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_UP
from math import isfinite

from ..live.guards import Limits
from ..live.portfolio import ExposureLimits
from ..signal_copy import ExternalSignal, parse_signal
from ..venues.base import Balance, Instrument
from .pairs import PairMap, map_to_binance
from .policy import ProposedTrade, evaluate
from .session import paper_balance

# Ordered daredevil → low. Caps are ceilings, not targets.
WEEKLY_PROFIT_TARGET_PCT = 40.0
TARGET_DAYS_PER_WEEK = 7
DAILY_PROFIT_TARGET_PCT = ((1.0 + WEEKLY_PROFIT_TARGET_PCT / 100.0)
                           ** (1.0 / TARGET_DAYS_PER_WEEK) - 1.0) * 100.0
# Planning allowance, not an exchange fee quote: 0.06% fees + 0.02% slippage.
COST_PCT_PER_SIDE = 0.08
SIZING_VERSION = "2026-09-21-minimum-size"

TIERS: tuple[dict, ...] = (
    {
        "id": "daredevil",
        "label": "Daredevil",
        "risk_pct": 8.0,
        "leverage_mult": 2.0,
        "leverage_add": 0,
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
        "leverage_add": 2,
        "leverage_floor": 8,
        "leverage_cap": 15,
        "tp_index": -1,
        "fallback_r": 3.0,
        "blurb": "Near the group’s leverage. Size is still aggressive.",
    },
    {
        "id": "mid",
        "label": "Average risk",
        "risk_pct": 1.0,
        "leverage_mult": 0.5,
        "leverage_add": 4,
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
        "leverage_add": 2,
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
    profit_if_tp_pct: float
    target_daily_profit_pct: float
    risk_needed_for_daily_target_pct: float
    target_ok: bool
    target_note: str
    policy_ok: bool
    policy_reason: str
    cost_pct_per_side: float = COST_PCT_PER_SIDE
    unrounded_qty: float = 0.0
    minimum_qty: float | None = None
    minimum_loss_if_sl: float | None = None
    minimum_risk_pct: float | None = None
    below_minimum: bool = False

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
    raw = max(base * tier["leverage_mult"] + float(tier.get("leverage_add", 0)),
              float(tier["leverage_floor"]))
    cap = float(tier["leverage_cap"])
    if max_lev:
        cap = min(cap, float(max_lev))
    return max(1, int(min(raw, cap)))


def _take_profit(take_profits: list[float], entry: float, stop: float, tier: dict) -> float:
    tps = list(take_profits)
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
    entry_override: float | None = None,
    stop_override: float | None = None,
    take_profits_override: list[float] | tuple[float, ...] | None = None,
    cost_pct_per_side: float = COST_PCT_PER_SIDE,
    exposure_limits: ExposureLimits | None = None,
) -> Recommendation:
    signal = parse_signal(message)
    pair: PairMap = map_to_binance(signal.symbol, instruments)
    warnings = list(signal.warnings)
    notes = [
        "Stop is the signal’s invalidation. Tiers change size and leverage, not the idea.",
        "Win/loss is this setup’s TP vs SL distance. Not a historical win rate.",
    ]
    notes.append(f"Costs estimated at {cost_pct_per_side:g}% per side; funding excluded. Leverage changes margin, not profit at fixed size.")
    if balance is not None:
        equity = balance.equity
    entry = _f(signal.entry)
    if entry_override not in (None, ""):
        entry = float(entry_override)
    if signal.entry_is_market:
        entry = mark
    if entry is None and (signal.entry_is_market or mark):
        entry = mark
        if mark:
            notes.append(f"Entry treated as mark {mark}.")
    stop = _f(signal.stop_loss)
    if stop_override not in (None, ""):
        stop = float(stop_override)
    direction = signal.direction.value if signal.direction else None
    tps_overridden = take_profits_override is not None
    tps = [float(x) for x in signal.take_profits] if signal.take_profits else []
    if tps_overridden:
        tps = [float(x) for x in take_profits_override if x not in (None, "")]
    if not tps_overridden and signal.take_profit is not None and float(signal.take_profit) not in tps:
        tps.insert(0, float(signal.take_profit))
    if entry is None:
        warnings.append("A real entry or live market price is required; no entry is estimated.")

    can = bool(pair.binance and direction and entry and stop and tps
               and all(isfinite(x) and x > 0 for x in [entry, stop, equity, *tps])
               and isfinite(cost_pct_per_side) and cost_pct_per_side >= 0
               and abs(entry - stop) > 0 and not signal.stop_adjustment)
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
        tp = _take_profit(tps, entry, stop, tier)
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
            cost_pct_per_side=cost_pct_per_side,
        )
        limits = Limits(
            risk_pct=tier["risk_pct"],
            max_risk_pct=max(tier["risk_pct"], 0.5),
            max_leverage=lev,
            require_stop=True,
        )
        cost_rate = cost_pct_per_side / 100.0
        risk_per_unit = abs(entry - stop) + (entry + stop) * cost_rate
        reward_per_unit = (tp - entry) * (1 if direction == "LONG" else -1) - (entry + tp) * cost_rate
        unrounded_qty = equity * tier["risk_pct"] / 100.0 / risk_per_unit
        qty = unrounded_qty
        instrument = instruments.get(pair.binance) if instruments else None
        minimum_qty = minimum_loss = minimum_risk = None
        below_minimum = False
        if instrument:
            qty = float(instrument.round_qty(qty))
            step = Decimal(1).scaleb(-instrument.qty_precision)
            minimum_qty = float(max(instrument.min_qty, step).quantize(step, rounding=ROUND_UP))
            minimum_loss = minimum_qty * risk_per_unit
            minimum_risk = minimum_loss / equity * 100.0
            below_minimum = qty < minimum_qty
        proposal.qty = qty
        decision = evaluate(
            proposal, balance=bal, positions=[], limits=limits,
            exposure_limits=exposure_limits,
        )
        notional = qty * entry
        loss = qty * risk_per_unit
        rr = reward_per_unit / risk_per_unit
        win = qty * reward_per_unit
        profit_if_tp_pct = (win / equity * 100.0) if equity > 0 else 0.0
        risk_needed = (DAILY_PROFIT_TARGET_PCT / rr) if rr > 0 else 0.0
        target_ok = decision.allowed and profit_if_tp_pct >= DAILY_PROFIT_TARGET_PCT
        if target_ok:
            target_note = (
                f"TP would make {profit_if_tp_pct:.2f}% of equity, above the "
                f"{DAILY_PROFIT_TARGET_PCT:.2f}% daily pace for a "
                f"{WEEKLY_PROFIT_TARGET_PCT:.0f}% weekly target."
            )
        else:
            target_note = (
                f"TP would make {profit_if_tp_pct:.2f}% of equity; this setup "
                f"needs about {risk_needed:.2f}% risk at {rr:.2f}R to match "
                f"the {DAILY_PROFIT_TARGET_PCT:.2f}% daily pace."
            )
        ok = decision.allowed
        reason = decision.reason
        if reward_per_unit <= 0:
            ok, reason = False, "take-profit does not cover the estimated trading costs"
        if instrument:
            fits, why = instrument.fits(instrument.round_qty(qty))
            if not fits:
                ok, reason = False, why
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
            profit_if_tp_pct=profit_if_tp_pct,
            target_daily_profit_pct=DAILY_PROFIT_TARGET_PCT,
            risk_needed_for_daily_target_pct=risk_needed,
            target_ok=target_ok and ok,
            target_note=target_note,
            policy_ok=ok,
            policy_reason=reason,
            cost_pct_per_side=cost_pct_per_side,
            unrounded_qty=unrounded_qty,
            minimum_qty=minimum_qty,
            minimum_loss_if_sl=minimum_loss,
            minimum_risk_pct=minimum_risk,
            below_minimum=below_minimum,
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
    if mark is None or not isfinite(mark) or mark <= 0:
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
    try:
        min_live_rr = float(os.environ.get("FXGLITCH_MIN_LIVE_RR", "0.5"))
    except ValueError:
        min_live_rr = 0.5
    if live_rr is not None and live_rr < min_live_rr:
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
        cost_pct_per_side=plan.get("cost_pct_per_side", COST_PCT_PER_SIDE),
    )


def place_plan(venue, rec: Recommendation, plan: dict, *, request_id: str = "",
               exposure_limits: ExposureLimits | None = None) -> dict:
    """Recheck live account and price, then submit once with attached SL/TP."""
    from .protection import ACCOUNT_LOCK

    with ACCOUNT_LOCK:
        return _place_checked(venue, rec, plan, request_id, exposure_limits)


def _place_checked(venue, rec, plan, request_id, exposure_limits=None):
    import hashlib
    from decimal import Decimal

    from ..engine import LONG, SHORT
    from ..venues.base import OrderRequest, VenueError
    from .protection import observe_account

    symbol = rec.bitunix_symbol or rec.binance_symbol
    if not symbol:
        raise VenueError(getattr(venue, "name", "bitunix"), "no-symbol",
                         "no pair on this signal")
    inst = venue.instruments().get(symbol)
    if inst is None or not inst.tradeable:
        raise VenueError(getattr(venue, "name", "bitunix"), "unknown-symbol",
                         f"{symbol} is not a tradeable Bitunix perpetual")
    balance = venue.balance()
    positions = venue.positions()
    state = observe_account(venue, balance)
    if state.halted:
        raise VenueError(venue.name, "account-halted", state.halt_reason)
    mark = venue.mark_price(symbol)
    check = live_entry_check(direction=rec.direction, entry=rec.entry, stop=rec.stop,
                             take_profits=[plan["take_profit"]], mark=mark)
    if not check["ok"]:
        raise VenueError(venue.name, "entry-rejected", check["reason"])
    fresh = recommend(rec.raw, balance=balance, mark=mark, instruments={symbol: inst},
                      entry_override=mark, stop_override=float(inst.round_price(plan["stop"])),
                      take_profits_override=[float(inst.round_price(plan["take_profit"]))],
                      cost_pct_per_side=plan.get("cost_pct_per_side", COST_PCT_PER_SIDE),
                      exposure_limits=exposure_limits)
    live_plan = plan_by_id(fresh, plan["id"])
    if live_plan is None or not live_plan["policy_ok"]:
        raise VenueError(venue.name, "plan-rejected", (live_plan or {}).get("policy_reason") or "invalid live setup")
    # A better price must not silently increase the size the user approved.
    qty = inst.round_qty(min(plan["qty"], live_plan["qty"]))
    ok, why = inst.fits(qty)
    if not ok:
        raise VenueError(getattr(venue, "name", "bitunix"), "size-rejected", why)
    calculated_qty = live_plan["qty"]
    live_plan["qty"] = float(qty)
    live_plan["leverage"] = int(plan["leverage"])
    proposal = proposal_from_plan(fresh, live_plan)
    decision = evaluate(proposal, balance=balance, positions=positions,
                        day_open_equity=state.day_open_equity,
                        limits=Limits(max_risk_pct=float(plan["risk_pct"]),
                                      max_leverage=min(int(plan["leverage"]), inst.max_leverage)),
                        exposure_limits=exposure_limits)
    if not decision.allowed:
        raise VenueError(venue.name, "policy-rejected", decision.reason)
    key = hashlib.sha256((request_id or (state.day + ":" + rec.raw.strip())).encode()).hexdigest()[:28]
    if key in state.orders:
        raise VenueError(venue.name, "duplicate-signal", "this signal was already submitted; check running trades before another attempt")
    venue.set_leverage(symbol, live_plan["leverage"])
    state.orders[key] = {"symbol": symbol, "status": "pending", "day": state.day}
    state.save()
    try:
        result = venue.place(OrderRequest(
            symbol=symbol,
            direction=LONG if rec.direction == "LONG" else SHORT,
            qty=qty,
            stop_price=Decimal(str(live_plan["stop"])),
            take_profit=Decimal(str(live_plan["take_profit"])),
            client_id=f"fxg-{key}",
            reason=f"{plan['label']} Telegram {symbol}",
        ))
    except VenueError as exc:
        if not exc.retryable and exc.code not in ("network", "malformed", "timeout"):
            state.orders.pop(key, None)
            state.save()
        raise
    if not result.accepted:
        state.orders.pop(key, None)
        state.save()
        raise VenueError(venue.name, "order-rejected", result.message or "exchange did not accept the order")
    state.orders[key].update(status="accepted", order_id=result.venue_order_id)
    state.save()
    scale = float(qty) / calculated_qty
    for field_name in ("notional", "loss_if_sl", "win_if_tp", "profit_if_tp_pct"):
        live_plan[field_name] *= scale
    live_plan["margin"] = live_plan["notional"] / live_plan["leverage"]
    live_plan["target_ok"] = live_plan["profit_if_tp_pct"] >= DAILY_PROFIT_TARGET_PCT
    plan.update(live_plan)
    return {
        "sent": bool(result.accepted),
        "order_id": result.venue_order_id,
        "message": result.message,
        "symbol": symbol,
        "qty": str(qty),
        "leverage": live_plan["leverage"],
    }

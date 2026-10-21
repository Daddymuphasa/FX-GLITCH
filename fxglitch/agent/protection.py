"""Durable account targets and monotonic stops shared by the desk and chat."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from threading import RLock

from ..live.guards import Limits, check_daily_loss
from ..live.state import State
from ..venues.base import Balance, Position, VenueError
from .plans import DAILY_PROFIT_TARGET_PCT, WEEKLY_PROFIT_TARGET_PCT

ROOT = Path(__file__).resolve().parents[2] / "data" / "protection"
ACCOUNT_LOCK = RLock()


@dataclass
class AccountState(State):
    week: str = ""
    week_open_equity: float = 0.0
    daily_target_hit: bool = False
    trailing: dict[str, dict] = field(default_factory=dict)
    orders: dict[str, dict] = field(default_factory=dict)

    def observe(self, balance: Balance, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        equity = balance.equity
        if not isfinite(equity) or equity < 0:
            raise VenueError("account", "invalid-equity", "cannot read a valid account equity")
        if self.roll_day(equity, now):
            self.daily_target_hit = False
        week = now.strftime("%G-W%V")
        if self.week != week:
            self.week, self.week_open_equity = week, equity
        if self.day_open_equity > 0 and equity >= self.day_open_equity * (1 + DAILY_PROFIT_TARGET_PCT / 100):
            self.daily_target_hit = True
        daily = check_daily_loss(balance, self.day_open_equity, Limits())
        if not daily:
            self.halted, self.halt_reason = True, "daily loss: " + daily.reason
        self.save()

    def progress(self, equity: float) -> dict:
        return {
            "daily_gain_pct": (equity / self.day_open_equity - 1) * 100 if self.day_open_equity > 0 else 0,
            "weekly_gain_pct": (equity / self.week_open_equity - 1) * 100 if self.week_open_equity > 0 else 0,
            "daily_target_pct": DAILY_PROFIT_TARGET_PCT,
            "weekly_target_pct": WEEKLY_PROFIT_TARGET_PCT,
            "daily_target_hit": self.daily_target_hit,
            "halted": self.halted,
        }


def observe_account(venue, balance: Balance, now: datetime | None = None) -> AccountState:
    slot = int(getattr(venue, "account_id", 1))
    state = AccountState.load(str(ROOT / f"{venue.name}-{slot}.json"))
    state.observe(balance, now)
    return state


def trailing_stop(position: Position, current: float, state: dict, target_hit: bool) -> float | None:
    """Use the tighter of the R ladder and half the open profit after target."""
    entry, qty, pnl = position.entry_price, abs(position.qty), position.unrealised_pnl
    if not all(isfinite(x) for x in (entry, qty, pnl, current)) or entry <= 0 or qty <= 0 or current <= 0:
        return None
    identity = [entry, position.direction, position.opened_at.isoformat() if position.opened_at else None]
    if state.get("identity") != identity:
        state.clear()
        state.update(identity=identity, initial_stop=current)
    initial = float(state["initial_stop"])
    distance = (entry - initial) * position.direction
    candidates = [current, float(state.get("last_stop", current))]
    if distance > 0 and pnl >= distance * qty:
        steps = int(pnl // (2 * distance * qty))
        candidates.append(entry + position.direction * steps * distance)
    if target_hit and pnl > 0:
        candidates.append(entry + position.direction * 0.5 * pnl / qty)
    target = max(candidates) if position.direction == 1 else min(candidates)
    if (target - current) * position.direction <= 0:
        return None
    return target


def protect_account(venue) -> AccountState:
    with ACCOUNT_LOCK:
        state = observe_account(venue, venue.balance())
        positions, stops = venue.positions(), venue.stops()
        active = set()
        for pos in positions:
            key = pos.venue_id or f"{pos.symbol}:{pos.direction}"
            active.add(key)
            current = stops.get(pos.venue_id) or pos.stop_price
            if current is None:
                continue
            # fx-trail owns live Bitunix SL (250% first trail on new tickets).
            state.trailing.setdefault(key, {})
        state.trailing = {key: value for key, value in state.trailing.items() if key in active}
        state.save()
        return state

"""Auto-send Cosmas signals using the Daredevil risk plan. No WhatsApp confirm.

Set FXGLITCH_AUTO_TRADE=1 and FXGLITCH_AUTO_SLOTS=2,3,4,5 (comma list of Bitunix slots).
Each configured slot is attempted once per telegram_id, within 4 hours.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..live.portfolio import ExposureLimits
from ..signal_copy import parse_signal
from ..venues.base import VenueError
from ..venues.bitunix import SLOTS, from_slot
from .plans import fetch_mark, live_entry_check, plan_by_id, place_plan, recommend

ROOT = Path(__file__).resolve().parents[2]
FILLS = ROOT / "data" / "auto_fills.json"
LOG = ROOT / "data" / "auto_trade.log"
LOCK = threading.Lock()
MAX_AGE_HOURS = float(os.environ.get("FXGLITCH_SIGNAL_MAX_AGE_H") or "8")
SEEN = ROOT / "data" / "cosmas_seen.json"
REPEAT_DAYS = float(os.environ.get("FXGLITCH_REPEAT_DAYS") or "21")


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def record_taken(symbol: str, direction: str, telegram_id: str = "manual", at: str | None = None) -> None:
    """Remember symbol+side so Cosmas reminders never reopen the same trade."""
    TAKEN = FILLS.with_name("taken_signals.json")
    rows = _load_json(TAKEN, [])
    rows.append({"symbol": symbol.upper(), "direction": str(direction).upper().replace("SIGNALDIRECTION.", ""),
                 "id": telegram_id, "at": at or _now().strftime("%Y-%m-%dT%H:%M:%SZ")})
    TAKEN.parent.mkdir(parents=True, exist_ok=True)
    TAKEN.write_text(json.dumps(rows[-500:], indent=2), encoding="utf-8")


def recently_taken(symbol: str, direction: str) -> dict | None:
    d = str(direction).upper().replace("SIGNALDIRECTION.", "")
    cut = _now() - timedelta(days=REPEAT_DAYS)
    for row in reversed(_load_json(FILLS.with_name("taken_signals.json"), [])):
        try:
            when = datetime.strptime(row["at"].replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if row.get("symbol") == symbol.upper() and row.get("direction") == d and when >= cut:
            return row
    return None


def already_seen(telegram_id: str, posted_at: str | None) -> bool:
    """Backlog watermark: messages at/before the mark never trade."""
    seen = _load_json(SEEN, {})
    try:
        chat, mid = telegram_id.rsplit(":", 1)
        if str(seen.get("chat")) == chat and seen.get("max_id") and int(mid) <= int(seen["max_id"]):
            return True
    except ValueError:
        pass
    before = seen.get("before")
    if before and posted_at and posted_at <= before:
        return True
    return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enabled() -> bool:
    return os.environ.get("FXGLITCH_AUTO_TRADE", "1").strip().lower() not in ("0", "false", "no")


def slots() -> list[int]:
    raw = os.environ.get("FXGLITCH_AUTO_SLOTS", "2")
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            n = int(part)
            if n in SLOTS and n not in out:
                out.append(n)
    return out or [2]


def _auto_exposure() -> ExposureLimits:
    """Cosmas desk accounts are small; allow ~$5@15x notionals past the 60% research cap."""
    try:
        directional = float(os.environ.get("FXGLITCH_AUTO_MAX_DIRECTIONAL", "300"))
    except ValueError:
        directional = 300.0
    try:
        gross = float(os.environ.get("FXGLITCH_AUTO_MAX_GROSS", "400"))
    except ValueError:
        gross = 400.0
    return ExposureLimits(max_directional_pct=directional, max_gross_pct=gross)


def _log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(_now().strftime("%Y-%m-%dT%H:%M:%SZ ") + msg + "\n")
    except OSError:
        pass


def _load_fills() -> dict:
    try:
        data = json.loads(FILLS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_fills(data: dict) -> None:
    FILLS.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILLS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(FILLS)


def _fresh(posted_at: str | None) -> bool:
    if not posted_at:
        return True
    try:
        when = datetime.strptime(posted_at.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return _now() - when <= timedelta(hours=MAX_AGE_HOURS)


def maybe_execute(text: str, *, telegram_id: str, posted_at: str | None = None) -> dict | None:
    """Place the signal on configured Bitunix slots. Safe to call twice."""
    if not enabled() or os.environ.get("VERCEL"):
        return None
    if not telegram_id:
        return None
    with LOCK:
        fills = _load_fills()
        if telegram_id in fills and not fills[telegram_id].get("retryable"):
            return fills[telegram_id]
    if not _fresh(posted_at):
        return None
    if already_seen(telegram_id, posted_at):
        return None
    try:
        from ..cosmas_coins import learn
        learn(text)
    except Exception:
        pass
    parsed = parse_signal(text)
    if not parsed.symbol or not parsed.direction:
        return None
    if any("contradict" in w or "not between" in w for w in parsed.warnings):
        _log(f"skip {telegram_id} {parsed.warnings}")
        return None
    rep = recently_taken(parsed.symbol, parsed.direction.value)
    if rep:
        _log(f"skip {telegram_id} repeat {parsed.symbol} {parsed.direction.value} taken {rep.get('at')}")
        return {"ok": False, "reason": f"repeat: {parsed.symbol} {parsed.direction.value} already taken {rep.get('at')}",
                "id": telegram_id, "sent": [], "errors": [], "retryable": False}
    if parsed.stop_adjustment and not parsed.stop_loss:
        return None
    with LOCK:
        fills = _load_fills()
        previous = fills.get(telegram_id)
        if previous and not previous.get("retryable"):
            return previous
        # Freeze the slot set at first receipt. Old successful records remain
        # final, so enabling another account cannot backfill old signals.
        target_slots = (previous.get("retryable_slots", []) if previous and previous.get("version") == 2
                        else slots())
        if not target_slots:
            return previous
        result = _send(text, telegram_id, posted_at, target_slots)
        if previous and previous.get("version") == 2:
            result["sent"] = previous.get("sent", []) + result.get("sent", [])
            result["errors"] = previous.get("errors", []) + result.get("errors", [])
            result["blocked_slots"] = previous.get("blocked_slots", []) + result.get("blocked_slots", [])
            result["ok"] = bool(result["sent"])
            result["configured_slots"] = previous.get("configured_slots", target_slots)
        else:
            result["configured_slots"] = target_slots
        result["version"] = 2
        result["retryable"] = bool(result.get("retryable_slots"))
        if result.get("retryable"):
            result["raw"] = text
            result["posted_at"] = posted_at
        fills[telegram_id] = result
        _save_fills(fills)
        if result.get("sent"):
            record_taken(parsed.symbol, parsed.direction.value, telegram_id)
        return result


def retry_pending(limit: int = 10) -> None:
    """Recheck pre-order failures after the next Telegram poll, including restarts."""
    with LOCK:
        pending = [(key, row.get("raw"), row.get("posted_at"))
                   for key, row in _load_fills().items()
                   if isinstance(row, dict) and row.get("retryable") and row.get("raw")]
    for telegram_id, raw, posted_at in pending[:limit]:
        if _fresh(posted_at):
            try:
                maybe_execute(raw, telegram_id=telegram_id, posted_at=posted_at)
            except Exception as exc:
                _log(f"retry failed {telegram_id} {exc}")


def _send(text: str, telegram_id: str, posted_at: str | None,
          target_slots: list[int] | None = None) -> dict:
    target_slots = slots() if target_slots is None else target_slots
    rec = recommend(text, equity=1000.0)
    symbol = rec.bitunix_symbol or rec.binance_symbol
    mark = rec.mark or fetch_mark(symbol)
    if mark and rec.entry is None:
        rec = recommend(text, equity=1000.0, mark=mark)
    check = live_entry_check(
        direction=rec.direction, entry=rec.entry, stop=rec.stop,
        take_profits=rec.take_profits, mark=mark,
    )
    if not check["ok"]:
        _log(f"skip {telegram_id} {check['reason']}")
        retryable_slots = target_slots if check["reason"] == "no live mark to judge the entry" else []
        return {"ok": False, "reason": check["reason"], "id": telegram_id,
                "sent": [], "errors": [], "retryable_slots": retryable_slots,
                "blocked_slots": [] if retryable_slots else target_slots,
                "retryable": bool(retryable_slots)}
    stop = rec.stop
    tp = rec.take_profits[0] if rec.take_profits else None
    if stop is None or tp is None:
        _log(f"skip {telegram_id} missing sl/tp")
        return {"ok": False, "reason": "need stop and take-profit", "id": telegram_id,
                "sent": [], "errors": [], "retryable_slots": [], "blocked_slots": target_slots,
                "retryable": False}
    sent = []
    errors = []
    retryable_slots = []
    blocked_slots = []
    exposure_limits = _auto_exposure()
    for slot in target_slots:
        submitting = False
        try:
            venue = from_slot(slot)
            if not venue.authenticated:
                errors.append(f"slot {slot} no keys")
                retryable_slots.append(slot)
                continue
            inst = venue.instruments().get(symbol)
            if inst is None or not inst.tradeable:
                errors.append(f"slot {slot} {symbol} not listed")
                retryable_slots.append(slot)
                continue
            bal = venue.balance()
            slot_rec = recommend(
                text, balance=bal, mark=mark, instruments={symbol: inst},
                exposure_limits=exposure_limits,
            )
            plan = plan_by_id(slot_rec, "daredevil")
            if plan is None or not plan["policy_ok"]:
                errors.append(f"slot {slot} {(plan or {}).get('policy_reason') or 'Daredevil plan unavailable'}")
                retryable_slots.append(slot)
                continue
            try:
                venue.set_margin_mode(symbol, "CROSS")
            except VenueError:
                pass
            # place_plan re-reads price, balance and positions; enforces the
            # 8% stop-loss risk cap; and persists a stable duplicate guard.
            # Once that path may have sent an order, do not resend on timeout.
            submitting = True
            placed = place_plan(
                venue, slot_rec, plan, request_id=telegram_id,
                exposure_limits=exposure_limits,
            )
            sent.append({"slot": slot, "order": placed["order_id"], "qty": placed["qty"],
                         "lev": placed["leverage"], "margin": "CROSS",
                         "risk_pct": plan["risk_pct"], "estimated_stop_loss": plan["loss_if_sl"]})
            _log(f"sent {symbol} {rec.direction} Daredevil {placed['leverage']}x "
                 f"risk {plan['risk_pct']}% estimated SL ${plan['loss_if_sl']:.2f} "
                 f"qty {placed['qty']} slot {slot} {placed['order_id']}")
        except VenueError as exc:
            uncertain = submitting and exc.code not in (
                "unknown-symbol", "account-halted", "entry-rejected",
                "plan-rejected", "size-rejected", "policy-rejected",
            )
            (blocked_slots if uncertain else retryable_slots).append(slot)
            errors.append(f"slot {slot}: {str(exc)[:180]}")
            _log(f"fail {telegram_id} {exc}")
        except Exception as exc:
            if submitting:
                blocked_slots.append(slot)
            else:
                retryable_slots.append(slot)
            errors.append(f"slot {slot}: {str(exc)[:180]}")
            _log(f"fail {telegram_id} {exc}")
    return {
        "ok": bool(sent),
        "id": telegram_id,
        "symbol": symbol,
        "direction": rec.direction,
        "sent": sent,
        "errors": errors,
        "retryable_slots": retryable_slots,
        "blocked_slots": blocked_slots,
        "retryable": bool(retryable_slots),
        "at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

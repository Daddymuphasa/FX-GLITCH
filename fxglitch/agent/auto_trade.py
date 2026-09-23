"""Auto-send Cosmas signals to Bitunix. No WhatsApp confirm.

Set FXGLITCH_AUTO_TRADE=1 and FXGLITCH_AUTO_SLOTS=2 (comma list).
Only fills a telegram_id once, and only if posted in the last 4 hours.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from ..signal_copy import parse_signal
from ..venues.base import LONG, SHORT, OrderRequest, VenueError
from ..venues.bitunix import from_slot
from .plans import fetch_mark, live_entry_check, recommend

ROOT = Path(__file__).resolve().parents[2]
FILLS = ROOT / "data" / "auto_fills.json"
LOG = ROOT / "data" / "auto_trade.log"
LOCK = threading.Lock()
MAX_AGE_HOURS = 4


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
            if n in (1, 2, 3):
                out.append(n)
    return out or [2]


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
        if telegram_id in fills:
            return fills[telegram_id]
    if not _fresh(posted_at):
        return None
    parsed = parse_signal(text)
    if not parsed.symbol or not parsed.direction:
        return None
    if parsed.stop_adjustment and not parsed.stop_loss:
        return None
    with LOCK:
        fills = _load_fills()
        if telegram_id in fills:
            return fills[telegram_id]
        result = _send(text, telegram_id, posted_at)
        fills[telegram_id] = result
        _save_fills(fills)
        return result


def _send(text: str, telegram_id: str, posted_at: str | None) -> dict:
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
        return {"ok": False, "reason": check["reason"], "id": telegram_id}
    stop = rec.stop
    tp = rec.take_profits[0] if rec.take_profits else None
    if stop is None or tp is None:
        _log(f"skip {telegram_id} missing sl/tp")
        return {"ok": False, "reason": "need stop and take-profit", "id": telegram_id}
    lev = int(rec.signal_leverage or 5)
    lev = max(1, min(lev, 25))
    side = LONG if rec.direction == "LONG" else SHORT
    sent = []
    errors = []
    for slot in slots():
        try:
            venue = from_slot(slot)
            if not venue.authenticated:
                errors.append(f"slot {slot} no keys")
                continue
            inst = venue.instruments().get(symbol)
            if inst is None or not inst.tradeable:
                errors.append(f"slot {slot} {symbol} not listed")
                continue
            qty = inst.min_qty
            ok, why = inst.fits(qty)
            if not ok:
                errors.append(f"slot {slot} {why}")
                continue
            try:
                venue.set_margin_mode(symbol, "CROSS")
            except VenueError:
                pass
            venue.set_leverage(symbol, min(lev, inst.max_leverage))
            order = venue.place(OrderRequest(
                symbol=symbol,
                direction=side,
                qty=qty,
                stop_price=Decimal(str(stop)),
                take_profit=Decimal(str(tp)),
                client_id=f"fxg-auto-{telegram_id}"[:36],
                reason=f"auto Cosmas {rec.direction} {symbol}",
            ))
            sent.append({"slot": slot, "order": order.venue_order_id, "qty": str(qty)})
            _log(f"sent {symbol} {rec.direction} slot {slot} {order.venue_order_id}")
        except VenueError as exc:
            errors.append(str(exc)[:200])
            _log(f"fail {telegram_id} {exc}")
        except Exception as exc:
            errors.append(str(exc)[:200])
            _log(f"fail {telegram_id} {exc}")
    return {
        "ok": bool(sent),
        "id": telegram_id,
        "symbol": symbol,
        "direction": rec.direction,
        "sent": sent,
        "errors": errors,
        "at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

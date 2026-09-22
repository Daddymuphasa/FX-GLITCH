"""Trail Bitunix SL every 200% ROI on every open position.

At 200% move stop to entry (breakeven). At 400% lock the 200% price, etc.
Never moves the stop against the position. Runs forever on the VPS.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

if Path("/app/.env").exists():
    ROOT = Path("/app")
elif Path("/opt/fx-glitch/.env").exists():
    ROOT = Path("/opt/fx-glitch")
else:
    ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ORIG = ROOT / "data" / "trail_orig.json"
WATCH = ROOT / "data" / "trail_watch.json"
STEP = 200.0
SLEEP = 20
DESK = os.environ.get("FXG_DESK", "http://127.0.0.1:8765")


def load_env() -> None:
    path = ROOT / ".env"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def slots() -> list[int]:
    raw = os.environ.get("FXGLITCH_AUTO_SLOTS", "2")
    out = []
    for part in raw.split(","):
        if part.strip().isdigit():
            n = int(part.strip())
            if n in (1, 2, 3):
                out.append(n)
    return out or [2]


def _orig() -> dict:
    try:
        data = json.loads(ORIG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_orig(data: dict) -> None:
    ORIG.parent.mkdir(parents=True, exist_ok=True)
    ORIG.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _watch() -> dict:
    try:
        data = json.loads(WATCH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_watch(data: dict) -> None:
    WATCH.parent.mkdir(parents=True, exist_ok=True)
    WATCH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def alert(text: str) -> None:
    body = json.dumps({"action": "alert", "text": text}).encode()
    req = urllib.request.Request(
        DESK + "/api/whatsapp",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
        print("ALERT", text.replace("\n", " | "), flush=True)
    except Exception as exc:
        print("ALERT_FAIL", str(exc)[:120], flush=True)


def roi(side: str, entry: float, mark: float, lev: float) -> float:
    if entry <= 0 or lev <= 0:
        return 0.0
    if side == "SHORT":
        return (entry - mark) / entry * lev * 100.0
    return (mark - entry) / entry * lev * 100.0


def lock_price(side: str, entry: float, lev: float, lock_roi: float) -> float:
    frac = lock_roi / 100.0 / lev
    if side == "SHORT":
        return entry * (1.0 - frac)
    return entry * (1.0 + frac)


def trail_one(venue, pos, orig: dict) -> None:
    mark = venue.mark_price(pos.symbol)
    if not mark:
        return
    entry = float(pos.entry_price)
    lev = float(pos.leverage or 5)
    side = pos.side
    r = roi(side, entry, float(mark), lev)
    steps = int(r // STEP)
    pid = pos.venue_id
    resting = venue.stops(pos.symbol).get(pid)
    if pid not in orig:
        orig[pid] = resting if resting else (0.09 if side == "SHORT" else 0.0)
        if side == "LONG" and not orig[pid] and pos.stop_price:
            orig[pid] = float(pos.stop_price)
        _save_orig(orig)
    first_sl = float(orig.get(pid) or 0)
    print(
        f"{side} {pos.symbol} mark {mark} entry {entry} roi {r:.1f}% lev {lev} steps {steps}",
        flush=True,
    )
    if steps < 1:
        return
    lock_roi = (steps - 1) * STEP
    new_sl = lock_price(side, entry, lev, lock_roi)
    if side == "SHORT":
        if first_sl:
            new_sl = min(first_sl, new_sl)
        if new_sl <= float(mark) * 1.002:
            return
        if resting is not None and new_sl >= resting - 1e-12:
            return
    else:
        if first_sl:
            new_sl = max(first_sl, new_sl)
        if new_sl >= float(mark) * 0.998:
            return
        if resting is not None and new_sl <= resting + 1e-12:
            return
    result = venue.set_stop(pos, new_sl)
    print("TRAIL", pos.symbol, "sl", new_sl, "lock_roi", lock_roi, result.venue_order_id, flush=True)
    if lock_roi <= 0:
        alert(
            f"{pos.symbol} {side}: 200% in profit.\n"
            f"Stop moved to breakeven ({new_sl:.6g})."
        )
    else:
        alert(
            f"{pos.symbol} {side}: another 200% in profit.\n"
            f"Stop trailed to lock {lock_roi:.0f}% ({new_sl:.6g})."
        )


def _classify_close(snap: dict, mark: float | None) -> str:
    side = snap.get("side")
    sl = snap.get("sl")
    tp = snap.get("tp")
    liq = snap.get("liq")
    if mark is None:
        return "closed"
    if liq:
        if side == "SHORT" and mark >= float(liq) * 0.99:
            return "liquidated"
        if side == "LONG" and mark <= float(liq) * 1.01:
            return "liquidated"
    if tp:
        if side == "SHORT" and mark <= float(tp) * 1.01:
            return "take-profit hit"
        if side == "LONG" and mark >= float(tp) * 0.99:
            return "take-profit hit"
    if sl:
        if side == "SHORT" and mark >= float(sl) * 0.99:
            return "stop-loss hit"
        if side == "LONG" and mark <= float(sl) * 1.01:
            return "stop-loss hit"
    return "closed"


def once() -> None:
    from fxglitch.venues.bitunix import from_slot

    orig = _orig()
    watch = _watch()
    live_ids: set[str] = set()
    for slot in slots():
        venue = from_slot(slot)
        if not venue.authenticated:
            continue
        rows = venue.positions()
        if not rows:
            print(f"slot {slot} flat", flush=True)
        sl_map = {}
        tp_map = {}
        try:
            sl_map = venue.stops()
            tp_map = venue.targets()
        except Exception:
            pass
        for pos in rows:
            live_ids.add(pos.venue_id)
            mark = venue.mark_price(pos.symbol)
            watch[pos.venue_id] = {
                "slot": slot,
                "symbol": pos.symbol,
                "side": pos.side,
                "entry": pos.entry_price,
                "qty": pos.qty,
                "lev": pos.leverage,
                "sl": sl_map.get(pos.venue_id),
                "tp": tp_map.get(pos.venue_id),
                "liq": pos.liquidation_price,
                "mark": mark,
            }
            try:
                trail_one(venue, pos, orig)
            except Exception as exc:
                print("ERR", pos.symbol, type(exc).__name__, str(exc)[:200], flush=True)
    from fxglitch.venues.bitunix import from_slot as _slot
    for pid, snap in list(watch.items()):
        if pid in live_ids:
            continue
        mark = snap.get("mark")
        try:
            mark = _slot(int(snap.get("slot") or 2)).mark_price(snap.get("symbol")) or mark
        except Exception:
            pass
        why = _classify_close(snap, mark)
        alert(
            f"{snap.get('symbol')} {snap.get('side')}: {why}.\n"
            f"Entry {snap.get('entry')}  qty {snap.get('qty')}."
        )
        watch.pop(pid, None)
        orig.pop(pid, None)
    _save_orig(orig)
    _save_watch(watch)


def main() -> int:
    load_env()
    print("trail 200% slots", slots(), flush=True)
    while True:
        try:
            once()
        except Exception as exc:
            print("ERR", type(exc).__name__, str(exc)[:250], flush=True)
        time.sleep(SLEEP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

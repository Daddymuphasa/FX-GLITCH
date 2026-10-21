"""Trail Bitunix SL on every open position.

New tickets wait until +200% ROI, then move SL to entry. Each extra 200%
locks the prior step. Positions already being trailed keep their original
step size. Never moves the stop against the position.
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
LEGACY_STEP = 100.0
SLEEP = int(os.environ.get("FXGLITCH_TRAIL_SLEEP", "60"))
DESK = os.environ.get("FXG_DESK", "http://127.0.0.1:8765")


def trail_step() -> float:
    return float(os.environ.get("FXGLITCH_TRAIL_STEP", "200"))


def load_env() -> None:
    path = ROOT / ".env"
    # Prefer host .env over stale compose-baked values for slot/trail knobs.
    prefer = {
        "FXGLITCH_AUTO_SLOTS",
        "FXGLITCH_TRAIL_STEP",
        "FXGLITCH_TRAIL_SLEEP",
        "FXGLITCH_AUTO_TRADE",
        "AUTO_TRADE",
    }
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip().strip('"').strip("'")
        if key in prefer or key not in os.environ:
            os.environ[key] = val


def slots() -> list[int]:
    # Allow Bitunix slots 1–5 (KarlaGod = 5). Prefer .env over process env.
    raw = os.environ.get("FXGLITCH_AUTO_SLOTS", "2,3,4,5")
    try:
        from fxglitch.venues.bitunix import SLOTS as BITUNIX_SLOTS
        allowed = set(BITUNIX_SLOTS)
    except Exception:
        allowed = {1, 2, 3, 4, 5}
    out = []
    for part in raw.split(","):
        if part.strip().isdigit():
            n = int(part.strip())
            if n in allowed and n not in out:
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


def _at_or_beyond_be(side: str, sl: float, entry: float) -> bool:
    if side == "SHORT":
        return sl <= entry * 1.002
    return sl >= entry * 0.998


def trail_one(venue, pos, orig: dict) -> None:
    mark = venue.mark_price(pos.symbol)
    if not mark:
        return
    entry = float(pos.entry_price)
    lev = float(pos.leverage or 5)
    side = pos.side
    r = roi(side, entry, float(mark), lev)
    pid = pos.venue_id
    resting = venue.stops(pos.symbol).get(pid)
    meta = orig.get(pid)
    if isinstance(meta, (int, float)):
        meta = {"sl": float(meta), "step": 0, "be": False, "step_size": LEGACY_STEP}
    if not isinstance(meta, dict):
        meta = {
            "sl": float(resting or (pos.stop_price or 0) or 0),
            "step": 0,
            "be": False,
            "step_size": trail_step(),
        }
        orig[pid] = meta
        _save_orig(orig)
    step_size = float(meta.get("step_size") or LEGACY_STEP)
    steps = max(0, int(r // step_size))
    last_step = int(meta.get("step") or 0)
    first_sl = float(meta.get("sl") or 0)
    # First trail is at +step_size ROI: SL to entry. Later steps lock the prior step.
    if steps < 1 or steps <= last_step:
        return
    lock_roi = (steps - 1) * step_size
    new_sl = entry if lock_roi <= 0 else lock_price(side, entry, lev, lock_roi)
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
    current = venue.stops(pos.symbol).get(pid)
    if current is None:
        print("TRAIL_FAIL", pos.symbol, "no stop after set", flush=True)
        return
    if side == "SHORT" and current > new_sl * 1.002:
        print("TRAIL_FAIL", pos.symbol, "sl still", current, "wanted", new_sl, flush=True)
        return
    if side == "LONG" and current < new_sl * 0.998:
        print("TRAIL_FAIL", pos.symbol, "sl still", current, "wanted", new_sl, flush=True)
        return
    meta["step"] = steps
    if _at_or_beyond_be(side, current, entry):
        meta["be"] = True
    orig[pid] = meta
    _save_orig(orig)
    print("TRAIL", pos.symbol, "roi", round(r, 1), "sl", current, result.venue_order_id, flush=True)
    pct = int(steps * step_size)
    if lock_roi <= 0:
        alert(f"{pos.symbol} {side}: {pct}% in profit.\nStop moved to breakeven ({current:.6g}).")
    else:
        alert(
            f"{pos.symbol} {side}: {pct}% in profit.\n"
            f"Stop trailed to lock {lock_roi:.0f}% ({current:.6g})."
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
    print("trail step", trail_step(), "slots", slots(), flush=True)
    while True:
        try:
            once()
        except Exception as exc:
            print("ERR", type(exc).__name__, str(exc)[:250], flush=True)
        time.sleep(SLEEP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

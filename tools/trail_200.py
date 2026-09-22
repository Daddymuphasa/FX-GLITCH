"""Trail Bitunix SL every 200% ROI. Account 2, MUBARAKUSDT short.

At 200% move stop to entry (breakeven). At 400% lock the 200% price, etc.
Never moves the stop against the position. Laptop can be off — run on the VPS.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

if Path("/app/.env").exists():
    ROOT = Path("/app")
elif Path("/opt/fx-glitch/.env").exists():
    ROOT = Path("/opt/fx-glitch")
else:
    ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env() -> None:
    path = ROOT / ".env"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


SYMBOL = "MUBARAKUSDT"
SLOT = 2
LEV = 5.0
STEP = 200.0
ORIG_SL = 0.09
SLEEP = 20


def roi_short(entry: float, mark: float, lev: float) -> float:
    if entry <= 0:
        return 0.0
    return (entry - mark) / entry * lev * 100.0


def lock_stop_short(entry: float, lev: float, lock_roi: float) -> float:
    return entry * (1.0 - lock_roi / 100.0 / lev)


def once() -> bool:
    """Return False when the position is gone."""
    from fxglitch.venues.bitunix import from_slot

    venue = from_slot(SLOT)
    rows = [p for p in venue.positions(SYMBOL) if p.side == "SHORT"]
    if not rows:
        print("FLAT", flush=True)
        return False
    pos = rows[0]
    mark = venue.mark_price(SYMBOL) or pos.entry_price
    entry = float(pos.entry_price)
    roi = roi_short(entry, float(mark), LEV)
    steps = int(roi // STEP)
    print(f"mark {mark} entry {entry} roi {roi:.1f}% steps {steps}", flush=True)
    if steps < 1:
        return True
    lock_roi = (steps - 1) * STEP
    new_sl = lock_stop_short(entry, LEV, lock_roi)
    new_sl = min(ORIG_SL, new_sl)
    if new_sl <= float(mark) * 1.002:
        print("skip sl would be at/through mark", new_sl, mark, flush=True)
        return True
    resting = venue.stops(SYMBOL).get(pos.venue_id)
    if resting is not None and new_sl >= resting - 1e-8:
        return True
    result = venue.set_stop(pos, new_sl)
    print("TRAIL sl", new_sl, "lock_roi", lock_roi, "order", result.venue_order_id, flush=True)
    return True


def main() -> int:
    load_env()
    print("trail 200% on", SYMBOL, "slot", SLOT, flush=True)
    while True:
        try:
            if not once():
                return 0
        except Exception as exc:
            print("ERR", type(exc).__name__, str(exc)[:250], flush=True)
        time.sleep(SLEEP)


if __name__ == "__main__":
    raise SystemExit(main())

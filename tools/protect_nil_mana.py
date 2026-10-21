#!/usr/bin/env python3
"""Restore Cosmas NIL/MANA SL+TP on slots 2/3/4 without loosening. Seed trail 200."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

os.chdir("/app")
os.environ.setdefault("PYTHONPATH", "/app")

for line in Path("/app/.env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from fxglitch.venues.bitunix import from_slot

TARGETS = {
    "NILUSDT": {"sl": 0.11934, "tp": 0.023},
    "MANAUSDT": {"sl": 0.12699, "tp": 0.02309},
}
SLOTS = (2, 3, 4)
TRAIL = Path("/app/data/trail_orig.json")
STEP = 200


def load_trail() -> dict:
    if TRAIL.exists():
        try:
            return json.loads(TRAIL.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_trail(data: dict) -> None:
    TRAIL.parent.mkdir(parents=True, exist_ok=True)
    TRAIL.write_text(json.dumps(data, indent=2), encoding="utf-8")


def ensure_tpsl(v, pos, want_sl: float, want_tp: float) -> list[str]:
    acts: list[str] = []
    pid = str(pos.venue_id or "")
    stops = v.stops(pos.symbol)
    tps = v.targets(pos.symbol)
    cur_sl = stops.get(pid)
    cur_tp = tps.get(pid)
    need_sl = cur_sl is None or float(cur_sl) > want_sl * 1.001
    need_tp = cur_tp is None
    if not need_sl and not need_tp:
        acts.append(f"OK sl={cur_sl} tp={cur_tp}")
        return acts
    if need_sl and not need_tp:
        try:
            v.set_stop(pos, want_sl)
            acts.append(f"SET_SL {want_sl} (kept tp={cur_tp})")
            return acts
        except Exception as e:
            acts.append(f"set_stop_err {e}")
    try:
        inst = v.instruments().get(pos.symbol)
        sl_p = inst.round_price(want_sl) if inst else Decimal(str(want_sl))
        tp_p = inst.round_price(want_tp) if inst else Decimal(str(want_tp))
        final_tp = cur_tp if cur_tp is not None and not need_tp else float(tp_p)
        final_sl = want_sl if need_sl else float(cur_sl)
        payload = {
            "symbol": pos.symbol,
            "positionId": pid,
            "slPrice": str(sl_p if need_sl else cur_sl),
            "slStopType": "MARK_PRICE",
            "tpPrice": str(final_tp),
            "tpStopType": "MARK_PRICE",
        }
        rows = [r for r in v._pending_tpsl(pos.symbol) if str(r.get("positionId", "") or "") == pid]
        for row in rows:
            oid = v._tpsl_oid(row) if hasattr(v, "_tpsl_oid") else (row.get("orderId") or row.get("id"))
            if not oid:
                continue
            try:
                v._request(
                    "POST",
                    "/api/v1/futures/tpsl/cancel_order",
                    payload={"symbol": pos.symbol, "orderId": str(oid)},
                    private=True,
                )
            except Exception:
                pass
        v._request(
            "POST",
            "/api/v1/futures/tpsl/position/place_order",
            payload=payload,
            private=True,
        )
        new_sl = v.stops(pos.symbol).get(pid)
        new_tp = v.targets(pos.symbol).get(pid)
        acts.append(f"PLACE_TPSL sl={new_sl} tp={new_tp} wanted_sl={final_sl} wanted_tp={final_tp}")
    except Exception as e:
        acts.append(f"place_tpsl_err {type(e).__name__}: {e}")
    return acts


def main() -> None:
    trail = load_trail()
    any_open = False
    for s in SLOTS:
        v = from_slot(s)
        for pos in v.positions() or []:
            want = TARGETS.get(pos.symbol)
            if not want:
                continue
            if getattr(pos, "direction", None) not in (-1, "SHORT") and getattr(pos, "side", None) != "SHORT":
                if getattr(pos, "direction", None) != -1:
                    continue
            any_open = True
            pid = str(pos.venue_id or "")
            print(f"slot{s} {pos.symbol} qty={pos.qty} entry={pos.entry_price} pid={pid}")
            print(" ", "stops", v.stops(pos.symbol).get(pid), "tps", v.targets(pos.symbol).get(pid))
            for a in ensure_tpsl(v, pos, want["sl"], want["tp"]):
                print(" ", a)
            if pid:
                meta = trail.get(pid) if isinstance(trail.get(pid), dict) else {}
                step = float(meta.get("step_size") or 0)
                if step != float(STEP) or meta.get("mode") != "trail_orig" or not meta.get("symbol"):
                    trail[pid] = {
                        "symbol": pos.symbol,
                        "side": "SHORT",
                        "slot": s,
                        "entry": float(pos.entry_price) if pos.entry_price is not None else None,
                        "sl": float(want["sl"]),
                        "tp": float(want["tp"]),
                        "step": int(meta.get("step") or 0),
                        "be": bool(meta.get("be") or False),
                        "step_size": float(STEP),
                        "mode": "trail_orig",
                        "source": "protect_nil_mana",
                        "note": f"trail200_{pos.symbol}",
                    }
                    print(f"  SEED_TRAIL step={STEP}")
                else:
                    print(f"  TRAIL_OK step={step}")
    save_trail(trail)
    print("ANY_OPEN", any_open)


if __name__ == "__main__":
    main()
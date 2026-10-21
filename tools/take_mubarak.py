"""Open MUBARAKUSDT short on Bitunix account 2, then trail SL every 200% ROI."""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


MSG = """Mubarak/usdt
Short position
Entry CMP
Tp 0.02300
Sl 0.0900
Leverage 5x
"""


def main() -> int:
    load_env()
    from fxglitch.agent.plans import place_plan, plan_by_id, recommend
    from fxglitch.venues.bitunix import from_slot

    slot = 2
    venue = from_slot(slot)
    if not venue.authenticated:
        print("account 2 has no keys")
        return 1
    inst = venue.instruments()
    symbol = "MUBARAKUSDT"
    if symbol not in inst:
        names = [s for s in inst if "MUBARAK" in s.upper() or "MUBAR" in s.upper()]
        print("symbol missing", names[:20])
        if names:
            symbol = names[0]
        else:
            return 1
    mark = venue.mark_price(symbol)
    bal = venue.balance()
    print("symbol", symbol, "mark", mark, "equity", round(bal.equity, 4), "avail", round(bal.available, 4))
    rec = recommend(MSG, equity=bal.equity, mark=mark, instruments=inst, balance=bal)
    print("dir", rec.direction, "entry", rec.entry, "stop", rec.stop, "tps", rec.take_profits, "can", rec.can_recommend)
    # Prefer the plan that uses 5x if possible, else mid, else first policy_ok
    chosen = None
    for p in rec.plans:
        if p.get("policy_ok") and int(p.get("leverage") or 0) == 5:
            chosen = p
            break
    if chosen is None:
        chosen = plan_by_id(rec, "mid") or next((p for p in rec.plans if p.get("policy_ok")), None)
    if chosen is None:
        print("no plan", [(p["id"], p.get("policy_ok"), p.get("policy_reason")) for p in rec.plans])
        return 1
    # Force the signal's 5x and the given SL/TP
    chosen = dict(chosen)
    chosen["leverage"] = 5
    chosen["stop"] = 0.09
    chosen["take_profit"] = 0.023
    info = inst[symbol]
    qty = info.round_qty(max(Decimal(str(chosen["qty"])), info.min_qty))
    ok, why = info.fits(qty)
    if not ok:
        print("size refused", why)
        return 1
    print("using", chosen["id"], "qty", qty, "lev 5 sl 0.09 tp 0.023")
    from datetime import datetime, timezone
    from fxglitch.venues.base import SHORT, OrderRequest
    venue.set_leverage(symbol, 5)
    result = venue.place(OrderRequest(
        symbol=symbol,
        direction=SHORT,
        qty=qty,
        stop_price=Decimal("0.09"),
        take_profit=Decimal("0.023"),
        client_id=f"fxg-mubarak-{int(datetime.now(timezone.utc).timestamp())}",
        reason="Mubarak short CMP 5x trail 200%",
    ))
    print("placed", result.accepted, result.venue_order_id, result.message)
    time.sleep(2)
    print("positions", [(p.symbol, p.side, p.qty, p.entry_price, p.venue_id, round(p.unrealised_pnl, 4)) for p in venue.positions(symbol)])
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())

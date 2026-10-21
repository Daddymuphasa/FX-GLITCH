"""Close every open Bitunix futures position on accounts 1–3."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def main() -> int:
    load_env()
    from fxglitch.venues.bitunix import from_slot, listed_accounts

    print("accounts", listed_accounts())
    for slot in (1, 2, 3):
        venue = from_slot(slot)
        if not venue.authenticated:
            print("slot", slot, "no keys")
            continue
        try:
            rows = venue.positions()
        except Exception as exc:
            print("slot", slot, "list failed", str(exc)[:250])
            continue
        print("slot", slot, "open", [(p.symbol, p.side, p.qty, p.venue_id, round(p.unrealised_pnl, 4)) for p in rows])
        for pos in rows:
            try:
                result = venue.close(pos, reason="flatten all")
                print("closed", pos.symbol, pos.side, result.venue_order_id, result.message)
            except Exception as exc:
                print("close failed", pos.symbol, str(exc)[:250])
        left = venue.positions()
        print("slot", slot, "remaining", [(p.symbol, p.side, p.qty) for p in left])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

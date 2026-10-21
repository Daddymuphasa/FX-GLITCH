"""Live read of every Bitunix slot: balance, positions, SL/TP. Run: .venv/bin/python box/allslots.py"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"\''))
from fxglitch.venues.bitunix import SLOTS, from_slot
NAMES = {1: "muphasa", 2: "merrijeh", 3: "ekeson", 4: "kazi", 5: "KarlaGod"}
tot = [0.0, 0.0, 0.0, 0]
print("slot name equity free openPnL trades")
for s in sorted(SLOTS):
    try:
        v = from_slot(s); b = v.balance(); pos = v.positions() or []
        eq = b.available + b.used + b.unrealised_pnl
        tot[0] += eq; tot[1] += b.available; tot[2] += b.unrealised_pnl; tot[3] += len(pos)
        print(s, NAMES.get(s), round(eq, 2), round(b.available, 2), round(b.unrealised_pnl, 2), len(pos))
        for p in pos:
            print("   ", p.symbol, "LONG" if p.direction > 0 else "SHORT", p.qty, "@", p.entry_price,
                  "lev", p.leverage, {k: getattr(p, k) for k in ("stop_loss", "take_profit", "unrealised_pnl") if hasattr(p, k)})
    except Exception as e:
        print(s, NAMES.get(s), "ERR", type(e).__name__, str(e)[:120])
print("TOTAL", round(tot[0], 2), round(tot[1], 2), round(tot[2], 2), tot[3])

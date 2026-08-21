"""Download the macro series that actually move crypto.

    python tools/fetch_macro.py --years 12          # everything
    python tools/fetch_macro.py --series TYX,DXY    # just these

No API key. Yahoo Finance serves all of these daily, back decades, and is
reachable on networks where exchange APIs are not.

WHY THESE SERIES
----------------
The 19 August 2026 rally was, per the reporting, triggered by the Treasury
doubling its longer-dated buybacks after the 30-year yield spiked to levels last
seen around 2007. That is a testable claim, and TYX is how you test it.

More generally, Bitcoin trades as a long-duration risk asset. It is not the
digital gold of the marketing copy - it behaves like the most speculative thing
in a portfolio, which means it responds to the price of money:

    TYX/TNX   long-term yields. Rising yields = tighter conditions = risk-off.
              Falling yields = the discount rate on future cash flows drops,
              and the most speculative assets benefit most.
    DXY       dollar strength. A stronger dollar is a headwind for everything
              priced in dollars, crypto included.
    VIX       equity fear. Spikes are risk-off; crypto rarely escapes them.
    GSPC      the risk-on benchmark. Correlation is regime-dependent, which is
              itself worth measuring.
    GOLD      the actual store-of-value comparison, and your other market.

Whether any of this PREDICTS anything is exactly what tools/event_study.py and
the macro backtests are for. Do not assume it because the story is tidy.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

socket.setdefaulttimeout(25)
UA = {"User-Agent": "Mozilla/5.0 (FX-GLITCH research)"}

# our name -> (yahoo symbol, description, is_yield)
SERIES: dict[str, tuple[str, str, bool]] = {
    "TYX":  ("^TYX", "30-year Treasury yield", True),
    "TNX":  ("^TNX", "10-year Treasury yield", True),
    "IRX":  ("^IRX", "13-week T-bill yield", True),
    "DXY":  ("DX-Y.NYB", "US dollar index", False),
    "VIX":  ("^VIX", "Volatility index", False),
    "GSPC": ("^GSPC", "S&P 500", False),
    "GOLD": ("GC=F", "Gold futures", False),
    "NDX":  ("^NDX", "Nasdaq 100", False),
}


def fetch(symbol: str, start: datetime, end: datetime) -> list[dict]:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?interval=1d"
           f"&period1={int(start.timestamp())}&period2={int(end.timestamp())}")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
        payload = json.load(r)

    result = payload["chart"]["result"]
    if not result:
        raise ValueError(f"no data for {symbol}")
    res = result[0]
    stamps = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]

    rows = []
    for i, ts in enumerate(stamps):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if c is None:
            continue
        rows.append({
            "time": datetime.fromtimestamp(ts, tz=timezone.utc),
            # Yields have no meaningful OHLC volume; fall back to close.
            "open": float(o if o is not None else c),
            "high": float(h if h is not None else c),
            "low": float(l if l is not None else c),
            "close": float(c),
            "volume": float(q["volume"][i] or 0) if q.get("volume") else 0.0,
        })
    return rows


def write_csv(rows: list[dict], path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for r in rows:
            w.writerow([r["time"].strftime("%Y-%m-%d %H:%M:%S"), r["open"],
                        r["high"], r["low"], r["close"], r["volume"]])
    return path


def main() -> None:
    p = argparse.ArgumentParser(description="Download macro series to data/raw/macro/")
    p.add_argument("--years", type=float, default=12.0)
    p.add_argument("--series", help=f"comma list; default all of: {','.join(SERIES)}")
    p.add_argument("--outdir", default="data/raw/macro")
    args = p.parse_args()

    wanted = ([s.strip().upper() for s in args.series.split(",")]
              if args.series else list(SERIES))
    unknown = [s for s in wanted if s not in SERIES]
    if unknown:
        sys.exit(f"Unknown series {unknown}. Available: {', '.join(SERIES)}")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(args.years * 365))

    print(f"\nDownloading {len(wanted)} series, {args.years:g} years\n")
    print(f" {'series':<7} {'bars':>7}  {'period':<25} {'last':>12}  description")
    print(" " + "-" * 76)

    ok = 0
    for name in wanted:
        sym, desc, _ = SERIES[name]
        try:
            rows = fetch(sym, start, end)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            print(f" {name:<7} {'FAIL':>7}  {type(e).__name__}: {str(e)[:40]}")
            continue
        if not rows:
            print(f" {name:<7} {'EMPTY':>7}")
            continue
        write_csv(rows, os.path.join(args.outdir, f"{name.lower()}.csv"))
        period = f"{rows[0]['time']:%Y-%m-%d} to {rows[-1]['time']:%Y-%m-%d}"
        print(f" {name:<7} {len(rows):>7,}  {period:<25} {rows[-1]['close']:>12,.2f}  {desc}")
        ok += 1

    print(f"\n {ok}/{len(wanted)} saved to {args.outdir}/")
    print("\nNext:\n  python tools/macro_check.py --csv data/raw/btcusdt_1d.csv")


if __name__ == "__main__":
    main()

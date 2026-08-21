"""Download perpetual funding rates and open interest.

    python tools/fetch_derivs.py BTCUSDT --years 3          # funding history
    python tools/fetch_derivs.py BTCUSDT --oi               # open interest too
    python tools/fetch_derivs.py BTCUSDT --snapshot         # live, all exchanges

WHAT YOU CAN AND CANNOT GET FOR FREE
------------------------------------
    funding history   Binance back to 2019, Bybit/OKX similar. Full and free.
    open interest     Binance free endpoint returns THE LAST 30 DAYS ONLY.

That OI limit is a hard constraint, not something a better fetcher fixes. You
cannot backtest an OI factor over years on free data. Your options are to start
logging daily from today (run this on a schedule and it appends), or to pay a
vendor like Coinglass or Glassnode.

The honest consequence: funding-based factors can be backtested properly today.
OI-based factors cannot, and any result you get from 30 days of OI is an
anecdote. The tool marks this in its output rather than letting you forget.

`--snapshot` uses CoinGecko, which needs no key and is reachable almost
everywhere. It gives live funding and OI across 150+ BTC perp markets - useful
for a decision right now, useless for a backtest.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

socket.setdefaulttimeout(20)
UA = {"User-Agent": "Mozilla/5.0 (FX-GLITCH research)"}


def _get(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
        return json.loads(r.read().decode())


# --------------------------------------------------------------------------
# Funding history
# --------------------------------------------------------------------------

def funding_binance(symbol: str, start: datetime, end: datetime) -> list[dict]:
    rows: list[dict] = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    while cursor < end_ms:
        url = (f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        batch = _get(url)
        if not batch:
            break
        for k in batch:
            rows.append({
                "time": datetime.fromtimestamp(k["fundingTime"] / 1000, tz=timezone.utc),
                "funding_rate": float(k["fundingRate"]),
            })
        last = batch[-1]["fundingTime"]
        if last <= cursor:
            break
        cursor = last + 1
        print(f"    {len(rows):>6,} points ... {rows[-1]['time']:%Y-%m-%d}", end="\r")
        time.sleep(0.12)
    print()
    return rows


def funding_bybit(symbol: str, start: datetime, end: datetime) -> list[dict]:
    rows: list[dict] = []
    cursor = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)
    while cursor > start_ms:
        url = (f"https://api.bybit.com/v5/market/funding/history?category=linear"
               f"&symbol={symbol}&endTime={cursor}&limit=200")
        batch = _get(url).get("result", {}).get("list", [])
        if not batch:
            break
        for k in batch:
            ts = int(k["fundingRateTimestamp"])
            if ts >= start_ms:
                rows.append({
                    "time": datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    "funding_rate": float(k["fundingRate"]),
                })
        oldest = int(batch[-1]["fundingRateTimestamp"])
        if oldest >= cursor:
            break
        cursor = oldest - 1
        print(f"    {len(rows):>6,} points ... ", end="\r")
        time.sleep(0.12)
    print()
    rows.sort(key=lambda r: r["time"])
    return rows


def funding_okx(symbol: str, start: datetime, end: datetime) -> list[dict]:
    inst = symbol.replace("USDT", "-USDT-SWAP")
    rows: list[dict] = []
    cursor = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)
    while cursor > start_ms:
        url = (f"https://www.okx.com/api/v5/public/funding-rate-history?"
               f"instId={inst}&after={cursor}&limit=100")
        batch = _get(url).get("data", [])
        if not batch:
            break
        for k in batch:
            ts = int(k["fundingTime"])
            if ts >= start_ms:
                rows.append({
                    "time": datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    "funding_rate": float(k["fundingRate"]),
                })
        oldest = int(batch[-1]["fundingTime"])
        if oldest >= cursor:
            break
        cursor = oldest - 1
        time.sleep(0.15)
    rows.sort(key=lambda r: r["time"])
    return rows


# --------------------------------------------------------------------------
# Open interest - 30 days maximum on the free endpoint
# --------------------------------------------------------------------------

def oi_binance(symbol: str, period: str = "1d") -> list[dict]:
    url = (f"https://fapi.binance.com/futures/data/openInterestHist?"
           f"symbol={symbol}&period={period}&limit=500")
    return [{
        "time": datetime.fromtimestamp(k["timestamp"] / 1000, tz=timezone.utc),
        "open_interest": float(k["sumOpenInterest"]),
        "open_interest_value": float(k["sumOpenInterestValue"]),
    } for k in _get(url)]


def oi_bybit(symbol: str, period: str = "1d") -> list[dict]:
    interval = {"1d": "1d", "4h": "4h", "1h": "1h"}[period]
    url = (f"https://api.bybit.com/v5/market/open-interest?category=linear"
           f"&symbol={symbol}&intervalTime={interval}&limit=200")
    rows = _get(url).get("result", {}).get("list", [])
    out = [{
        "time": datetime.fromtimestamp(int(k["timestamp"]) / 1000, tz=timezone.utc),
        "open_interest": float(k["openInterest"]),
        "open_interest_value": 0.0,
    } for k in rows]
    out.sort(key=lambda r: r["time"])
    return out


# --------------------------------------------------------------------------
# Live snapshot - works without a key, reachable nearly everywhere
# --------------------------------------------------------------------------

def snapshot(index: str = "BTC", top: int = 12) -> list[dict]:
    data = _get("https://api.coingecko.com/api/v3/derivatives")
    perps = [m for m in data
             if m.get("index_id") == index and m.get("contract_type") == "perpetual"]
    perps.sort(key=lambda m: -(float(m.get("open_interest") or 0)))
    return perps[:top]


def print_snapshot(index: str, top: int) -> None:
    perps = snapshot(index, top)
    if not perps:
        sys.exit(f"No perpetual markets found for {index}")

    print(f"\n{'=' * 72}")
    print(f" {index} PERPETUALS - live positioning")
    print(f"{'=' * 72}")
    print(f" {'exchange':<24} {'price':>11} {'funding/8h':>12} {'open interest':>18}")
    print(" " + "-" * 70)

    fundings, total_oi = [], 0.0
    for m in perps:
        oi = float(m.get("open_interest") or 0)
        total_oi += oi
        fr = m.get("funding_rate")
        try:
            fr = float(fr)
            fundings.append(fr)
            fr_s = f"{fr:+.4f}%"
        except (TypeError, ValueError):
            fr_s = "n/a"
        print(f" {m['market'][:23]:<24} {float(m.get('price') or 0):>11,.0f} "
              f"{fr_s:>12} {oi:>18,.0f}")

    print(" " + "-" * 70)
    print(f" {'TOTAL':<24} {'':<11} {'':<12} {total_oi:>18,.0f}")

    if fundings:
        avg = sum(fundings) / len(fundings)
        print(f"\n Average funding: {avg:+.4f}% per 8h  "
              f"({avg * 3 * 365:+.1f}% annualised)")
        print()
        # CoinGecko reports funding already in percent.
        if avg > 0.05:
            print(" READ: crowd is aggressively LONG and paying for it.")
            print(" Long liquidations are the fuel. Downside moves can cascade.")
        elif avg < -0.02:
            print(" READ: crowd is SHORT and paying for it. This is squeeze fuel -")
            print(" if price rises anyway, forced buying amplifies it. The 19 Aug setup.")
        elif abs(avg) <= 0.015:
            print(" READ: funding is at baseline. Positioning is NOT crowded either")
            print(" way, so it carries no contrarian information right now.")
        else:
            print(" READ: mildly one-sided. Not extreme enough to act on alone.")
    print("=" * 72)


# --------------------------------------------------------------------------

def write_csv(rows: list[dict], path: str, columns: list[str]) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Append-friendly: if the file exists, merge and de-duplicate on time. This
    # is what makes daily OI logging viable despite the 30-day API window.
    existing: dict[datetime, dict] = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                try:
                    t = datetime.fromisoformat(r["time"])
                except ValueError:
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                existing[t] = {k: r.get(k, "") for k in columns}

    for r in rows:
        existing[r["time"]] = {c: r.get(c, "") for c in columns}

    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time"] + columns)
        for t in sorted(existing):
            w.writerow([t.strftime("%Y-%m-%d %H:%M:%S")]
                       + [existing[t].get(c, "") for c in columns])
    return path


FUNDING_SOURCES = {"binance": funding_binance, "bybit": funding_bybit, "okx": funding_okx}
OI_SOURCES = {"binance": oi_binance, "bybit": oi_bybit}


def main() -> None:
    p = argparse.ArgumentParser(description="Download perp funding and open interest")
    p.add_argument("symbol", nargs="?", default="BTCUSDT")
    p.add_argument("--years", type=float, default=3.0)
    p.add_argument("--oi", action="store_true", help="also fetch open interest")
    p.add_argument("--snapshot", action="store_true",
                   help="live positioning across exchanges, then exit")
    p.add_argument("--index", default="BTC", help="for --snapshot")
    p.add_argument("--top", type=int, default=12, help="for --snapshot")
    p.add_argument("--source", choices=sorted(FUNDING_SOURCES))
    args = p.parse_args()

    if args.snapshot:
        try:
            print_snapshot(args.index, args.top)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            sys.exit(f"CoinGecko unreachable: {e}")
        return

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(args.years * 365))
    order = [args.source] if args.source else ["binance", "bybit", "okx"]

    rows: list[dict] = []
    used = ""
    for name in order:
        print(f"[{name}] funding history for {args.symbol} ...")
        try:
            rows = FUNDING_SOURCES[name](args.symbol, start, end)
            if rows:
                used = name
                break
        except Exception as e:
            print(f"    failed ({type(e).__name__}: {str(e)[:60]}) - trying next")

    if not rows:
        sys.exit("\nEvery funding source failed. Behind a VPN or restricted network?\n"
                 "Try:  python tools/fetch_derivs.py --snapshot   (CoinGecko, "
                 "usually reachable)")

    path = write_csv(rows, f"data/raw/{args.symbol.lower()}_funding.csv",
                     ["funding_rate"])
    vals = [r["funding_rate"] for r in rows]
    print(f"\n  source     {used}")
    print(f"  points     {len(rows):,}")
    print(f"  period     {rows[0]['time']:%Y-%m-%d} -> {rows[-1]['time']:%Y-%m-%d}")
    print(f"  funding    mean {sum(vals)/len(vals)*100:+.4f}%/8h, "
          f"range {min(vals)*100:+.4f}% to {max(vals)*100:+.4f}%")
    print(f"  saved      {path}")

    if args.oi:
        oi_rows: list[dict] = []
        for name in ["binance", "bybit"]:
            print(f"\n[{name}] open interest for {args.symbol} ...")
            try:
                oi_rows = OI_SOURCES[name](args.symbol)
                if oi_rows:
                    break
            except Exception as e:
                print(f"    failed ({type(e).__name__}: {str(e)[:60]})")
        if oi_rows:
            oi_path = write_csv(oi_rows, f"data/raw/{args.symbol.lower()}_oi.csv",
                                ["open_interest", "open_interest_value"])
            span = (oi_rows[-1]["time"] - oi_rows[0]["time"]).days
            print(f"  points     {len(oi_rows):,} covering {span} days")
            print(f"  saved      {oi_path}")
            print("\n  NOTE: the free OI endpoint returns roughly the last 30 days.")
            print("  This file APPENDS on each run - schedule it daily and you will")
            print("  build real history from today forward. Until you have months of")
            print("  it, do not treat an OI backtest as validation.")

    print(f"\nNext:\n  python run.py confluence --csv data/raw/"
          f"{args.symbol.lower()}_1d.csv --market binance-spot \\\n"
          f"      --signals --funding data/raw/{args.symbol.lower()}_funding.csv")


if __name__ == "__main__":
    main()

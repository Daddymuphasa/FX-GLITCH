"""Download real crypto OHLCV history to data/raw/ as csv.

No API key, no account, no paid tier. Crypto is the one market where the full
tick-by-tick past is simply given away, which makes it the best place to learn
to test a strategy honestly.

    python tools/fetch_crypto.py BTCUSDT --interval 1h --years 3
    python tools/fetch_crypto.py ETHUSDT --interval 15m --days 180
    python tools/fetch_crypto.py BTC-USD --source yahoo --interval 1h --days 300

Sources, in the order the script tries them:

  binance  best by far. Full history to 2017, down to 1m bars, no key.
           Blocked in some countries and on some corporate networks.
  bybit    same data shape, different host. Good fallback if Binance is blocked.
  yahoo    always reachable, no key. Limited: 1m only covers 7 days,
           anything under 1h covers 60 days, 1h covers 730 days.
  coingecko daily bars only, but reaches back years and is never blocked.

If one is unreachable the script says so and moves to the next.
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

# Interval names normalised across sources: our name -> (binance, yahoo, minutes)
INTERVALS = {
    "1m":  ("1m",  "1m",  1),
    "5m":  ("5m",  "5m",  5),
    "15m": ("15m", "15m", 15),
    "30m": ("30m", "30m", 30),
    "1h":  ("1h",  "1h",  60),
    "4h":  ("4h",  None,  240),
    "1d":  ("1d",  "1d",  1440),
}


def _get(url: str) -> dict | list:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

def from_binance(symbol: str, interval: str, start: datetime, end: datetime,
                 host: str = "api.binance.com") -> list[dict]:
    """Paginate Binance klines. 1000 bars per request, oldest first."""
    b_interval = INTERVALS[interval][0]
    rows: list[dict] = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    while cursor < end_ms:
        url = (f"https://{host}/api/v3/klines?symbol={symbol}&interval={b_interval}"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        batch = _get(url)
        if not batch:
            break
        for k in batch:
            rows.append({
                "time": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
            })
        last = batch[-1][0]
        if last <= cursor:
            break
        cursor = last + 1
        print(f"    {len(rows):>7,} bars ... {rows[-1]['time']:%Y-%m-%d}", end="\r")
        time.sleep(0.12)  # stay well under the rate limit
    print()
    return rows


def from_bybit(symbol: str, interval: str, start: datetime, end: datetime) -> list[dict]:
    """Bybit v5 klines. Returns NEWEST first, so we walk backwards."""
    minutes = INTERVALS[interval][2]
    b_int = "D" if minutes == 1440 else str(minutes)
    rows: list[dict] = []
    cursor = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)

    while cursor > start_ms:
        url = (f"https://api.bybit.com/v5/market/kline?category=spot&symbol={symbol}"
               f"&interval={b_int}&end={cursor}&limit=1000")
        payload = _get(url)
        batch = payload.get("result", {}).get("list", [])
        if not batch:
            break
        for k in batch:
            ts = int(k[0])
            if ts < start_ms:
                continue
            rows.append({
                "time": datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
            })
        oldest = int(batch[-1][0])
        if oldest >= cursor:
            break
        cursor = oldest - 1
        print(f"    {len(rows):>7,} bars ... {rows[-1]['time']:%Y-%m-%d}", end="\r")
        time.sleep(0.12)
    print()
    rows.sort(key=lambda r: r["time"])
    return rows


def from_yahoo(symbol: str, interval: str, start: datetime, end: datetime) -> list[dict]:
    """Yahoo Finance chart endpoint. Use dashed symbols: BTC-USD, ETH-USD."""
    y_interval = INTERVALS[interval][1]
    if y_interval is None:
        raise ValueError(f"Yahoo does not serve {interval} bars. Use 1h or 1d.")

    # Yahoo caps intraday history hard; ask for no more than it will give.
    caps = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "1h": 730, "1d": 20000}
    max_days = caps[interval]
    if (end - start).days > max_days:
        start = end - timedelta(days=max_days)
        print(f"    yahoo caps {interval} history at {max_days} days;"
              f" starting {start:%Y-%m-%d}")

    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval={y_interval}&period1={int(start.timestamp())}"
           f"&period2={int(end.timestamp())}")
    payload = _get(url)
    result = payload["chart"]["result"]
    if not result:
        raise ValueError(f"Yahoo returned nothing for {symbol}")
    res = result[0]
    stamps = res["timestamp"]
    q = res["indicators"]["quote"][0]

    rows = []
    for i, ts in enumerate(stamps):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue  # Yahoo leaves gaps; skip rather than interpolate
        rows.append({
            "time": datetime.fromtimestamp(ts, tz=timezone.utc),
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": float(q["volume"][i] or 0),
        })
    return rows


def from_coingecko(symbol: str, interval: str, start: datetime, end: datetime) -> list[dict]:
    """Daily OHLC only. `symbol` here is a coingecko id like 'bitcoin'."""
    if interval != "1d":
        raise ValueError("CoinGecko here only serves 1d bars.")
    days = max(1, (end - start).days)
    url = (f"https://api.coingecko.com/api/v3/coins/{symbol}/ohlc"
           f"?vs_currency=usd&days={min(days, 365)}")
    data = _get(url)
    return [{
        "time": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
        "open": float(k[1]), "high": float(k[2]),
        "low": float(k[3]), "close": float(k[4]), "volume": 0.0,
    } for k in data]


SOURCES = {
    "binance":   lambda s, i, a, b: from_binance(s, i, a, b),
    "binanceus": lambda s, i, a, b: from_binance(s, i, a, b, host="api.binance.us"),
    "bybit":     from_bybit,
    "yahoo":     from_yahoo,
    "coingecko": from_coingecko,
}
FALLBACK_ORDER = ["binance", "bybit", "binanceus", "yahoo"]


# --------------------------------------------------------------------------

def validate(rows: list[dict]) -> list[str]:
    """Catch bad data before it silently ruins a backtest."""
    problems = []
    if not rows:
        return ["no rows returned"]

    bad_ohlc = sum(1 for r in rows
                   if r["high"] < max(r["open"], r["close"])
                   or r["low"] > min(r["open"], r["close"]))
    if bad_ohlc:
        problems.append(f"{bad_ohlc} bars where high/low do not contain open/close")

    dupes = len(rows) - len({r["time"] for r in rows})
    if dupes:
        problems.append(f"{dupes} duplicate timestamps")

    if len(rows) > 2:
        gaps = {}
        for a, b in zip(rows, rows[1:]):
            gaps[(b["time"] - a["time"]).total_seconds()] = \
                gaps.get((b["time"] - a["time"]).total_seconds(), 0) + 1
        expected = max(gaps, key=gaps.get)
        missing = sum(n for g, n in gaps.items() if g > expected)
        if missing:
            problems.append(f"{missing} gaps in the series (exchange downtime or missing data)")

    zeros = sum(1 for r in rows if r["close"] <= 0)
    if zeros:
        problems.append(f"{zeros} bars with a non-positive close")
    return problems


def write_csv(rows: list[dict], path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for r in rows:
            w.writerow([r["time"].strftime("%Y-%m-%d %H:%M:%S"),
                        r["open"], r["high"], r["low"], r["close"], r["volume"]])
    return path


def main() -> None:
    p = argparse.ArgumentParser(description="Download crypto OHLCV to data/raw/")
    p.add_argument("symbol", help="BTCUSDT for binance/bybit, BTC-USD for yahoo")
    p.add_argument("--interval", default="1h", choices=sorted(INTERVALS))
    p.add_argument("--days", type=int, help="how far back to go")
    p.add_argument("--years", type=float, help="how far back to go, in years")
    p.add_argument("--source", choices=sorted(SOURCES),
                   help="force one source instead of trying them in order")
    p.add_argument("--out", help="output path (default data/raw/<symbol>_<interval>.csv)")
    args = p.parse_args()

    days = args.days or (int(args.years * 365) if args.years else 365)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    order = [args.source] if args.source else FALLBACK_ORDER
    symbol = args.symbol

    rows: list[dict] = []
    used = ""
    for name in order:
        # Yahoo wants BTC-USD, the exchanges want BTCUSDT. Translate rather than
        # making the user remember which is which.
        sym = symbol
        if name == "yahoo" and "-" not in sym:
            sym = sym.replace("USDT", "-USD").replace("USDC", "-USD")
            if "-" not in sym:
                sym = f"{sym}-USD"
        elif name != "yahoo" and "-" in sym:
            sym = sym.replace("-USD", "USDT")

        print(f"[{name}] requesting {sym} {args.interval}, {days} days ...")
        try:
            rows = SOURCES[name](sym, args.interval, start, end)
            if rows:
                used = name
                break
            print(f"    {name} returned no data")
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout) as e:
            print(f"    unreachable ({type(e).__name__}: {str(e)[:60]}) - trying next")
        except Exception as e:
            print(f"    failed ({type(e).__name__}: {str(e)[:80]}) - trying next")

    if not rows:
        sys.exit("\nEvery source failed. If you are behind a VPN or corporate network, "
                 "try --source yahoo, or export a csv from your broker into data/raw/.")

    rows.sort(key=lambda r: r["time"])
    # De-duplicate on timestamp, keeping the last seen.
    seen: dict = {}
    for r in rows:
        seen[r["time"]] = r
    rows = [seen[k] for k in sorted(seen)]

    out = args.out or f"data/raw/{symbol.replace('-', '').lower()}_{args.interval}.csv"
    write_csv(rows, out)

    print(f"\n  source     {used}")
    print(f"  bars       {len(rows):,}")
    print(f"  period     {rows[0]['time']:%Y-%m-%d %H:%M} -> {rows[-1]['time']:%Y-%m-%d %H:%M}")
    print(f"  price      {min(r['low'] for r in rows):,.2f} - {max(r['high'] for r in rows):,.2f}")
    print(f"  saved      {out}")

    problems = validate(rows)
    if problems:
        print("\n  DATA WARNINGS - read these before trusting a backtest on it:")
        for w in problems:
            print(f"    - {w}")
    else:
        print("  quality    no problems found")

    print(f"\nNext:\n  python run.py ema_cross --csv {out} --fee 0.1 --noise-test")


if __name__ == "__main__":
    main()

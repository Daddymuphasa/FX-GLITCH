"""Build local candle history, one run at a time.

    python tools/sync_candles.py --top 40                 # top 40 by turnover
    python tools/sync_candles.py --symbols BTCUSDT,ETHUSDT
    python tools/sync_candles.py --top 40 --interval 4h
    python tools/sync_candles.py --coverage               # what do we have?

WHY YOU WANT THIS RUNNING ON A SCHEDULE
---------------------------------------
Free endpoints hand back a few hundred bars. That is enough to trade on and
nowhere near enough to measure on, and no amount of code changes it - the data
simply is not offered. The only way to hold five years of alt history is to
have been collecting for five years, which means the useful version of this
tool is the one that ran yesterday.

Two things in the repo are blocked on exactly this, and both unblock quietly as
the store fills:

  - The exposure layer treats every alt as beta 1.0 to BTC, a deliberately
    crude stand-in, because measuring a real beta needs a shared history of
    alt and BTC returns.
  - The BTC gate is an unmeasured prior. Testing whether gating alt entries on
    BTC's trend helps or hurts needs alt price history to test it against.

Same constraint as open interest in derivs.py: history accrues from the day you
start, and never from any day you did not.

WHAT IT WILL NOT DO
-------------------
Invent history. A symbol listed last month has a month of bars, and running
this a hundred times will not produce a hundred months. The coverage report
exists so you can see what is actually there before trusting a measurement
taken from it.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.live.screener import ScreenRules, screen  # noqa: E402
from fxglitch.store import DEFAULT_ROOT, CandleStore, coverage  # noqa: E402
from fxglitch.venues.base import VenueError  # noqa: E402
from fxglitch.venues.bitunix import Bitunix  # noqa: E402


def show_coverage(store: CandleStore, interval: str) -> None:
    rows = coverage(store, interval)
    if not rows:
        print(f"\nNothing stored for {interval} yet. Run without --coverage first.")
        return
    print(f"\n{len(rows)} symbols at {interval}\n")
    print(f" {'symbol':<16} {'bars':>6}  {'from':<12} {'to':<12}")
    print(" " + "-" * 50)
    for symbol, count, first, last in sorted(rows, key=lambda r: -r[1]):
        print(f" {symbol:<16} {count:>6}  {first:%Y-%m-%d}   {last:%Y-%m-%d}")

    shortest = min(r[1] for r in rows)
    print(f"\n The shortest history here is {shortest} bars.")
    if shortest < 200:
        print(" Too short to measure anything with. Keep the syncer running;")
        print(" this number is the one that decides when betas become real.")


def main() -> None:
    p = argparse.ArgumentParser(description="Download and store candle history")
    p.add_argument("--symbols", help="comma separated; overrides --top")
    p.add_argument("--top", type=int, default=20,
                   help="how many symbols to keep, by turnover (default 20)")
    p.add_argument("--interval", default="1d")
    p.add_argument("--bars", type=int, default=400,
                   help="target history depth per symbol")
    p.add_argument("--min-turnover", type=float, default=10_000_000.0)
    p.add_argument("--root", default=DEFAULT_ROOT)
    p.add_argument("--coverage", action="store_true",
                   help="report what is stored and exit")
    args = p.parse_args()

    store = CandleStore(args.root)
    if args.coverage:
        show_coverage(store, args.interval)
        return

    venue = Bitunix()
    try:
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        else:
            rules = ScreenRules(min_turnover=args.min_turnover, top=args.top)
            symbols = [t.symbol for t in
                       screen(venue.tickers(), venue.instruments(), rules)]
    except VenueError as exc:
        sys.exit(f"\nCould not reach Bitunix: {exc}")

    print(f"\nSyncing {len(symbols)} symbols at {args.interval} "
          f"into {args.root}\n")

    added = failed = mismatched = 0
    for symbol in symbols:
        try:
            report = store.sync_report(venue, symbol, args.interval, args.bars)
        except VenueError as exc:
            # One symbol failing must not abandon the rest - the point of the
            # tool is the history that accumulates, and a partial run still
            # adds to it.
            print(f" {symbol:<14} FAILED: {exc}")
            failed += 1
            if exc.code == "cloudflare":
                print("\n Stopping: further requests will extend the block.")
                print(" Everything synced so far is saved. Rerun later.")
                break
            continue
        print(f" {report}")
        added += report.added
        mismatched += report.mismatches

    print(f"\n{added:,} new bars stored"
          + (f", {failed} symbol(s) failed" if failed else ""))
    if mismatched:
        print(f"{mismatched} bars disagreed with stored history and were NOT")
        print("overwritten. Check you are not mixing intervals or price types.")
    show_coverage(store, args.interval)


if __name__ == "__main__":
    main()

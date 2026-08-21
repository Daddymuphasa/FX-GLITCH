"""Event study: does this catalyst actually move price, or does it just get
written about after price moves?

This is the tool that turns "Treasury buybacks pumped Bitcoin" from a belief
into a measurement. For every logged event of a category it measures the
forward return over several horizons, and - the part that matters - compares
it against the return of a RANDOM day in the same period.

Because here is the trap. Bitcoin's average 3-day return over 2015-2026 is
positive and large. If you find that your catalyst is followed by +2% over
three days, and a random day is followed by +2.1%, your catalyst has NEGATIVE
information value. It felt predictive only because the asset went up.

    python tools/event_study.py --events data/events/btc.csv \
        --csv data/raw/btcusdt_1d.csv --category treasury_buyback

    python tools/event_study.py --events data/events/btc.csv \
        --csv data/raw/btcusdt_1d.csv           # all categories

Under about 30 events of a category, this tool will tell you so and you should
believe it rather than the number next to it.
"""

from __future__ import annotations

import argparse
import bisect
import os
import statistics
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.data import Series, load_csv  # noqa: E402
from fxglitch.news import load_events  # noqa: E402


def forward_return(candles: Series, times: list[datetime], t: datetime,
                   bars: int) -> float | None:
    """Return from the close after `t` to `bars` bars later.

    Entry is the first bar at or after t, exited `bars` later. Using the close
    of the bar containing t would be lookahead: at time t that bar has not
    finished.
    """
    i = bisect.bisect_left(times, t)
    if i >= len(candles) or i + bars >= len(candles):
        return None
    entry = candles[i].close
    exit_ = candles[i + bars].close
    return (exit_ - entry) / entry * 100.0 if entry else None


def baseline(candles: Series, bars: int) -> list[float]:
    """Forward return of every bar - what a random day looks like."""
    out = []
    for i in range(len(candles) - bars):
        e, x = candles[i].close, candles[i + bars].close
        if e:
            out.append((x - e) / e * 100.0)
    return out


def study(candles: Series, events: list, category: str, horizons: list[int]) -> None:
    times = [c.time for c in candles]
    subset = [e for e in events if e.name == category]
    if not subset:
        return

    print(f"\n{'=' * 66}")
    print(f" {category}   ({len(subset)} events)")
    print(f"{'=' * 66}")

    if len(subset) < 30:
        print(f" WARNING: {len(subset)} events is not enough to conclude anything.")
        print(" Treat everything below as a placeholder until you have 30+.")
        print()

    print(f" {'horizon':>9} {'events':>7} {'mean':>9} {'median':>9} {'win%':>7} "
          f"{'baseline':>10} {'EDGE':>9}")
    print(" " + "-" * 64)

    for bars in horizons:
        rets = [r for e in subset
                if (r := forward_return(candles, times, e.available_at, bars)) is not None]
        if not rets:
            continue
        base = baseline(candles, bars)
        base_mean = statistics.mean(base) if base else 0.0
        mean = statistics.mean(rets)
        med = statistics.median(rets)
        win = sum(1 for r in rets if r > 0) / len(rets) * 100
        edge = mean - base_mean
        flag = "" if len(rets) >= 30 else "  (n<30)"
        print(f" {bars:>7}b {len(rets):>7} {mean:>+8.2f}% {med:>+8.2f}% {win:>6.0f}% "
              f"{base_mean:>+9.2f}% {edge:>+8.2f}%{flag}")

    # The honest read on the longest horizon.
    bars = horizons[-1]
    rets = [r for e in subset
            if (r := forward_return(candles, times, e.available_at, bars)) is not None]
    if len(rets) >= 2:
        base = baseline(candles, bars)
        edge = statistics.mean(rets) - (statistics.mean(base) if base else 0)
        sd = statistics.stdev(rets)
        # Standard error of the mean, so we can say whether the edge is even
        # distinguishable from zero.
        se = sd / (len(rets) ** 0.5)
        print()
        if len(rets) < 30:
            print(f" READ: {len(rets)} events. No conclusion is available. Keep logging.")
        elif abs(edge) < se:
            print(f" READ: edge {edge:+.2f}% is smaller than its own error bar "
                  f"(+/-{se:.2f}%).")
            print(" Indistinguishable from noise. Do not weight this catalyst.")
        elif edge > 0:
            print(f" READ: {edge:+.2f}% above baseline, error bar +/-{se:.2f}%. "
                  f"Worth weighting.")
        else:
            print(f" READ: {edge:+.2f}% BELOW baseline. This catalyst is a fade, "
                  f"or noise.")


def main() -> None:
    p = argparse.ArgumentParser(description="Measure whether catalysts have real edge")
    p.add_argument("--events", required=True, help="csv or json catalyst log")
    p.add_argument("--csv", required=True, help="price data")
    p.add_argument("--category", help="just this one; default is all")
    p.add_argument("--horizons", default="1,3,5,10",
                   help="forward windows in bars (default 1,3,5,10)")
    args = p.parse_args()

    candles = load_csv(args.csv)
    events = load_events(args.events)
    horizons = [int(x) for x in args.horizons.split(",")]

    print(f"\nPrice:  {os.path.basename(args.csv)}  ({len(candles):,} bars, "
          f"{candles[0].time:%Y-%m-%d} to {candles[-1].time:%Y-%m-%d})")
    print(f"Events: {os.path.basename(args.events)}  ({len(events)} logged)")

    cats = [args.category] if args.category else sorted({e.name for e in events})
    for c in cats:
        study(candles, events, c, horizons)

    print(f"\n{'=' * 66}")
    print(" Remember what this can and cannot tell you. It measures whether")
    print(" price tended to rise after events you LOGGED. If you logged them")
    print(" from memory, you logged the memorable ones, and the memorable ones")
    print(" are the ones that worked. Log prospectively or not at all.")
    print("=" * 66)


if __name__ == "__main__":
    main()

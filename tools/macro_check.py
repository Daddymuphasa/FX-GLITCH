"""Does any macro move actually predict crypto returns?

    python tools/macro_check.py --csv data/raw/btcusdt_1d.csv
    python tools/macro_check.py --csv data/raw/ethusdt_1d.csv --horizon 3

For each macro series this takes the most extreme daily moves (top and bottom
5% by default), measures what the target asset did over the following days, and
compares that against the asset's OWN average move over the same window.

That last comparison is the entire point, and it is what almost nobody does.
BTC's average 5-day return over 2015-2026 is about +1.0%. If a macro trigger is
followed by +0.7%, the trigger has NEGATIVE information value - it felt
predictive only because the asset went up in general.

WHAT THIS FOUND, and why the repo's weights changed because of it
-----------------------------------------------------------------
Run on BTC daily over 12 years, twelve conditions across six macro series:

    - Ten of twelve were statistically indistinguishable from noise.
    - Not one predicted BTC OUTPERFORMANCE. Every measured edge was <= 0.
    - The two real effects were both negative: after a big S&P fall or a big
      dollar fall, BTC underperformed its own baseline by more than one
      standard error.

Including the specific claim that prompted this tool. The 30-year Treasury
yield fell -0.09 on 19 August 2026 - a genuine 3rd-percentile move, and the
narrative tying it to that day's +7% BTC candle is coherent and well sourced.
But across 129 comparable yield drops in twelve years, BTC's forward return was
slightly BELOW baseline at every horizon of 1, 3, 5 and 10 days.

The story about that one day may well be true. It is simply not a rule.

This is the survivorship trap made concrete: you only ever read about the
Treasury announcement that preceded a rally. The 128 that did not were never
written up, so the base rate is invisible until you compute it.

Read the negative result correctly, though. It does NOT prove macro is
irrelevant to crypto - it proves these SIMPLE formulations carry no edge:
single-day thresholds, fixed horizons, no regime conditioning. A more careful
construction might. The burden is on the construction to show it here first.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.data import load_csv  # noqa: E402

SERIES = [("tyx", "30y yield"), ("tnx", "10y yield"), ("irx", "13w bill"),
          ("dxy", "dollar"), ("vix", "VIX"), ("gspc", "S&P 500"),
          ("gold", "gold"), ("ndx", "Nasdaq")]


def forward_returns(candles, n):
    return [(candles[i + n].close - candles[i].close) / candles[i].close * 100
            for i in range(len(candles) - n) if candles[i].close]


def main() -> None:
    p = argparse.ArgumentParser(description="Test macro triggers against an asset")
    p.add_argument("--csv", required=True, help="the asset, e.g. data/raw/btcusdt_1d.csv")
    p.add_argument("--macro-dir", default="data/raw/macro")
    p.add_argument("--horizon", type=int, default=5, help="forward days (default 5)")
    p.add_argument("--percentile", type=float, default=0.05,
                   help="how extreme a move must be (default 0.05 = top/bottom 5%%)")
    p.add_argument("--min-events", type=int, default=30)
    args = p.parse_args()

    target = load_csv(args.csv)
    index = {c.time.date(): i for i, c in enumerate(target)}
    n = args.horizon

    base_all = forward_returns(target, n)
    if not base_all:
        sys.exit("not enough target data")
    baseline = statistics.mean(base_all)

    print(f"\nTarget:   {os.path.basename(args.csv)}  ({len(target):,} bars, "
          f"{target[0].time:%Y-%m-%d} to {target[-1].time:%Y-%m-%d})")
    print(f"Horizon:  {n} days")
    print(f"Baseline: {baseline:+.2f}%  <- what a RANDOM {n}-day window returned")
    print(f"\n{'condition':<36} {'n':>4} {'asset':>8} {'EDGE':>8} {'err':>7}  verdict")
    print("-" * 78)

    found = []
    for key, label in SERIES:
        path = os.path.join(args.macro_dir, f"{key}.csv")
        if not os.path.exists(path):
            continue
        s = load_csv(path)
        changes = [(s[i].time, (s[i].close - s[i - 1].close) / s[i - 1].close * 100)
                   for i in range(1, len(s)) if s[i - 1].close]
        if len(changes) < 100:
            continue
        ordered = sorted(c for _, c in changes)

        for direction, tag in ((-1, "big fall"), (1, "big rise")):
            if direction < 0:
                cut = ordered[int(len(ordered) * args.percentile)]
                events = [t for t, c in changes if c <= cut]
            else:
                cut = ordered[int(len(ordered) * (1 - args.percentile))]
                events = [t for t, c in changes if c >= cut]

            rets = []
            for t in events:
                i = index.get(t.date())
                if i is None or i + n >= len(target) or not target[i].close:
                    continue
                rets.append((target[i + n].close - target[i].close)
                            / target[i].close * 100)

            if len(rets) < args.min_events:
                continue
            mean = statistics.mean(rets)
            se = statistics.stdev(rets) / len(rets) ** 0.5
            edge = mean - baseline
            real = abs(edge) > se
            verdict = ("EDGE+" if edge > 0 else "EDGE-") if real else "noise"
            found.append((label, tag, edge, real))
            print(f"{label + ' ' + tag:<36} {len(rets):>4} {mean:>+7.2f}% "
                  f"{edge:>+7.2f}% {se:>6.2f}%  {verdict}")

    print("-" * 78)
    positives = [f for f in found if f[3] and f[2] > 0]
    negatives = [f for f in found if f[3] and f[2] < 0]
    print(f"\n {len(found)} conditions tested, {len(positives)} with positive edge, "
          f"{len(negatives)} with negative edge, "
          f"{len(found) - len(positives) - len(negatives)} noise.")
    print()
    if not positives:
        print(" READ: no macro condition predicted outperformance. On this evidence,")
        print(" macro belongs in the report as CONTEXT, not in the model as a")
        print(" weighted input. A tidy story is not an edge.")
    else:
        print(" READ: some conditions show positive edge. Before trusting one, check")
        print(" it holds on another asset and in a different date range - with this")
        print(" many comparisons, one or two will look good by chance alone.")
    print("\n Note: these are crude single-day thresholds at a fixed horizon. A null")
    print(" result rules out THIS formulation, not the whole idea of macro.")


if __name__ == "__main__":
    main()

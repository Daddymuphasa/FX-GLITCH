"""Parameter sweep - the curve-fitting detector.

A real edge is a broad, flat plateau: the strategy works at 40, 50, 55, 60 and
70, roughly as well at each. A curve-fitted illusion is a lonely spike: it makes
+0.9R at exactly 55 and loses money at 50 and 60. That spike is not a setting
you discovered, it is the one arrangement of numbers that happened to line up
with the noise in this particular slice of history. It will not repeat.

    python tools/sweep.py donchian_breakout --csv data/raw/btcusdt_1d.csv \
        --market binance-spot --vary entry=20,35,55,80,120

    # Two parameters at once gives you a heat grid
    python tools/sweep.py donchian_breakout --csv data/raw/btcusdt_1d.csv \
        --market binance-spot --vary entry=20,55,90 --vary atr_mult=1.5,2.5,3.5

Read the OUTPUT SHAPE, not the best cell. The best cell is always good; that is
what "best" means. The question is what its neighbours look like.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch import data, markets, simulate  # noqa: E402
from fxglitch.engine import Backtest  # noqa: E402
from fxglitch.metrics import analyse  # noqa: E402
from run import coerce, load_strategy  # noqa: E402


def run_one(StrategyClass, kwargs, candles, args):
    r = Backtest(candles, StrategyClass(**kwargs), symbol="sweep",
                 starting_equity=args.equity, risk_pct=args.risk,
                 spread=args.spread, fee_pct=args.fee,
                 slippage_pct=args.slippage).run()
    return analyse(r)


def main() -> None:
    p = argparse.ArgumentParser(description="Parameter sweep / curve-fit detector")
    p.add_argument("strategy")
    p.add_argument("--csv")
    p.add_argument("--sim")
    p.add_argument("--bars", type=int, default=10000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--vary", action="append", required=True, metavar="KEY=V1,V2,V3",
                   help="parameter and values to test; use twice for a 2-D grid")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="hold this parameter fixed")
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--spread", type=float, default=0.0)
    p.add_argument("--fee", type=float, default=0.0)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--market")
    p.add_argument("--metric", default="expectancy_r",
                   choices=["expectancy_r", "profit_factor", "return_pct",
                            "win_rate", "max_drawdown_pct", "trades"])
    args = p.parse_args()

    if args.market:
        m = markets.get(args.market)
        args.fee, args.slippage, args.spread = m.fee_pct, m.slippage_pct, m.spread

    if args.csv:
        candles = data.load_csv(args.csv)
        label = os.path.basename(args.csv)
    elif args.sim:
        candles = simulate.preset(args.sim, bars=args.bars, seed=args.seed)
        label = f"{args.sim} (simulated)"
    else:
        p.error("choose --csv or --sim")

    StrategyClass = load_strategy(args.strategy)
    fixed = {k: coerce(v) for k, v in (kv.split("=", 1) for kv in args.set)}

    axes = []
    for spec in args.vary:
        key, raw = spec.split("=", 1)
        axes.append((key, [coerce(v) for v in raw.split(",")]))

    print(f"\nSweeping {args.strategy} on {label} ({len(candles):,} bars)")
    print(f"metric: {args.metric}\n")

    if len(axes) == 1:
        key, values = axes[0]
        print(f" {key:>10} {'trades':>8} {'expect':>9} {'PF':>7} {'return':>9} {'maxDD':>8}")
        print(" " + "-" * 54)
        scores = []
        for v in values:
            s = run_one(StrategyClass, {**fixed, key: v}, candles, args)
            scores.append(getattr(s, args.metric))
            pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
            print(f" {str(v):>10} {s.trades:>8} {s.expectancy_r:>+9.3f} {pf:>7} "
                  f"{s.return_pct:>+8.1f}% {s.max_drawdown_pct:>7.1f}%")
        verdict_1d(values, scores, key)

    else:
        (k1, v1s), (k2, v2s) = axes[0], axes[1]
        print(f" {k1} down, {k2} across  ({args.metric})\n")
        print(f" {'':>10}" + "".join(f"{str(v):>9}" for v in v2s))
        grid = []
        for a in v1s:
            row = []
            for b in v2s:
                s = run_one(StrategyClass, {**fixed, k1: a, k2: b}, candles, args)
                row.append(getattr(s, args.metric))
            grid.append(row)
            print(f" {str(a):>10}" + "".join(f"{x:>+9.3f}" for x in row))
        flat = [x for row in grid for x in row]
        verdict_2d(flat)


def verdict_1d(values, scores, key) -> None:
    print()
    best_i = max(range(len(scores)), key=lambda i: scores[i])
    positive = sum(1 for s in scores if s > 0)
    print(f" best at {key}={values[best_i]} ({scores[best_i]:+.3f})")
    print(f" positive in {positive} of {len(scores)} settings")

    # Is the best value an isolated spike, or part of a plateau?
    neighbours = [scores[i] for i in (best_i - 1, best_i + 1) if 0 <= i < len(scores)]
    print()
    if positive <= 1:
        print(" READ: ONE setting works and the rest do not. That is a curve fit,")
        print(" not an edge. Do not trade it.")
    elif neighbours and all(n <= 0 for n in neighbours):
        print(" READ: the best setting is a lonely spike - its neighbours lose money.")
        print(" Fragile. A slightly different market and it stops working.")
    elif positive >= len(scores) * 0.7:
        print(" READ: broad plateau - most settings work. That is what a real edge")
        print(" looks like. Pick a middle value, not the peak.")
    else:
        print(" READ: mixed. Some regions work. Check whether the winners are")
        print(" adjacent (a real zone) or scattered (luck).")


def verdict_2d(flat) -> None:
    positive = sum(1 for x in flat if x > 0)
    print()
    print(f" positive in {positive} of {len(flat)} combinations")
    if positive >= len(flat) * 0.7:
        print(" READ: most of the grid works - the shape of a real edge.")
    elif positive <= len(flat) * 0.25:
        print(" READ: only a corner of the grid works. Treat as curve-fitted")
        print(" until proven otherwise on data you have not looked at.")
    else:
        print(" READ: mixed. Look for a contiguous block of winners, not scattered ones.")


if __name__ == "__main__":
    main()

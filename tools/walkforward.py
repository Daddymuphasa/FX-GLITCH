"""Walk-forward validation - optimise on the past, trade the future, repeat.

    # Does picking the best Donchian channel length actually survive?
    python tools/walkforward.py donchian_breakout --csv data/raw/btcusdt_1d.csv \
        --market binance-spot --vary entry=20,35,55,80,120

    # Two parameters, rolling window instead of expanding
    python tools/walkforward.py donchian_breakout --csv data/raw/btcusdt_1d.csv \
        --market binance-spot --vary entry=20,55,120 --vary atr_mult=1.5,2.5,3.5 \
        --rolling

    # No --vary: no optimisation, just fixed rules measured fold by fold.
    # Use this to see how unstable the strategy is across regimes.
    python tools/walkforward.py donchian_breakout --csv data/raw/btcusdt_1d.csv \
        --market binance-spot

HOW THIS DIFFERS FROM tools/sweep.py, AND WHY YOU WANT BOTH
-----------------------------------------------------------
sweep.py asks: is the good result a broad plateau or a lonely spike? It looks
at the SHAPE of the parameter surface, on all the data at once.

walkforward.py asks a harder question: if you had picked a setting using only
what you knew at the time, would it have worked next year? It never lets the
chooser see the data it is judged on.

A strategy can pass sweep and fail here. That combination means the surface
looked healthy but its best region kept moving - the plateau was real in each
period and in a different place each period, so no rule you could have written
in advance would have landed on it.

WHAT THE OUTPUT IS FOR
----------------------
The headline is not the return. It is the OPTIMISM GAP: how much better the
optimised in-sample number looked than what actually happened next. That gap is
the tax on every backtest you have ever read, this repo's included, and it is
the closest thing here to an honest estimate of how much to discount them by.

Three caveats worth saying out loud:

  - Few folds means few numbers. Five folds gives five out-of-sample results,
    and five of anything is not a distribution. Read the aggregate, not a fold.
  - This validates a PROCEDURE, not a parameter. Passing does not mean "trade
    entry=55". It means "refitting this parameter periodically was not a fantasy".
  - Nothing here fixes the deeper problem that you already know how BTC did
    since 2015. You chose to test a breakout on crypto because you know it ran.
    Walk-forward cannot unsee that, and no software can.
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch import data, markets, simulate  # noqa: E402
from fxglitch.engine import Backtest  # noqa: E402
from fxglitch.metrics import Stats, analyse  # noqa: E402
from fxglitch.walkforward import combine, drift, make_folds, trim_result  # noqa: E402
from run import coerce, load_strategy  # noqa: E402


def backtest(StrategyClass, kwargs, candles, args, symbol="wf"):
    return Backtest(candles, StrategyClass(**kwargs), symbol=symbol,
                    starting_equity=args.equity, risk_pct=args.risk,
                    spread=args.spread, fee_pct=args.fee,
                    slippage_pct=args.slippage).run()


def label(combo: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in combo.items()) or "(defaults)"


def choose(StrategyClass, fixed, combos, train, args) -> tuple[dict, Stats, bool]:
    """Pick the best parameters on the training window only.

    Returns (combo, its in-sample stats, whether the trade-count floor was met).
    A candidate with four trades can post a spectacular expectancy; ranking on
    it is how you end up carrying pure noise into the next window. Candidates
    below `--min-trades` are set aside, and if none survive we say so in the
    output rather than pretending the pick was informed.
    """
    scored = []
    for combo in combos:
        s = analyse(backtest(StrategyClass, {**fixed, **combo}, train, args))
        scored.append((combo, s))

    eligible = [(c, s) for c, s in scored if s.trades >= args.min_trades]
    pool, enough = (eligible, True) if eligible else (scored, False)
    best = max(pool, key=lambda cs: getattr(cs[1], args.metric))
    return best[0], best[1], enough


def main() -> None:
    p = argparse.ArgumentParser(description="Walk-forward validation")
    p.add_argument("strategy")
    p.add_argument("--csv")
    p.add_argument("--sim")
    p.add_argument("--bars", type=int, default=10000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--start")
    p.add_argument("--end")

    p.add_argument("--vary", action="append", default=[], metavar="KEY=V1,V2,V3",
                   help="parameter to optimise per fold; repeatable for a grid")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="hold this parameter fixed in every fold")

    p.add_argument("--folds", type=int, default=5, help="out-of-sample windows (default 5)")
    p.add_argument("--min-train", type=float, default=0.4, metavar="FRAC",
                   help="fraction of data reserved for the first training window")
    p.add_argument("--rolling", action="store_true",
                   help="train on a fixed recent window instead of everything before")
    p.add_argument("--warmup", type=int, default=250, metavar="BARS",
                   help="bars of history fed to each fold so indicators are warm")
    p.add_argument("--min-trades", type=int, default=10,
                   help="ignore training candidates with fewer trades than this")
    p.add_argument("--metric", default="expectancy_r",
                   choices=["expectancy_r", "profit_factor", "return_pct", "win_rate"],
                   help="what 'best' means when picking on the training window")

    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--spread", type=float, default=0.0)
    p.add_argument("--fee", type=float, default=0.0)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--market")
    args = p.parse_args()

    if args.market:
        m = markets.get(args.market)
        args.fee, args.slippage, args.spread = m.fee_pct, m.slippage_pct, m.spread

    if args.csv:
        candles = data.load_csv(args.csv)
        name = os.path.basename(args.csv)
    elif args.sim:
        candles = simulate.preset(args.sim, bars=args.bars, seed=args.seed)
        name = f"{args.sim} (simulated)"
    else:
        p.error("choose --csv or --sim")

    candles = data.slice_dates(candles, args.start, args.end)
    StrategyClass = load_strategy(args.strategy)
    fixed = {k: coerce(v) for k, v in (kv.split("=", 1) for kv in args.set)}

    axes = []
    for spec in args.vary:
        key, raw = spec.split("=", 1)
        axes.append((key, [coerce(v) for v in raw.split(",")]))
    if axes:
        keys = [k for k, _ in axes]
        combos = [dict(zip(keys, vals)) for vals in itertools.product(*[v for _, v in axes])]
    else:
        combos = [{}]

    try:
        folds = make_folds(len(candles), folds=args.folds,
                           min_train_frac=args.min_train,
                           rolling=args.rolling, warmup=args.warmup)
    except ValueError as exc:
        sys.exit(f"\n{exc}")

    mode = "rolling" if args.rolling else "anchored"
    print(f"\nWalk-forward: {args.strategy} on {name} ({len(candles):,} bars)")
    print(f"{len(folds)} folds, {mode} training, {args.warmup}-bar warm-up, "
          f"picking on {args.metric}")
    if axes:
        print(f"grid: {len(combos)} combination{'s' if len(combos) != 1 else ''} "
              f"of {', '.join(k for k, _ in axes)}")
    else:
        print("no --vary given: fixed parameters, so this measures stability, "
              "not optimisation")

    head = (f"\n {'fold':>4} {'train':>22} {'picked':>26} {'IS':>8}   "
            f"{'out-of-sample':>22} {'n':>4} {'OOS':>8}")
    print(head)
    print(" " + "-" * (len(head) - 2))

    oos_results, base_results, is_scores, picks, thin = [], [], [], [], 0
    for f in folds:
        train = candles[f.train_start:f.train_end]
        combo, is_stats, enough = choose(StrategyClass, fixed, combos, train, args)
        if not enough:
            thin += 1

        window = candles[f.warmup_start:f.test_end]
        opens_at = candles[f.test_start].time

        def out_of_sample(params):
            raw = backtest(StrategyClass, params, window, args, symbol=f"fold{f.index}")
            return trim_result(raw, opens_at, starting_equity=args.equity,
                               risk_pct=args.risk)

        oos = out_of_sample({**fixed, **combo})
        s = analyse(oos)

        # The control arm: the same windows, never optimised. If optimisation
        # is worth doing, it has to beat leaving the dial alone.
        if combos != [{}]:
            base_results.append(out_of_sample(dict(fixed)))

        oos_results.append(oos)
        is_scores.append(is_stats.expectancy_r)
        picks.append(combo)

        span = lambda a, b: (f"{candles[a].time:%Y-%m}..{candles[b - 1].time:%Y-%m}")
        print(f" {f.index:>4} {span(f.train_start, f.train_end):>22}"
              f" {label(combo):>26} {is_stats.expectancy_r:>+8.3f}   "
              f"{span(f.test_start, f.test_end):>22} {s.trades:>4} "
              f"{s.expectancy_r:>+8.3f}" + ("" if enough else "  <- thin"))

    report(oos_results, base_results, is_scores, picks, thin, combos, args)


def report(oos_results, base_results, is_scores, picks, thin, combos, args) -> None:
    account = combine(oos_results, starting_equity=args.equity, risk_pct=args.risk)
    agg = analyse(account)
    mean_is = sum(is_scores) / len(is_scores)
    gap = mean_is - agg.expectancy_r
    changed = drift(picks)
    winning_folds = sum(1 for r in oos_results if analyse(r).expectancy_r > 0)

    print("\n" + "=" * 62)
    print(" OUT-OF-SAMPLE, ALL FOLDS CHAINED INTO ONE ACCOUNT")
    print("=" * 62)
    print(f"   trades              {agg.trades}")
    print(f"   expectancy          {agg.expectancy_r:+.3f} R")
    print(f"   profit factor       "
          f"{'inf' if agg.profit_factor == float('inf') else f'{agg.profit_factor:.2f}'}")
    print(f"   return              {agg.return_pct:+.1f}%  "
          f"({args.equity:,.0f} -> {agg.final_equity:,.0f})")
    print(f"   max drawdown        {agg.max_drawdown_pct:.1f}%  (closed-trade)")
    print(f"   folds in profit     {winning_folds} of {len(oos_results)}")
    print()
    print(f"   in-sample best      {mean_is:+.3f} R   (average across folds)")
    print(f"   OPTIMISM GAP        {gap:+.3f} R")
    if len(combos) > 1:
        print(f"   parameter drift     {changed:.0%} of handovers changed the setting")

    edge_of_tuning = None
    if base_results:
        base = analyse(combine(base_results, starting_equity=args.equity,
                               risk_pct=args.risk))
        edge_of_tuning = agg.expectancy_r - base.expectancy_r
        print()
        print(f"   never optimised     {base.expectancy_r:+.3f} R over "
              f"{base.trades} trades, same windows")
        print(f"   TUNING WAS WORTH    {edge_of_tuning:+.3f} R per trade")

    print()
    if agg.trades < 30:
        print("   READ: too few out-of-sample trades to conclude anything. Every")
        print("   number above is noise. More data or fewer folds.")
    elif agg.expectancy_r <= 0:
        print("   READ: the procedure lost money on data it had not seen. Whatever")
        print("   the in-sample numbers said, this is the one that counts.")
    elif gap > 0.5:
        print("   READ: positive out-of-sample, but optimisation flattered it badly.")
        print("   Believe the OOS number, not the backtest that sold you on it.")
    else:
        print("   READ: positive out-of-sample and the in-sample number was not far")
        print("   off it. That is the good outcome. Still only "
              f"{len(oos_results)} independent windows.")

    if edge_of_tuning is not None and edge_of_tuning <= 0:
        print()
        print("   TUNING PAID NOTHING: refitting the parameters each fold did no")
        print("   better than leaving them alone - it did worse. The search found")
        print("   last window's noise and carried it into the next one. Ship the")
        print("   defaults and spend the effort somewhere it measures.")

    if len(combos) > 1 and changed >= 0.75:
        print()
        print("   PARAMETER DRIFT: the winning setting changed at nearly every")
        print("   handover. Even if the returns hold up, there is no setting here")
        print("   to carry forward - you are refitting noise each window.")
    if thin:
        print()
        print(f"   {thin} fold(s) had no training candidate reaching "
              f"{args.min_trades} trades.")
        print("   Those picks were effectively arbitrary. Lower --folds or "
              "--min-trades.")
    print("=" * 62)


if __name__ == "__main__":
    main()

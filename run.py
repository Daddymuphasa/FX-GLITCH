"""Run a strategy and print the verdict.

Examples:

    # Simulated V75, 20000 one-minute bars, using the example strategy
    python run.py ema_cross --sim V75 --bars 20000

    # Your own CSV
    python run.py ema_cross --csv data/raw/xauusd_m15.csv --spread 0.30

    # The noise test: same strategy, 10 different random markets.
    # If it "works" on all of them, it is not finding anything real.
    python run.py ema_cross --sim V75 --noise-test

    # Pass strategy parameters straight through
    python run.py ema_cross --sim V75 --set fast=10 --set slow=30 --set rr=3
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

from fxglitch import data, factors, markets, news, report, simulate
from fxglitch.signals import SignalFeed
from fxglitch.engine import Backtest
from fxglitch.metrics import analyse


def load_strategy(name: str):
    module_name = name if name.startswith("strategies.") else f"strategies.{name}"
    try:
        mod = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if module_name.split(".")[-1] not in str(exc):
            raise
        available = sorted(
            f[:-3] for f in os.listdir("strategies")
            if f.endswith(".py") and not f.startswith("_")
        )
        sys.exit(f"No strategy named {name!r}. Available: {', '.join(available) or '(none yet)'}")
    if not hasattr(mod, "strategy"):
        sys.exit(f"{module_name} must define a module-level `strategy = YourClass`.")
    return mod.strategy


def coerce(value: str):
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return {"true": True, "false": False}.get(value.lower(), value)


def main() -> None:
    p = argparse.ArgumentParser(description="FX-GLITCH strategy runner")
    p.add_argument("strategy", nargs="?", help="module name in strategies/, e.g. ema_cross")

    src = p.add_argument_group("data source")
    src.add_argument("--csv", help="path to an OHLC csv file")
    src.add_argument("--sim", help=f"simulated market: {', '.join(sorted(simulate.PRESETS))}")
    src.add_argument("--bars", type=int, default=10000, help="bars to simulate (default 10000)")
    src.add_argument("--seed", type=int, default=42, help="simulation seed")
    src.add_argument("--start", help="trim data from this date, e.g. 2024-01-01")
    src.add_argument("--end", help="trim data to this date")

    intel = p.add_argument_group("intelligence layer")
    intel.add_argument("--signals", action="store_true",
                       help="compute price/volume factors and feed them to the strategy")
    intel.add_argument("--events", metavar="PATH",
                       help="catalyst log (csv/json) to add to the signal feed")
    intel.add_argument("--explain", metavar="YYYY-MM-DD",
                       help="print the full signal breakdown for one date and exit")

    acct = p.add_argument_group("account")
    acct.add_argument("--equity", type=float, default=1000.0)
    acct.add_argument("--risk", type=float, default=1.0, help="percent risked per trade")
    acct.add_argument("--spread", type=float, default=0.0,
                      help="ABSOLUTE spread in price units (forex/CFD model)")
    acct.add_argument("--fee", type=float, default=0.0, metavar="PCT",
                      help="PERCENT fee per side (crypto model). Binance spot taker = 0.1")
    acct.add_argument("--slippage", type=float, default=0.0, metavar="PCT",
                      help="percent slippage per side. 0.02 is fair on liquid BTC")
    acct.add_argument("--market", metavar="KEY",
                      help="realistic cost preset, e.g. binance-spot. "
                           "Overrides --fee/--slippage/--spread. Use --markets to list")

    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="strategy parameter override; repeatable")
    p.add_argument("--noise-test", action="store_true",
                   help="rerun on 10 random markets to see if the edge is real")
    p.add_argument("--log", type=int, default=20, help="trades to print (0 for none)")
    p.add_argument("--save", metavar="PATH", help="write the trade list to a csv")

    p.add_argument("--markets", action="store_true", help="list cost presets and exit")

    args = p.parse_args()

    if args.markets:
        print(markets.table())
        return

    if args.market:
        m = markets.get(args.market)
        args.fee, args.slippage, args.spread = m.fee_pct, m.slippage_pct, m.spread
        print(f"\nCosts: {m.name} - {m.round_trip_pct:.3f}% round trip"
              + (f" plus {m.spread} spread" if m.spread else ""))
        if m.note:
            print(f"  note: {m.note}")

    if not args.strategy:
        p.error("which strategy? e.g. `python run.py ema_cross --sim V75`")
    if not args.csv and not args.sim:
        p.error("choose a data source: --csv PATH or --sim NAME")

    StrategyClass = load_strategy(args.strategy)
    overrides = dict(kv.split("=", 1) for kv in args.set)
    kwargs = {k: coerce(v) for k, v in overrides.items()}

    if args.csv:
        candles = data.load_csv(args.csv)
        symbol = os.path.basename(args.csv)
    else:
        candles = simulate.preset(args.sim, bars=args.bars, seed=args.seed)
        symbol = f"{args.sim.upper()} (simulated)"

    candles = data.slice_dates(candles, args.start, args.end)
    print(f"\nLoaded {symbol}: {data.describe(candles)}")

    feed = None
    if args.signals or args.events:
        sigs = factors.all_price_factors(candles) if args.signals else []
        if args.events:
            loaded = news.load_events(args.events)
            sigs += loaded
            print(f"Loaded {len(loaded)} catalysts from {os.path.basename(args.events)}")
        feed = SignalFeed(sigs)
        print(f"Signal feed: {len(feed)} signals "
              f"({'price factors' if args.signals else ''}"
              f"{' + ' if args.signals and args.events else ''}"
              f"{'catalysts' if args.events else ''})")

    if args.explain:
        if feed is None:
            sys.exit("--explain needs --signals and/or --events")
        when = data._parse_time(args.explain)
        print()
        print(feed.explain(when, verbose=True))
        return

    result = Backtest(
        candles, StrategyClass(**kwargs), symbol=symbol, feed=feed,
        starting_equity=args.equity, risk_pct=args.risk, spread=args.spread,
        fee_pct=args.fee, slippage_pct=args.slippage,
    ).run()

    bench = report.buy_and_hold(candles, args.equity)
    print()
    print(report.summary(result, benchmark=bench))
    print()
    print(report.equity_sparkline(result))
    if args.log:
        print()
        print(report.trade_log(result, limit=args.log))

    if args.save:
        print(f"\nTrades written to {report.save_trades_csv(result, args.save)}")

    if args.noise_test:
        run_noise_test(StrategyClass, kwargs, args, analyse(result).expectancy_r,
                       like=candles)


def run_noise_test(StrategyClass, kwargs, args, real_expectancy: float,
                   like: list | None = None) -> None:
    """Run the same rules against pure randomness and compare.

    This is the single most useful thing in this repo. Any set of rules will
    produce winners on some random data. The question is never 'did it make
    money' - it is 'did it make more money than the same rules make on noise'.
    """
    print("\n" + "=" * 62)
    print(" NOISE TEST - the same rules on 10 random markets")
    print("=" * 62)

    # Match the noise to the real series: same number of bars, same starting
    # price, same measured volatility. Otherwise the comparison is rigged.
    if like:
        n_bars = len(like)
        start_price = like[0].close
        annual_vol = simulate.realised_vol(like)
        bar_minutes = simulate.infer_bar_minutes(like)
        print(f" matched to the real series: {n_bars:,} bars, start {start_price:,.2f},"
              f" measured vol {annual_vol*100:.0f}%/yr, {bar_minutes}m bars\n")
    else:
        n_bars, start_price, annual_vol, bar_minutes = args.bars, 1000.0, 0.75, 1

    results = []
    for seed in range(1000, 1010):
        noise = simulate.random_walk(
            bars=n_bars, start_price=start_price, annual_vol=annual_vol,
            bar_minutes=bar_minutes, seed=seed,
        )
        r = Backtest(noise, StrategyClass(**kwargs), symbol=f"noise-{seed}",
                     starting_equity=args.equity, risk_pct=args.risk,
                     spread=args.spread, fee_pct=args.fee,
                     slippage_pct=args.slippage).run()
        s = analyse(r)
        results.append(s.expectancy_r)
        print(f"   seed {seed}:  {s.trades:>4} trades   expectancy {s.expectancy_r:+.3f} R")

    avg = sum(results) / len(results)
    best = max(results)
    print("-" * 62)
    print(f"   noise average   {avg:+.3f} R")
    print(f"   noise best      {best:+.3f} R   <- luck can look this good")
    print(f"   YOUR RESULT     {real_expectancy:+.3f} R")
    print()
    if real_expectancy <= best:
        print("   READ: your result sits inside the range random luck produces.")
        print("   That is not evidence of an edge.")
    else:
        print("   READ: your result beat all 10 random runs. Encouraging, not proof.")
        print("   Next step: check it holds on data you have never tested on.")
    print("=" * 62)


if __name__ == "__main__":
    main()

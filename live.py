"""Run a strategy against a live venue. Dry-run unless you say otherwise.

    # Watch what it would do. Sends nothing. No API key needed.
    python live.py donchian_breakout --symbols BTCUSDT,ETHUSDT

    # The top 20 USDT perps by the venue's own listing, BTC first
    python live.py donchian_breakout --top 20

    # Once every hour, forever
    python live.py donchian_breakout --top 20 --every 1h

    # For real. Requires BITUNIX_API_KEY and BITUNIX_SECRET_KEY.
    python live.py donchian_breakout --symbols BTCUSDT --live

THE ORDER TO DO THIS IN
-----------------------
1. Dry-run for a few weeks on the timeframe you intend to trade. Read the
   decisions. The question is not "does it work" - it is "are these the trades
   I would have taken", and it is answerable for free.
2. `python tools/bitunix_check.py --private` with a READ-ONLY key, to prove the
   signing works before a key that can trade exists.
3. `--live` with one symbol and an amount you would shrug at.
4. More symbols, only after the portfolio layer exists. Twenty alt perps are
   not twenty bets; they are one leveraged BTC bet wearing a disguise, and
   nothing in this file knows that yet.

WHAT --live ACTUALLY CHANGES
----------------------------
Nothing about the decisions. Dry-run computes every one in full - guards,
sizing, the exact order - and stops at the send. So the dry-run log is an
honest preview, not an approximation.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from fxglitch.live.guards import Limits
from fxglitch.live.portfolio import ExposureLimits
from fxglitch.live.screener import ScreenRules
from fxglitch.live.runner import INTERVAL_SECONDS, Runner
from fxglitch.live.state import State
from fxglitch.venues.base import VenueError
from fxglitch.venues.bitunix import Bitunix
from run import coerce, load_strategy

VENUES = {"bitunix": Bitunix}


def parse_every(raw: str) -> int:
    """'30s', '15m', '1h', '1d' -> seconds."""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if raw and raw[-1] in units:
        return int(float(raw[:-1]) * units[raw[-1]])
    return int(raw)


def main() -> None:
    p = argparse.ArgumentParser(description="Run a strategy on a live venue")
    p.add_argument("strategy")
    p.add_argument("--venue", default="bitunix", choices=sorted(VENUES))
    p.add_argument("--symbols", help="comma separated, e.g. BTCUSDT,ETHUSDT")
    p.add_argument("--top", type=int, metavar="N",
                   help="first N tradeable USDT perps, BTC first")
    p.add_argument("--interval", default="1d", help="bar size (default 1d)")
    p.add_argument("--history", type=int, default=400,
                   help="bars fed to the strategy; must exceed its longest lookback")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="strategy parameter override; repeatable")

    risk = p.add_argument_group("limits - every one of these errs tight on purpose")
    risk.add_argument("--risk", type=float, default=1.0, help="percent of equity per trade")
    risk.add_argument("--max-risk", type=float, default=2.0, help="hard ceiling on that")
    risk.add_argument("--max-positions", type=int, default=3)
    risk.add_argument("--max-leverage", type=int, default=5)
    risk.add_argument("--max-daily-loss", type=float, default=6.0, metavar="PCT")
    risk.add_argument("--min-free", type=float, default=20.0, metavar="PCT",
                      help="percent of equity to keep uncommitted")

    scan = p.add_argument_group("universe screening - one request, not 625")
    scan.add_argument("--screen", action="store_true",
                      help="screen the whole universe each cycle instead of a fixed list")
    scan.add_argument("--top-n", type=int, default=10, metavar="N",
                      help="symbols to examine after screening (default 10)")
    scan.add_argument("--min-turnover", type=float, default=10_000_000.0,
                      metavar="USDT", help="24h turnover floor - a liquidity filter, "
                                           "not an edge claim")
    scan.add_argument("--rank-by", default="turnover",
                      choices=["turnover", "change", "abs_change"],
                      help="turnover is a cost decision; the others are unmeasured "
                           "claims about edge")

    gate = p.add_argument_group("BTC gate and correlated exposure")
    gate.add_argument("--no-gate", action="store_true",
                      help="stop blocking alt entries against the BTC trend")
    gate.add_argument("--driver", default="BTCUSDT")
    gate.add_argument("--regime-period", type=int, default=50)
    gate.add_argument("--max-same-direction", type=int, default=3,
                      help="positions allowed to point the same way")
    gate.add_argument("--max-directional", type=float, default=60.0, metavar="PCT",
                      help="BTC-equivalent notional on one side, as %% of equity")

    p.add_argument("--paper-equity", type=float, default=1000.0, metavar="USDT",
                   help="assumed balance for dry-run when no API key is set")
    p.add_argument("--state", default="data/live_state.json",
                   help="where the crash-safe state lives")
    p.add_argument("--every", metavar="30s|15m|1h",
                   help="loop forever at this interval instead of one pass")
    p.add_argument("--live", action="store_true",
                   help="actually send orders. Without this, nothing is sent.")
    p.add_argument("--resume", action="store_true",
                   help="clear a persisted halt and start trading again")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    venue = VENUES[args.venue]()
    StrategyClass = load_strategy(args.strategy)
    params = {k: coerce(v) for k, v in (kv.split("=", 1) for kv in args.set)}

    try:
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        elif args.top:
            symbols = venue.universe()[:args.top]
        else:
            symbols = ["BTCUSDT"]
    except VenueError as exc:
        sys.exit(f"Could not reach {args.venue}: {exc}")

    state = State.load(args.state)
    if args.resume and state.halted:
        print(f"clearing halt: {state.halt_reason}")
        state.resume()
    if state.halted:
        sys.exit(f"HALTED: {state.halt_reason}\n"
                 f"Look at why before clearing it, then rerun with --resume.")

    limits = Limits(
        risk_pct=args.risk, max_risk_pct=args.max_risk,
        max_positions=args.max_positions, max_leverage=args.max_leverage,
        max_daily_loss_pct=args.max_daily_loss, min_free_balance_pct=args.min_free,
    )

    runner = Runner(venue, StrategyClass, symbols, interval=args.interval,
                    params=params, limits=limits, state=state,
                    history=args.history, live=args.live,
                    paper_equity=args.paper_equity,
                    screen_rules=(ScreenRules(
                        min_turnover=args.min_turnover, top=args.top_n,
                        rank_by=args.rank_by, always_include=(args.driver,))
                        if args.screen else None),
                    exposure_limits=ExposureLimits(
                        max_directional_pct=args.max_directional,
                        max_same_direction=args.max_same_direction),
                    driver=args.driver, regime_period=args.regime_period,
                    use_gate=not args.no_gate)

    mode = "LIVE - orders will be sent" if args.live else "dry-run - nothing will be sent"
    print(f"\n{StrategyClass.name}")
    print(f"venue {venue.name}, {len(symbols)} symbol(s), {args.interval} bars")
    print(f"risk {args.risk}% per trade, max {args.max_positions} positions, "
          f"halt at -{args.max_daily_loss}% daily")
    print(f"BTC gate {'off' if args.no_gate else 'on'}, "
          f"max {args.max_same_direction} positions the same way, "
          f"max {args.max_directional:.0f}% directional exposure")
    if args.screen:
        print(f"screening the universe each cycle: top {args.top_n} by "
              f"{args.rank_by}, turnover above {args.min_turnover:,.0f}")
    print(f"MODE: {mode}\n")

    if args.live and not getattr(venue, "authenticated", False):
        sys.exit("--live needs BITUNIX_API_KEY and BITUNIX_SECRET_KEY in the "
                 "environment.")

    interval = parse_every(args.every) if args.every else None
    try:
        while True:
            for decision in runner.cycle():
                if decision.action != "none" or args.verbose:
                    print(decision)
            if interval is None:
                break
            if runner.state.halted:
                print(f"\nHALTED: {runner.state.halt_reason}")
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped. State saved - restarting will not re-act on bars "
              "already decided.")
        runner.state.save()


if __name__ == "__main__":
    main()

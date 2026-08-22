"""Verify the Bitunix client against the real exchange. Reads only, never trades.

    python tools/bitunix_check.py                 # public data, no API key needed
    python tools/bitunix_check.py --private       # also check your keys and balance

WHY THIS EXISTS
---------------
Every test in tests/test_bitunix.py runs against a fake transport, which proves
the client is internally consistent and proves nothing about whether Bitunix
agrees. Docs go stale, fields get renamed, and a venue's real responses are the
only authority. This is the script that closes that gap.

Run it before wiring anything to real money. It touches five things - the
symbol universe, contract precision, candles, your balance and your open
positions - and if all five come back sane, the client is talking to the
exchange correctly.

It cannot place, modify or cancel an order. There is no flag for that.

FOR --private YOU NEED, in the environment and never in a file that git can see:

    BITUNIX_API_KEY=...
    BITUNIX_SECRET_KEY=...

PowerShell, current session only:
    $env:BITUNIX_API_KEY="..." ; $env:BITUNIX_SECRET_KEY="..."

Create the key with TRADE permission disabled until you actually want to trade.
A read-only key makes this script's promise structural rather than a matter of
trusting the code above.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fxglitch.venues.base import VenueError  # noqa: E402
from fxglitch.venues.bitunix import Bitunix  # noqa: E402


def check_public(client: Bitunix, symbol: str, interval: str, bars: int) -> bool:
    print("\n1. SYMBOL UNIVERSE")
    instruments = client.instruments()
    universe = client.universe()
    print(f"   {len(instruments):,} pairs listed, {len(universe):,} USDT perps tradeable")
    print(f"   first five (BTC leads by design): {', '.join(universe[:5])}")
    if symbol not in instruments:
        print(f"   FAIL: {symbol} is not in the universe")
        return False

    print(f"\n2. CONTRACT SPEC for {symbol}")
    i = instruments[symbol]
    print(f"   size step {10 ** -i.qty_precision:g} {i.base}, "
          f"min order {i.min_qty} {i.base}")
    print(f"   price step {10 ** -i.price_precision:g}, max leverage {i.max_leverage}x")
    print(f"   -> $100 risked on a $500 stop = "
          f"{i.round_qty(100 / 500)} {i.base}")

    print(f"\n3. CANDLES  {symbol} {interval}, asking for {bars}")
    candles = client.candles(symbol, interval, limit=bars)
    if not candles:
        print("   FAIL: no candles returned")
        return False
    print(f"   got {len(candles)} bars, {candles[0].time:%Y-%m-%d %H:%M} "
          f"-> {candles[-1].time:%Y-%m-%d %H:%M}")
    print(f"   last close {candles[-1].close:,.2f}")
    if len(candles) < bars:
        print(f"   NOTE: asked {bars}, got {len(candles)}. If a strategy needs a "
              f"{bars}-period filter, it will be blind. Check history depth.")
    ordered = all(a.time < b.time for a, b in zip(candles, candles[1:]))
    print(f"   ordered oldest-first: {'yes' if ordered else 'NO - BUG'}")
    return ordered


def check_private(client: Bitunix) -> bool:
    print("\n4. CREDENTIALS AND BALANCE")
    if not client.authenticated:
        print("   BITUNIX_API_KEY / BITUNIX_SECRET_KEY are not set. Skipping.")
        return False
    balance = client.balance()
    print(f"   signature accepted - the two-pass SHA256 is correct")
    print(f"   {balance.currency}: {balance.available:,.2f} available, "
          f"{balance.used:,.2f} as margin, equity {balance.equity:,.2f}")
    print(f"   position mode: {client.position_mode()}")
    if client.position_mode() == "HEDGE":
        print("   note: hedge mode, so closes need a positionId. Handled.")

    print("\n5. OPEN POSITIONS")
    positions = client.positions()
    if not positions:
        print("   none open")
    for p in positions:
        liq = f", liq {p.liquidation_price:,.2f}" if p.liquidation_price else ""
        print(f"   {p.symbol} {p.side} {p.qty} @ {p.entry_price:,.2f} "
              f"({p.leverage}x, pnl {p.unrealised_pnl:+,.2f}{liq})")
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only Bitunix connectivity check")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1d")
    p.add_argument("--bars", type=int, default=250,
                   help="past the 200 cap on purpose - it exercises paging")
    p.add_argument("--private", action="store_true",
                   help="also verify API keys, balance and open positions")
    args = p.parse_args()

    client = Bitunix()
    print(f"Bitunix check -> {client.base_url}")
    print("read-only: this script cannot place an order")

    try:
        ok = check_public(client, args.symbol, args.interval, args.bars)
        if args.private:
            ok = check_private(client) and ok
    except VenueError as exc:
        print(f"\nFAILED: {exc}")
        if exc.code == "network":
            print("No route to Bitunix. VPN, firewall, or the region block.")
        elif exc.code == "no-credentials":
            print("Set BITUNIX_API_KEY and BITUNIX_SECRET_KEY, or drop --private.")
        sys.exit(1)

    print("\n" + "=" * 58)
    if ok:
        print(" All checks passed. The client agrees with the exchange.")
        if not args.private:
            print(" Next: --private, with a read-only key, to verify signing.")
    else:
        print(" Something disagreed. Read the FAIL lines above; do not trade")
        print(" until they are gone.")
    print("=" * 58)


if __name__ == "__main__":
    main()

"""Analyze Telegram-style trade messages with the trading intelligence layer.

Examples:

    python tools/analyze_telegram.py --symbol V75 --strategy trend_pullback --message "BUY V75 ENTRY: 8237 SL: 8199 TP: 8290"

    python tools/analyze_telegram.py --symbol XAUUSD --strategy gold_breakout --file signals.txt
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fxglitch.intelligence import (
    analyze_with_openai,
    build_context,
    local_summary,
    parse_telegram_signal,
)


def main() -> None:
    p = argparse.ArgumentParser(description="Analyze Telegram-style signals")
    p.add_argument("--symbol", action="append", default=[], help="symbol under analysis; repeatable")
    p.add_argument("--strategy", action="append", default=[], help="candidate strategy name; repeatable")
    p.add_argument("--message", action="append", default=[], help="raw Telegram message; repeatable")
    p.add_argument("--file", help="text file containing one signal per line")
    p.add_argument("--market-note", default="", help="optional extra market context")
    args = p.parse_args()

    messages = list(args.message)
    if args.file:
        messages.extend([line.strip() for line in Path(args.file).read_text(encoding="utf-8").splitlines()
                         if line.strip()])

    telegram = [parse_telegram_signal(message) for message in messages]
    context = build_context(
        symbols=args.symbol,
        strategies=args.strategy,
        telegram=telegram,
        market_note=args.market_note,
    )

    try:
        print(analyze_with_openai(context))
    except Exception:
        print(local_summary(context))
        if telegram:
            print("\nparsed signals:")
            for sig in telegram:
                print(f"- {sig.symbol or 'unknown'} {sig.direction} entry={sig.entry} sl={sig.stop_loss} tp={sig.take_profit}")


if __name__ == "__main__":
    main()

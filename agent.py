"""CLI cycle for the Agent OS product. Dry-run unless --live.

    python agent.py
    python agent.py --symbol ETHUSDT
    python agent.py --message "BTCUSDT long CMP tp 120000 sl 105000 lev 5x"
    python agent.py --demo
    python -m fxglitch.agent.mcp_server
"""

from __future__ import annotations

import argparse
import json
import sys

from fxglitch.agent.session import judge_demo, run_cycle
from fxglitch.venues.binance import Binance


def main() -> None:
    p = argparse.ArgumentParser(description="FX-GLITCH positioning + policy cycle")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--message", help="optional external signal text")
    p.add_argument("--live", action="store_true",
                   help="send if policy allows AND keys are set. Default: dry-run")
    p.add_argument("--demo", action="store_true", help="offline judge flow, no network")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.demo:
        cases = judge_demo()
        if args.json:
            json.dump(cases, sys.stdout, indent=2)
            print()
            return
        for case in cases:
            pol = case["policy"]
            mark = "ALLOW" if pol["allowed"] else "BLOCK"
            print(f"{mark:5}  {case['title']:<22}  {pol['reason']}")
        return

    venue = Binance() if args.live else None
    result = run_cycle(args.symbol, message=args.message, live=args.live, venue=venue)
    if args.json:
        json.dump(result.to_dict(), sys.stdout, indent=2, default=str)
        print()
        return
    b, prop, pol = result.briefing, result.proposal, result.policy
    print(f"\n{b['symbol']}  mark {b['mark_price']}  funding {b['last_funding']*100:.4f}%/8h")
    print(f"crowd: {b['crowd']}")
    print(f"quadrant: {b['oi_quadrant']}  24h {b['price_change_24h_pct']:+.2f}%")
    print(f"\nproposal: {prop['action']} {prop.get('direction') or ''}  ({prop['source']})")
    print(f"          {prop['reason']}")
    print(f"\npolicy:   {'ALLOW' if pol['allowed'] else 'BLOCK'}  {pol['reason']}")
    print(f"mode:     {'SENT' if result.sent else 'dry-run'}\n")


if __name__ == "__main__":
    main()

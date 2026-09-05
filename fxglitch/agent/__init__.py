"""The Agent OS layer: positioning in, policy out, orders only if allowed.

This package is the hackathon product. Chart-pattern strategies are not
used here. An AI agent (or a pasted signal) may *propose* a trade.
FX-GLITCH decides whether that trade is allowed, using:

- public Binance futures positioning (funding, open interest, long/short)
- hard risk guards (stop required, leverage cap, daily loss, margin reserve)
- portfolio exposure counted as one BTC bet, not N tickets

Nothing in this package invents a Donchian/EMA/RSI entry.
"""

from .positioning import Briefing, fetch_briefing
from .policy import ProposedTrade, PolicyDecision, evaluate
from .session import CycleResult, run_cycle, judge_demo

__all__ = [
    "Briefing",
    "fetch_briefing",
    "ProposedTrade",
    "PolicyDecision",
    "evaluate",
    "CycleResult",
    "run_cycle",
    "judge_demo",
]

"""Realistic trading costs per market, so you never have to remember them.

Use `--market binance-spot` on the runner instead of hand-typing `--fee 0.1
--slippage 0.02`. Guessing costs low is the most common way a backtest lies,
and the numbers below are deliberately on the pessimistic side of fair.

A note on why crypto costs bite so much harder than forex. On XAUUSD you pay a
spread of maybe 0.01% of price. On Binance spot you pay 0.1% per side - ten
times as much, twice per trade. A strategy taking one trade a day at that rate
burns roughly 73% of its capital in fees over a year before it makes a single
cent of profit. That is not a detail to add later; it decides which strategies
can exist at all. It is why high-frequency scalping on retail crypto fees is
mathematically close to hopeless, and why the same rules can be fine on a 4h
chart and ruinous on a 5m one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    name: str
    fee_pct: float = 0.0        # percent of notional, per side
    slippage_pct: float = 0.0   # percent per side
    spread: float = 0.0         # absolute price units, per round trip
    note: str = ""

    @property
    def round_trip_pct(self) -> float:
        return (self.fee_pct + self.slippage_pct) * 2


MARKETS: dict[str, Market] = {
    # --- crypto: percentage fees, charged both sides -----------------------
    "binance-spot": Market(
        "Binance spot (taker)", fee_pct=0.10, slippage_pct=0.02,
        note="The default retail rate. Assume this unless you know otherwise."),
    "binance-spot-bnb": Market(
        "Binance spot, BNB discount", fee_pct=0.075, slippage_pct=0.02,
        note="25% off for paying fees in BNB."),
    "binance-futures": Market(
        "Binance USD-M futures (taker)", fee_pct=0.045, slippage_pct=0.02,
        note="Cheaper than spot, but funding is charged every 8h and is not "
             "modelled here. On a position held for days, funding can exceed fees."),
    "binance-maker": Market(
        "Binance spot (maker)", fee_pct=0.02, slippage_pct=0.05,
        note="Only valid if your entries are limit orders that actually rest. "
             "Slippage is raised because maker orders sometimes never fill, and "
             "the ones that do tend to fill when the market is going against you."),
    "bybit-spot": Market("Bybit spot (taker)", fee_pct=0.10, slippage_pct=0.02),
    "coinbase": Market(
        "Coinbase Advanced (taker)", fee_pct=0.60, slippage_pct=0.03,
        note="Retail tier is brutal. Almost nothing survives 1.2% per round trip."),
    "kraken": Market("Kraken (taker)", fee_pct=0.26, slippage_pct=0.03),
    "altcoin": Market(
        "Mid-cap altcoin", fee_pct=0.10, slippage_pct=0.15,
        note="Same fee, far worse slippage. Thin books punish market orders."),

    # --- forex / CFD: absolute spreads -------------------------------------
    "xauusd": Market("Gold vs USD", spread=0.25, slippage_pct=0.005,
                     note="Typical retail spread 20-30 cents. Widens hard around news."),
    "xauusd-tight": Market("Gold, ECN account", spread=0.12, slippage_pct=0.005),

    # --- deriv synthetics ---------------------------------------------------
    "v75": Market("Volatility 75 Index", spread=0.0, slippage_pct=0.01,
                  note="Check your own account: Deriv spreads vary by index and "
                       "by whether you are on the 1s or standard variant."),
    "boom1000": Market("Boom 1000", spread=0.0, slippage_pct=0.02,
                       note="Spikes mean real slippage is much worse than modelled."),

    # --- the control group --------------------------------------------------
    "free": Market("No costs (unrealistic)", note="Only for isolating the cost impact."),
}


def get(name: str) -> Market:
    key = name.lower()
    if key not in MARKETS:
        raise KeyError(f"Unknown market {name!r}. Available: {', '.join(sorted(MARKETS))}")
    return MARKETS[key]


def table() -> str:
    lines = [f"{'key':<20} {'round trip':>11}  market"]
    lines.append("-" * 70)
    for key in sorted(MARKETS):
        m = MARKETS[key]
        cost = f"{m.round_trip_pct:.3f}%" if m.fee_pct or m.slippage_pct else "-"
        if m.spread:
            cost += f" +{m.spread}"
        lines.append(f"{key:<20} {cost:>11}  {m.name}")
    return "\n".join(lines)

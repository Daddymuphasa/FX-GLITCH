"""Macro context - deliberately small, because the measurement said so.

READ THIS BEFORE ADDING ANYTHING TO THIS FILE
---------------------------------------------
This module is one factor long, and that is not an oversight. It is the size
the evidence supports.

tools/macro_check.py tested 16 macro conditions against BTC over 12 years, and
16 against ETH. Not one predicted BTC outperformance. The 30-year Treasury
yield - the specific trigger behind the 19 August 2026 rally, a real
3rd-percentile move - showed BELOW-baseline BTC returns across 129 comparable
historical drops at every horizon tested.

The obvious temptation is to add yield factors, DXY factors, liquidity factors,
because the stories are compelling and the data is free. Resist it. Every one
of those was tested and found to be noise. Adding them would give the model
confident-sounding inputs that carry no information, which is worse than having
no inputs at all: it manufactures conviction.

WHAT DID SURVIVE
----------------
Exactly one effect, and it replicated on both BTC and ETH:

    After a bottom-5% day in the S&P or Nasdaq, crypto UNDERPERFORMS its own
    baseline over the following 5 days, by more than one standard error.

    BTC:  S&P crash -> -1.17% edge   Nasdaq crash -> -1.23% edge
    ETH:  S&P crash -> -0.81% edge   Nasdaq crash -> -0.80% edge

That is risk-asset contagion, and it is a genuinely useful thing to know. Note
carefully what it is NOT: it is not a short signal, and the inverse is not a
long signal - equity RALLIES showed nothing on either asset. It is asymmetric.
Crypto catches the falling knife and does not catch the bounce.

So it belongs where an asymmetric downside effect belongs: in position SIZING,
as a reason to take less risk, never as a reason to enter. That is why the
factor below only ever produces a negative score.
"""

from __future__ import annotations

import os
from datetime import timedelta

from .data import Series, load_csv
from .signals import Kind, Signal


def load_macro_dir(path: str = "data/raw/macro") -> dict[str, Series]:
    """Load whatever macro series have been downloaded."""
    out: dict[str, Series] = {}
    if not os.path.isdir(path):
        return out
    for fn in sorted(os.listdir(path)):
        if fn.endswith(".csv"):
            try:
                out[fn[:-4].upper()] = load_csv(os.path.join(path, fn))
            except (ValueError, FileNotFoundError):
                continue
    return out


def risk_off_contagion(equity: Series, percentile: float = 0.05,
                       horizon_days: int = 5) -> list[Signal]:
    """A sharp equity drawdown day, which crypto historically follows down.

    `equity` should be the S&P (gspc) or Nasdaq (ndx) series.

    Deliberately one-sided: this never returns a positive score. Equity rallies
    were tested on both BTC and ETH and showed nothing, so treating a green S&P
    day as bullish for crypto would be inventing an effect that the data says
    is not there.

    Equity markets close; crypto does not. A Friday equity crash is knowable
    before Saturday's crypto bar, so a one-day availability lag is honest
    rather than conservative.
    """
    if len(equity) < 100:
        return []

    changes = []
    for i in range(1, len(equity)):
        prev = equity[i - 1].close
        if prev:
            changes.append((i, (equity[i].close - prev) / prev * 100))
    if not changes:
        return []

    ordered = sorted(c for _, c in changes)
    cut = ordered[int(len(ordered) * percentile)]

    out: list[Signal] = []
    for i, change in changes:
        if change > cut:
            continue
        # 0.0 at exactly the threshold, 1.0 at three times it. An earlier
        # version saturated at 2x the cut, which meant a -3% and a -12% equity
        # day scored identically - and on real data the 5% cut is around -1.5%,
        # so nearly every crash pinned to maximum. Gradation matters precisely
        # in the tail, which is the only place this factor speaks.
        severity = min(1.0, max(0.0, (abs(change) / abs(cut) - 1) / 2)) if cut else 0.5
        out.append(Signal(
            at=equity[i].time,
            available_at=equity[i].time + timedelta(days=1),
            kind=Kind.MACRO,
            name="risk_off_contagion",
            score=-0.5 * severity,
            # Capped low on purpose. The effect is real and replicated, but it
            # is roughly -1% over five days: worth trimming size for, nowhere
            # near worth taking a position on.
            confidence=min(0.55, 0.3 + severity * 0.25),
            horizon=timedelta(days=horizon_days),
            source="equity index",
            note=f"equity index {change:+.2f}% - bottom {percentile*100:.0f}% day, "
                 f"crypto historically underperforms for ~{horizon_days}d",
            meta={"equity_change_pct": change},
        ))
    return out


def context_line(macro: dict[str, Series], when=None) -> str:
    """A human-readable macro snapshot for the report.

    This is what macro is FOR in this repo: telling you what the world looked
    like on a given day, so a result has context. It is not a model input.
    """
    if not macro:
        return "(no macro data - run tools/fetch_macro.py)"

    bits = []
    labels = {"TYX": "30y", "TNX": "10y", "DXY": "DXY", "VIX": "VIX",
              "GSPC": "S&P", "GOLD": "gold", "NDX": "NDX"}
    for key, label in labels.items():
        series = macro.get(key)
        if not series:
            continue
        rows = [c for c in series if when is None or c.time <= when]
        if len(rows) < 2:
            continue
        last, prev = rows[-1], rows[-2]
        change = (last.close - prev.close) / prev.close * 100 if prev.close else 0.0
        bits.append(f"{label} {last.close:,.2f} ({change:+.1f}%)")
    return "  ".join(bits) if bits else "(no macro data)"


def all_macro_factors(macro: dict[str, Series]) -> list[Signal]:
    """Every macro factor the evidence currently supports. There is one."""
    out: list[Signal] = []
    equity = macro.get("GSPC") or macro.get("NDX")
    if equity:
        out += risk_off_contagion(equity)
    return out

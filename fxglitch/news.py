"""Storing catalysts as data instead of as a story.

The 20 August rally had a clean narrative: Treasury buybacks, then regulatory
optimism, amplified by a short squeeze and ETF inflows. That narrative is
almost certainly correct. It is also, on its own, worth nothing to a systematic
trader, for a reason worth being blunt about:

    You are reading an explanation constructed AFTER the move, selected BECAUSE
    the move happened.

Nobody writes "Treasury doubles buybacks, Bitcoin does nothing." That article
does not exist, so you never see the times the same catalyst produced no move.
Every narrative you encounter is drawn from the winners' bracket. Build a
system on post-hoc narratives and you have built a machine that explains the
past beautifully and predicts nothing.

The fix is not to ignore news - news genuinely moves markets. The fix is to log
catalysts PROSPECTIVELY, in a fixed schema, including the ones that went
nowhere, and then measure. That is what this file and tools/event_study.py are
for. Log fifty Treasury announcements, find that thirty-one were followed by a
positive three-day return, and you have something. Log one and you have an
anecdote with good punctuation.

Every event needs `available_at`: when a retail trader could actually have
known. See fxglitch/signals.py for why that field decides whether your backtest
is research or fiction.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timedelta, timezone

from .signals import Kind, Signal

# How strongly each catalyst category is assumed to matter, and for how long.
# These are PRIORS - starting guesses to be overwritten by measurement. Run
# tools/event_study.py once you have 30+ events of a category and replace them.
CATALYST_PRIORS: dict[str, tuple[Kind, float, int]] = {
    # name                      kind             score   horizon_days
    "treasury_buyback":        (Kind.MACRO,       0.7,   5),
    "fed_cut":                 (Kind.MACRO,       0.6,   5),
    "fed_hike":                (Kind.MACRO,      -0.6,   5),
    "cpi_hot":                 (Kind.MACRO,      -0.5,   3),
    "cpi_cool":                (Kind.MACRO,       0.5,   3),
    "yield_spike":             (Kind.MACRO,      -0.5,   3),
    "liquidity_injection":     (Kind.MACRO,       0.6,   5),

    "etf_approval":            (Kind.REGULATORY,  0.8,  10),
    "favourable_legislation":  (Kind.REGULATORY,  0.6,   7),
    "enforcement_action":      (Kind.REGULATORY, -0.6,   5),
    "exchange_ban":            (Kind.REGULATORY, -0.8,   7),

    "etf_inflow_large":        (Kind.FLOW,        0.6,   3),
    "etf_outflow_large":       (Kind.FLOW,       -0.6,   3),
    "exchange_outflow":        (Kind.FLOW,        0.4,   5),
    "stablecoin_mint":         (Kind.FLOW,        0.4,   5),

    "short_squeeze":           (Kind.POSITIONING, 0.5,   2),
    "long_liquidation":        (Kind.POSITIONING,-0.5,   2),
    "funding_extreme_positive":(Kind.POSITIONING,-0.4,   3),
    "funding_extreme_negative":(Kind.POSITIONING, 0.4,   3),

    "whale_accumulation":      (Kind.ONCHAIN,     0.5,   7),
    "whale_distribution":      (Kind.ONCHAIN,    -0.5,   7),
    "exchange_hack":           (Kind.ONCHAIN,    -0.7,   5),

    "social_euphoria":         (Kind.SENTIMENT,  -0.3,   3),   # contrarian
    "social_capitulation":     (Kind.SENTIMENT,   0.3,   3),   # contrarian
}


def event_to_signal(
    category: str,
    at: datetime,
    available_at: datetime | None = None,
    confidence: float = 0.5,
    score: float | None = None,
    note: str = "",
    source: str = "",
    horizon_days: int | None = None,
) -> Signal:
    """Build a Signal from a catalyst category, using the priors above."""
    if category not in CATALYST_PRIORS:
        raise KeyError(
            f"Unknown catalyst {category!r}. Known: {', '.join(sorted(CATALYST_PRIORS))}. "
            f"Add it to CATALYST_PRIORS rather than inventing one inline, so the "
            f"event study can group it."
        )
    kind, prior_score, prior_days = CATALYST_PRIORS[category]
    return Signal(
        at=at,
        available_at=available_at or at,
        kind=kind,
        name=category,
        score=prior_score if score is None else score,
        confidence=confidence,
        horizon=timedelta(days=horizon_days or prior_days),
        source=source,
        note=note,
    )


def _parse(raw: str) -> datetime:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised event timestamp: {raw!r}")


def load_events(path: str) -> list[Signal]:
    """Read a catalyst log (json or csv) into Signals.

    Required columns/keys: category, at
    Optional: available_at, confidence, score, note, source, horizon_days, asset

    If `available_at` is missing it defaults to `at` PLUS ONE DAY, not `at`.
    That pessimism is deliberate. Most catalyst logs are written from memory or
    from articles published after the fact, and assuming instant knowledge is
    the single easiest way to manufacture a fake edge.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"No event log at {path}")

    rows: list[dict]
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        rows = payload["events"] if isinstance(payload, dict) else payload
    else:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))

    out: list[Signal] = []
    for r in rows:
        if not r.get("category") or not r.get("at"):
            continue
        at = _parse(str(r["at"]))
        avail_raw = r.get("available_at")
        available_at = _parse(str(avail_raw)) if avail_raw else at + timedelta(days=1)
        out.append(event_to_signal(
            category=str(r["category"]).strip(),
            at=at,
            available_at=available_at,
            confidence=float(r.get("confidence") or 0.5),
            score=float(r["score"]) if r.get("score") not in (None, "") else None,
            note=str(r.get("note") or ""),
            source=str(r.get("source") or ""),
            horizon_days=int(r["horizon_days"]) if r.get("horizon_days") else None,
        ))
    return out

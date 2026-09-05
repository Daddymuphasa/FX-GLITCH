"""Safe parsing primitives for copying external futures signals.

The source (Telegram today, another feed tomorrow) is deliberately kept out
of this module.  A parsed signal is only an *instruction candidate*; callers
must still verify the Binance contract, freshness, price, balance, and risk
limits before execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


_SYMBOL = re.compile(r"\b([A-Za-z0-9]{2,30})\s*/?\s*USDT\b", re.I)
_PRICE = r"\$?([0-9]+(?:\.[0-9]+)?)"
_TP = re.compile(r"\b(?:tp|take\s*profit)\s*[:=-]?\s*" + _PRICE, re.I)
_SL = re.compile(r"\b(?:sl|stop\s*loss)\s*[:=-]?\s*" + _PRICE, re.I)
_LEV = re.compile(r"\b(?:leverage|lev)\s*[:=-]?\s*([0-9]+(?:\.[0-9]+)?)\s*x?\b", re.I)


def _decimal(match: re.Match[str] | None) -> Decimal | None:
    if match is None:
        return None
    try:
        value = Decimal(match.group(1))
    except InvalidOperation:
        return None
    return value if value > 0 else None


@dataclass(frozen=True, slots=True)
class ExternalSignal:
    """A normalized signal candidate extracted from one source message."""

    source_message: str
    symbol: str | None
    direction: SignalDirection | None
    entry: Decimal | None
    entry_is_market: bool
    take_profit: Decimal | None
    stop_loss: Decimal | None
    leverage: Decimal | None
    stop_adjustment: bool = False
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def can_open(self) -> bool:
        """Whether the candidate has the minimum fields for a new position.

        This does not mean the trade is approved.  Venue and risk validation
        happen after this parser and can still reject it.
        """
        return bool(self.symbol and self.direction and self.stop_loss and
                    (self.entry_is_market or self.entry is not None) and
                    not self.stop_adjustment and not self.warnings)


def parse_signal(message: str) -> ExternalSignal:
    """Parse the common Bitunix signal format without guessing missing data."""
    text = message.strip()
    symbol_match = _SYMBOL.search(text)
    symbol = f"{symbol_match.group(1).upper()}USDT" if symbol_match else None

    lower = text.lower()
    has_long = bool(re.search(r"\b(?:long|buy)\b", lower))
    has_short = bool(re.search(r"\bshort\b", lower))
    direction = None
    warnings: list[str] = []
    if has_long and has_short:
        warnings.append("signal contains both long and short directions")
    elif has_long:
        direction = SignalDirection.LONG
    elif has_short:
        direction = SignalDirection.SHORT
    else:
        warnings.append("direction is missing")

    market_entry = bool(re.search(r"\b(?:entry\s*)?cmp\b|current\s+market", lower))
    entry_match = re.search(r"\bentry\s*[:=-]?\s*" + _PRICE, text, re.I)
    entry = _decimal(entry_match)
    if not market_entry and entry is None:
        warnings.append("entry is missing or not explicitly CMP")

    stop_loss = _decimal(_SL.search(text))
    if stop_loss is None:
        warnings.append("stop-loss is missing")

    take_profit = _decimal(_TP.search(text))
    lev = _decimal(_LEV.search(text))
    stop_adjustment = bool(re.search(r"\badjust\s+(?:your\s+)?sl\b", lower))
    notes = tuple(word for word in ("scalping", "whale", "holding tight")
                  if word in lower)

    if stop_adjustment:
        warnings.append("message requests a stop adjustment, not a new entry")
    if symbol is None:
        warnings.append("USDT symbol is missing")

    return ExternalSignal(
        source_message=message,
        symbol=symbol,
        direction=direction,
        entry=entry,
        entry_is_market=market_entry,
        take_profit=take_profit,
        stop_loss=stop_loss,
        leverage=lev,
        stop_adjustment=stop_adjustment,
        notes=notes,
        warnings=tuple(dict.fromkeys(warnings)),
    )


@dataclass(slots=True)
class SignalDeduplicator:
    """Prevent a Telegram replay from producing a second entry."""

    seen_ids: set[str] = field(default_factory=set)

    def first_time(self, message_id: str) -> bool:
        if message_id in self.seen_ids:
            return False
        self.seen_ids.add(message_id)
        return True

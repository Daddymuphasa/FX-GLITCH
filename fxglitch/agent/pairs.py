"""Map a Bitunix (or pasted) USDT symbol onto a Binance USDⓈ-M perpetual."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..venues.base import Instrument


@dataclass
class PairMap:
    source: str
    bitunix: str
    binance: str
    listed: bool
    tradeable: bool
    max_leverage: int | None
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_usdt(raw: str | None) -> str | None:
    if not raw:
        return None
    symbol = raw.upper().replace(" ", "").replace("/", "").replace("-", "")
    if symbol.endswith("PERP"):
        symbol = symbol[:-4]
    if not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"
    if not symbol.endswith("USDT") or len(symbol) < 7:
        return None
    return symbol


def map_to_binance(raw: str | None, instruments: dict[str, Instrument] | None = None) -> PairMap:
    """Bitunix USDT-M names are usually the same ticker as Binance USDT-M.

    We do not guess a different base asset. If Binance does not list that
    perpetual, the dashboard must say so instead of inventing a cousin pair.
    """
    symbol = normalize_usdt(raw)
    if symbol is None:
        return PairMap("bitunix", "", "", False, False, None, "no USDT symbol in the signal")
    info = (instruments or {}).get(symbol)
    if instruments is None:
        return PairMap("bitunix", symbol, symbol, False, False, None,
                       "Binance listing not checked yet")
    if info is None:
        return PairMap("bitunix", symbol, symbol, False, False, None,
                       f"{symbol} is not a Binance USDⓈ-M perpetual — will not send")
    if not info.tradeable:
        return PairMap("bitunix", symbol, symbol, True, False, info.max_leverage,
                       f"{symbol} is listed but not tradeable right now")
    return PairMap("bitunix", symbol, symbol, True, True, info.max_leverage,
                   f"{symbol} is a tradeable Binance USDⓈ-M perpetual")

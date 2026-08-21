"""Loading and shaping price data.

Everything downstream speaks in `Candle` objects and `Series` (a list of
candles), so it does not matter whether the data came from Deriv, MT5 or a
CSV somebody emailed you.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class Candle:
    """One bar of price action."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_bull(self) -> bool:
        return self.close >= self.open


Series = list[Candle]

# Column names we accept, lowercased, mapped to the field we want.
_ALIASES = {
    "time": "time", "date": "time", "datetime": "time", "timestamp": "time",
    "open": "open", "o": "open",
    "high": "high", "h": "high",
    "low": "low", "l": "low",
    "close": "close", "c": "close", "price": "close",
    "volume": "volume", "vol": "volume", "tickvol": "volume", "tick_volume": "volume",
}

_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y-%m-%d",
    "%Y.%m.%d",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M",
)


def _parse_time(raw: str) -> datetime:
    raw = raw.strip()
    # Plain unix timestamp?
    if raw.isdigit():
        ts = int(raw)
        if ts > 1e11:  # milliseconds
            ts //= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp: {raw!r}")


def load_csv(path: str) -> Series:
    """Read an OHLC csv. Tolerant about column naming and separators."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No data file at {path}. Put a csv in data/raw/ or run a downloader "
            f"in tools/."
        )

    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)

        if not reader.fieldnames:
            raise ValueError(f"{path} has no header row.")

        # Map the file's headers onto our field names.
        mapping: dict[str, str] = {}
        for col in reader.fieldnames:
            key = col.strip().lower().lstrip("<").rstrip(">").replace(" ", "_")
            if key in _ALIASES:
                mapping[col] = _ALIASES[key]

        missing = {"time", "open", "high", "low", "close"} - set(mapping.values())
        if missing:
            raise ValueError(
                f"{path} is missing column(s): {', '.join(sorted(missing))}. "
                f"Found headers: {reader.fieldnames}"
            )

        candles: Series = []
        for row in reader:
            vals = {field: row[col] for col, field in mapping.items()}
            if not vals["close"]:
                continue  # skip blank rows
            candles.append(
                Candle(
                    time=_parse_time(vals["time"]),
                    open=float(vals["open"]),
                    high=float(vals["high"]),
                    low=float(vals["low"]),
                    close=float(vals["close"]),
                    volume=float(vals.get("volume") or 0.0),
                )
            )

    candles.sort(key=lambda c: c.time)
    return candles


def slice_dates(candles: Series, start: str | None = None, end: str | None = None) -> Series:
    """Trim a series to a date window, e.g. slice_dates(c, '2024-01-01', '2024-06-30')."""
    out = candles
    if start:
        s = _parse_time(start)
        out = [c for c in out if c.time >= s]
    if end:
        e = _parse_time(end)
        out = [c for c in out if c.time <= e]
    return out


def closes(candles: Series) -> list[float]:
    return [c.close for c in candles]


def describe(candles: Series) -> str:
    """One-line summary, handy for sanity-checking that data loaded correctly."""
    if not candles:
        return "empty series"
    return (
        f"{len(candles)} bars | {candles[0].time:%Y-%m-%d %H:%M} -> "
        f"{candles[-1].time:%Y-%m-%d %H:%M} | "
        f"price {min(c.low for c in candles):.5g}-{max(c.high for c in candles):.5g}"
    )
